from __future__ import annotations

import copy
import datetime as dt
import logging
import re
import secrets
import shlex
import shutil
import subprocess
from typing import Any, Dict, Optional

from .config import KometaTriggerSettings

try:  # pragma: no cover - exercised in production environments
    from kubernetes import client, config
    from kubernetes.client import ApiException
except Exception:  # pragma: no cover - optional dependency guard
    client = None  # type: ignore[assignment]
    config = None  # type: ignore[assignment]

    class ApiException(Exception):  # type: ignore[no-redef]
        """Fallback so callers can catch a consistent type."""

LOGGER = logging.getLogger(__name__)

_DEFAULT_LABEL_KEY = "trigger"
_DEFAULT_LABEL_VALUE = "playbook"


class _BaseKometaTrigger:
    @property
    def enabled(self) -> bool:  # pragma: no cover - overridden
        return False

    def trigger(
        self,
        extra_labels: Optional[Dict[str, str]] = None,
        extra_annotations: Optional[Dict[str, str]] = None,
    ) -> bool:  # pragma: no cover - overridden
        return False


class _NullKometaTrigger(_BaseKometaTrigger):
    pass


def build_kometa_trigger(settings: KometaTriggerSettings) -> _BaseKometaTrigger:
    mode = (settings.mode or "kubernetes").lower()
    if mode == "docker":
        return KometaDockerTrigger(settings)
    if mode == "kubernetes":
        return KometaCronTrigger(settings)
    LOGGER.warning("Unknown kometa_trigger.mode '%s'; disabling trigger", settings.mode)
    return _NullKometaTrigger()


class KometaCronTrigger(_BaseKometaTrigger):
    """Creates ad-hoc Jobs from the Kometa CronJob when new items are ingested."""

    def __init__(self, settings: KometaTriggerSettings) -> None:
        self._settings = settings
        self._api: Optional["client.BatchV1Api"] = None
        self._api_client: Optional["client.ApiClient"] = None

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.enabled and (self._settings.mode or "kubernetes").lower() == "kubernetes"
        )

    def trigger(
        self,
        extra_labels: Optional[Dict[str, str]] = None,
        extra_annotations: Optional[Dict[str, str]] = None,
    ) -> bool:
        if not self.enabled:
            return False
        if client is None or config is None:
            LOGGER.error("Kubernetes client library is not available; skipping Kometa trigger")
            return False

        try:
            api = self._ensure_client()
        except Exception as exc:  # pragma: no cover - depends on runtime env
            LOGGER.error("Unable to initialize Kubernetes client: %s", exc)
            return False

        namespace = self._settings.namespace or "media"
        cronjob_name = self._settings.cronjob_name or "kometa-sport"
        job_name = self._build_job_name()

        try:
            cronjob = api.read_namespaced_cron_job(name=cronjob_name, namespace=namespace)
        except ApiException as exc:
            LOGGER.error("Failed to load CronJob %s/%s: %s", namespace, cronjob_name, exc)
            return False

        try:
            job_body = self._build_job_body(cronjob, job_name, extra_labels, extra_annotations)
        except Exception as exc:
            LOGGER.error(
                "Failed to build ad-hoc Kometa Job from CronJob %s/%s: %s",
                namespace,
                cronjob_name,
                exc,
            )
            return False

        try:
            api.create_namespaced_job(namespace=namespace, body=job_body)
        except ApiException as exc:
            if getattr(exc, "status", None) == 409:
                LOGGER.info("Kometa Job already exists; skipping duplicate trigger (%s/%s)", namespace, job_name)
            else:
                LOGGER.error("Failed to create Kometa Job %s/%s: %s", namespace, job_name, exc)
            return False

        LOGGER.info(
            "Triggered Kometa CronJob\n  Namespace: %s\n  CronJob: %s\n  Job: %s",
            namespace,
            cronjob_name,
            job_name,
        )
        return True

    def _ensure_client(self) -> "client.BatchV1Api":
        if self._api is not None:
            return self._api

        config.load_incluster_config()
        api_client = client.ApiClient()
        self._api_client = api_client
        self._api = client.BatchV1Api(api_client)
        return self._api

    def _build_job_name(self) -> str:
        base = self._settings.job_name_prefix or f"{self._settings.cronjob_name or 'kometa-sport'}-manual"
        normalized = re.sub(r"[^a-z0-9-]", "-", base.lower()).strip("-") or "kometa-sport"
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        random_suffix = secrets.token_hex(2)
        name = f"{normalized}-{timestamp}-{random_suffix}"
        if len(name) > 63:
            name = name[:63].rstrip("-")
        if not name:
            name = f"kometa-sport-{random_suffix}"
        return name

    def _build_job_body(
        self,
        cronjob: Any,
        job_name: str,
        extra_labels: Optional[Dict[str, str]],
        extra_annotations: Optional[Dict[str, str]],
    ) -> Dict[str, Any]:
        if not self._api_client:
            raise RuntimeError("API client not initialized")

        cronjob_dict = self._api_client.sanitize_for_serialization(cronjob)
        job_template = (cronjob_dict.get("spec") or {}).get("jobTemplate")
        if not job_template:
            raise ValueError("CronJob spec is missing jobTemplate")

        metadata = copy.deepcopy(job_template.get("metadata") or {})
        spec = copy.deepcopy(job_template.get("spec") or {})
        template = spec.get("template")
        if not template:
            raise ValueError("CronJob jobTemplate spec is missing template")
        template_metadata = copy.deepcopy(template.get("metadata") or {})
        template_spec = template.get("spec")
        if not template_spec:
            raise ValueError("CronJob jobTemplate template is missing spec")

        self._strip_runtime_fields(metadata)
        self._strip_runtime_fields(template_metadata)

        metadata["name"] = job_name
        metadata["labels"] = self._build_labels(metadata.get("labels"), extra_labels)
        metadata["annotations"] = self._build_annotations(metadata.get("annotations"), extra_annotations, include_timestamp=True)

        template_metadata["labels"] = self._build_labels(template_metadata.get("labels"), extra_labels)
        template_metadata["annotations"] = self._build_annotations(template_metadata.get("annotations"), extra_annotations, include_timestamp=False)
        template["metadata"] = template_metadata
        spec["template"] = template

        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": metadata,
            "spec": spec,
        }

    @staticmethod
    def _strip_runtime_fields(metadata: Dict[str, Any]) -> None:
        for key in ("creationTimestamp", "resourceVersion", "selfLink", "uid", "generation", "managedFields"):
            metadata.pop(key, None)

    @staticmethod
    def _build_labels(
        base_labels: Optional[Dict[str, Any]],
        extras: Optional[Dict[str, str]],
    ) -> Dict[str, str]:
        labels = {str(key): str(value) for key, value in (base_labels or {}).items() if value is not None}
        labels[_DEFAULT_LABEL_KEY] = _DEFAULT_LABEL_VALUE
        if extras:
            for key, value in extras.items():
                if key and value is not None:
                    labels[str(key)] = str(value)
        return labels

    @staticmethod
    def _build_annotations(
        base_annotations: Optional[Dict[str, Any]],
        extras: Optional[Dict[str, str]],
        *,
        include_timestamp: bool,
    ) -> Dict[str, str]:
        annotations = {
            str(key): str(value)
            for key, value in (base_annotations or {}).items()
            if value is not None
        }
        annotations.setdefault("playbook/triggered-by", "playbook")
        if include_timestamp:
            annotations["playbook/triggered-at"] = (
                dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            )
        if extras:
            for key, value in extras.items():
                if key and value is not None:
                    annotations[str(key)] = str(value)
        return annotations


