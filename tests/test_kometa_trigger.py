from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("kubernetes")

from playbook.config import KometaTriggerSettings
from playbook.kometa_trigger import (
    KometaCronTrigger,
    KometaDockerTrigger,
    build_kometa_trigger,
    client,
    config,
)  # noqa: E402


def test_trigger_returns_false_when_disabled() -> None:
    trigger = KometaCronTrigger(KometaTriggerSettings(enabled=False))

    assert trigger.trigger() is False


def test_trigger_creates_job_from_cronjob(monkeypatch) -> None:
    settings = KometaTriggerSettings(
        enabled=True,
        namespace="media",
        cronjob_name="kometa-sport",
        job_name_prefix="manual",
    )
    trigger = KometaCronTrigger(settings)
    monkeypatch.setattr(trigger, "_build_job_name", lambda: "manual-1234")

    cronjob_dict = {
        "spec": {
            "jobTemplate": {
                "metadata": {"labels": {"app": "kometa"}},
                "spec": {
                    "template": {
                        "metadata": {"labels": {"app": "kometa"}},
                        "spec": {"containers": [{"name": "kometa"}], "restartPolicy": "Never"},
                    }
                },
            }
        }
    }

    mock_batch_api = MagicMock()
    mock_batch_api.read_namespaced_cron_job.return_value = cronjob_dict

    class DummyApiClient:
        def sanitize_for_serialization(self, obj):
            return obj

    monkeypatch.setattr(config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(client, "ApiClient", lambda: DummyApiClient())
    monkeypatch.setattr(client, "BatchV1Api", lambda api_client=None: mock_batch_api)

    result = trigger.trigger(extra_labels={"sport-id": "f1"})

    assert result is True
    mock_batch_api.read_namespaced_cron_job.assert_called_once_with(name="kometa-sport", namespace="media")
    assert mock_batch_api.create_namespaced_job.call_count == 1
    _, kwargs = mock_batch_api.create_namespaced_job.call_args
    assert kwargs["namespace"] == "media"
    job_body = kwargs["body"]
    assert job_body["metadata"]["name"] == "manual-1234"
    assert job_body["metadata"]["labels"]["trigger"] == "playbook"
    assert job_body["metadata"]["labels"]["sport-id"] == "f1"
    assert job_body["spec"]["template"]["metadata"]["labels"]["trigger"] == "playbook"


def test_build_kometa_trigger_selects_docker() -> None:
    settings = KometaTriggerSettings(
        enabled=True,
        mode="docker",
        docker_config_path="/srv/kometa",
        docker_libraries="Sport",
    )

    trigger = build_kometa_trigger(settings)

    assert isinstance(trigger, KometaDockerTrigger)


def test_docker_trigger_runs_command(monkeypatch) -> None:
    settings = KometaTriggerSettings(
        enabled=True,
        mode="docker",
        docker_binary="docker",
        docker_image="kometa:latest",
        docker_config_path="/srv/kometa/config",
        docker_libraries="Movies - 4K|TV Shows - 4K",
        docker_extra_args=["--config", "/config/config.yml"],
    )

    trigger = KometaDockerTrigger(settings)

    recorded = {}

    monkeypatch.setattr("playbook.kometa_trigger.shutil.which", lambda _: "/usr/bin/docker")

    def fake_run(cmd, **kwargs):
        recorded["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("playbook.kometa_trigger.subprocess.run", fake_run)

    assert trigger.trigger() is True
    assert recorded["cmd"][:3] == ["docker", "run", "--rm"]
    assert "--run-libraries" in recorded["cmd"]


def test_docker_trigger_execs_into_container(monkeypatch) -> None:
    settings = KometaTriggerSettings(
        enabled=True,
        mode="docker",
        docker_binary="docker",
        docker_container_name="kometa",
        docker_libraries="Sports",
        docker_exec_python="python3",
        docker_exec_script="/app/kometa/kometa.py",
    )
    trigger = KometaDockerTrigger(settings)

    recorded = {}
    monkeypatch.setattr("playbook.kometa_trigger.shutil.which", lambda _: "/usr/bin/docker")

    def fake_run(cmd, **kwargs):
        recorded["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("playbook.kometa_trigger.subprocess.run", fake_run)

    assert trigger.trigger() is True
    assert recorded["cmd"][:3] == ["docker", "exec", "kometa"]
    assert recorded["cmd"][3:5] == ["python3", "/app/kometa/kometa.py"]