class KometaDockerTrigger(_BaseKometaTrigger):
    """Runs Kometa via `docker run` after new items are ingested."""

    def __init__(self, settings: KometaTriggerSettings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.enabled and (self._settings.mode or "").lower() == "docker")

    def trigger(
        self,
        extra_labels: Optional[Dict[str, str]] = None,  # noqa: ARG002 - docker implementation ignores labels
        extra_annotations: Optional[Dict[str, str]] = None,  # noqa: ARG002
    ) -> bool:
        if not self.enabled:
            return False

        binary = self._settings.docker_binary or "docker"
        if shutil.which(binary) is None:
            LOGGER.error("Docker binary '%s' not found on PATH; skipping Kometa trigger", binary)
            return False

        use_exec = bool(self._settings.docker_container_name)
        if not use_exec and not self._settings.docker_config_path:
            LOGGER.error(
                "Kometa docker trigger requires 'kometa_trigger.docker.config_path' when launching a new container"
            )
            return False

        if use_exec:
            command = self._build_exec_command(binary)
        else:
            command = self._build_run_command(binary, self._settings.docker_config_path or "")

        LOGGER.debug("Running Kometa docker command: %s", " ".join(shlex.quote(part) for part in command))

        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            LOGGER.error("Failed to start Kometa docker trigger: %s", exc)
            return False

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            LOGGER.error(
                "Kometa docker trigger exited with code %s%s",
                result.returncode,
                f": {stderr}" if stderr else "",
            )
            return False

        stdout = (result.stdout or "").strip()
        if stdout:
            LOGGER.debug("Kometa docker output:\n%s", stdout)

        if use_exec:
            LOGGER.info(
                "Triggered Kometa via docker exec\n  Container: %s\n  Libraries: %s",
                self._settings.docker_container_name,
                self._settings.docker_libraries or "(all configured libraries)",
            )
        else:
            LOGGER.info(
                "Triggered Kometa via docker run\n  Image: %s\n  Libraries: %s",
                self._settings.docker_image or "kometateam/kometa",
                self._settings.docker_libraries or "(all configured libraries)",
            )
        return True

    def _build_run_command(self, binary: str, config_path: str) -> list[str]:
        command: list[str] = [binary, "run"]
        if self._settings.docker_remove_container:
            command.append("--rm")
        if self._settings.docker_interactive:
            command.extend(["-it"])

        container_path = self._settings.docker_config_container_path or "/config"
        volume_mode = self._settings.docker_volume_mode or "rw"
        volume = f"{config_path}:{container_path}"
        if volume_mode:
            volume = f"{volume}:{volume_mode}"
        command.extend(["-v", volume])

        for key, value in (self._settings.docker_env or {}).items():
            command.extend(["-e", f"{key}={value}"])

        image = self._settings.docker_image or "kometateam/kometa"
        command.append(image)
        command.extend(self._kometa_cli_args())
        return command

    def _build_exec_command(self, binary: str) -> list[str]:
        container = self._settings.docker_container_name
        if not container:
            raise ValueError("docker_container_name is required for docker exec mode")

        command: list[str] = [binary, "exec"]
        if self._settings.docker_interactive:
            command.extend(["-it"])

        for key, value in (self._settings.docker_env or {}).items():
            command.extend(["-e", f"{key}={value}"])

        command.append(container)
        python_bin = self._settings.docker_exec_python or "python3"
        script_path = self._settings.docker_exec_script or "/app/kometa/kometa.py"
        command.extend([python_bin, script_path])
        command.extend(self._kometa_cli_args())
        return command

    def _kometa_cli_args(self) -> list[str]:
        args: list[str] = []
        if self._settings.docker_libraries:
            args.extend(["--run-libraries", self._settings.docker_libraries])
        args.extend(self._settings.docker_extra_args or [])
        return args

