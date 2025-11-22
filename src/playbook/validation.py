from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from jsonschema import Draft7Validator

from .pattern_templates import load_builtin_pattern_sets


@dataclass(slots=True)
class ValidationIssue:
    """Represents a single validation problem."""

    severity: str
    path: str
    message: str
    code: str


@dataclass(slots=True)
class ValidationReport:
    """Aggregates validation warnings and errors."""

    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


_TIME_PATTERN = r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$"
_LINK_MODES = ["hardlink", "copy", "symlink"]

CONFIG_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "settings": {
            "type": "object",
            "properties": {
                "source_dir": {"type": "string"},
                "destination_dir": {"type": "string"},
                "cache_dir": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "skip_existing": {"type": "boolean"},
                "poll_interval": {"type": "integer"},
                "link_mode": {"type": "string", "enum": _LINK_MODES},
                "discord_webhook_url": {"type": ["string", "null"]},
                "destination": {
                    "type": "object",
                    "properties": {
                        "root_template": {"type": "string"},
                        "season_dir_template": {"type": "string"},
                        "episode_template": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "notifications": {
                    "type": "object",
                    "properties": {
                        "batch_daily": {"type": "boolean"},
                        "flush_time": {"type": "string", "pattern": _TIME_PATTERN},
                    },
                    "additionalProperties": True,
                },
                "file_watcher": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "paths": {
                            "oneOf": [
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "string"},
                            ]
                        },
                        "include": {
                            "oneOf": [
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "string"},
                            ]
                        },
                        "ignore": {
                            "oneOf": [
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "string"},
                            ]
                        },
                        "debounce_seconds": {"type": ["number", "integer"], "minimum": 0},
                        "reconcile_interval": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": True,
                },
                "kometa_trigger": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "mode": {"type": "string", "enum": ["kubernetes", "docker"]},
                        "namespace": {"type": "string"},
                        "cronjob_name": {"type": "string"},
                        "job_name_prefix": {"type": "string"},
                        "docker": {
                            "type": "object",
                            "properties": {
                                "binary": {"type": "string"},
                                "image": {"type": "string"},
                                "config_path": {"type": "string"},
                                "container_path": {"type": "string"},
                                "volume_mode": {"type": "string"},
                                "libraries": {"type": "string"},
                                "container_name": {"type": "string"},
                                "exec_python": {"type": "string"},
                                "exec_script": {"type": "string"},
                                "extra_args": {
                                    "oneOf": [
                                        {"type": "array", "items": {"type": "string"}},
                                        {"type": "string"},
                                    ]
                                },
                                "env": {
                                    "type": "object",
                                    "additionalProperties": {"type": "string"},
                                },
                                "remove_container": {"type": "boolean"},
                                "interactive": {"type": "boolean"},
                            },
                            "additionalProperties": True,
                        },
                    },
                    "additionalProperties": True,
                },
            },
            "additionalProperties": True,
        },
        "pattern_sets": {
            "type": ["object", "null"],
            "patternProperties": {
                "^[A-Za-z0-9_.-]+$": {
                    "oneOf": [
                        {"type": "array", "items": {"$ref": "#/definitions/pattern_definition"}},
                        {"type": "null"},
                    ]
                }
            },
            "additionalProperties": True,
        },
        "sports": {
            "type": "array",
            "items": {"$ref": "#/definitions/sport"},
        },
    },
    "required": ["sports"],
    "additionalProperties": True,
    "definitions": {
        "metadata": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "minLength": 1},
                "show_key": {"type": ["string", "null"]},
                "ttl_hours": {"type": "integer", "minimum": 1},
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "season_overrides": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                },
            },
            "required": ["url"],
            "additionalProperties": True,
        },
        "season_selector": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["round", "key", "title", "sequential"],
                },
                "group": {"type": ["string", "null"]},
                "offset": {"type": "integer"},
                "mapping": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
            },
            "additionalProperties": True,
        },
        "episode_selector": {
            "type": "object",
            "properties": {
                "group": {"type": "string"},
                "allow_fallback_to_title": {"type": "boolean"},
            },
            "additionalProperties": True,
        },
        "pattern_definition": {
            "type": "object",
            "properties": {
                "regex": {"type": "string", "minLength": 1},
                "description": {"type": ["string", "null"]},
                "season_selector": {"$ref": "#/definitions/season_selector"},
                "episode_selector": {"$ref": "#/definitions/episode_selector"},
                "session_aliases": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "metadata_filters": {
                    "type": "object",
                    "additionalProperties": True,
                },
                "filename_template": {"type": ["string", "null"]},
                "season_dir_template": {"type": ["string", "null"]},
                "destination_root_template": {"type": ["string", "null"]},
                "priority": {"type": "integer"},
            },
            "required": ["regex"],
            "additionalProperties": True,
        },
        "sport": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "name": {"type": "string"},
                "enabled": {"type": "boolean"},
                "metadata": {"$ref": "#/definitions/metadata"},
                "pattern_sets": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "file_patterns": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/pattern_definition"},
                },
                "source_globs": {"type": "array", "items": {"type": "string"}},
                "source_extensions": {"type": "array", "items": {"type": "string"}},
                "link_mode": {"type": "string", "enum": _LINK_MODES},
                "allow_unmatched": {"type": "boolean"},
                "destination": {
                    "type": "object",
                    "properties": {
                        "root_template": {"type": "string"},
                        "season_dir_template": {"type": "string"},
                        "episode_template": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "variants": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "id_suffix": {"type": "string"},
                            "year": {"type": ["integer", "string"]},
                            "name": {"type": "string"},
                            "metadata": {"$ref": "#/definitions/metadata"},
                        },
                        "additionalProperties": True,
                    },
                },
            },
            "required": ["id"],
            "additionalProperties": True,
        },
    },
}


def _format_jsonschema_path(path: Sequence[Any]) -> str:
    if not path:
        return "<root>"
    tokens: List[str] = []
    for part in path:
        if isinstance(part, int):
            if tokens:
                tokens[-1] = f"{tokens[-1]}[{part}]"
            else:
                tokens.append(f"[{part}]")
        else:
            tokens.append(str(part))
    return ".".join(tokens) if tokens else "<root>"


def _parse_time(value: str) -> Optional[str]:
    try:
        parts = [int(part) for part in value.split(":")]
    except ValueError:
        return "components must be integers"
    if len(parts) not in {2, 3}:
        return "expected HH:MM or HH:MM:SS"
    hour, minute = parts[0], parts[1]
    second = parts[2] if len(parts) == 3 else 0
    try:
        dt.time(hour=hour, minute=minute, second=second)
    except ValueError as exc:
        return str(exc)
    return None


def _collect_pattern_set_names(data: Dict[str, Any]) -> Iterable[str]:
    builtin = set(load_builtin_pattern_sets().keys())
    user_sets = data.get("pattern_sets") or {}
    if isinstance(user_sets, dict):
        return builtin | set(user_sets.keys())
    return builtin


def _validate_metadata_block(metadata: Dict[str, Any], path: str, report: ValidationReport) -> None:
    url_value = metadata.get("url")
    if isinstance(url_value, str) and not url_value.strip():
        report.errors.append(
            ValidationIssue(
                severity="error",
                path=path + ".url",
                message="Metadata URL must not be blank",
                code="metadata-url",
            )
        )


def validate_config_data(data: Dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    validator = Draft7Validator(CONFIG_SCHEMA)

    for error in sorted(validator.iter_errors(data), key=lambda exc: exc.path):
        report.errors.append(
            ValidationIssue(
                severity="error",
                path=_format_jsonschema_path(error.absolute_path),
                message=error.message,
                code="schema",
            )
        )

    _validate_semantics(data, report)
    return report


def _validate_semantics(data: Dict[str, Any], report: ValidationReport) -> None:
    settings = data.get("settings") or {}
    notifications = settings.get("notifications") or {}
    flush_time = notifications.get("flush_time")
    if isinstance(flush_time, str):
        problem = _parse_time(flush_time)
        if problem:
            report.errors.append(
                ValidationIssue(
                    severity="error",
                    path="settings.notifications.flush_time",
                    message=f"Invalid time '{flush_time}': {problem}",
                    code="flush-time",
                )
            )

    sports = data.get("sports") or []
    seen_ids: Dict[str, int] = {}
    for index, sport in enumerate(sports):
        if not isinstance(sport, dict):
            continue
        sport_id = sport.get("id")
        if isinstance(sport_id, str):
            if sport_id in seen_ids:
                report.errors.append(
                    ValidationIssue(
                        severity="error",
                        path=f"sports[{index}].id",
                        message=f"Duplicate sport id '{sport_id}' also defined at index {seen_ids[sport_id]}",
                        code="duplicate-id",
                    )
                )
            else:
                seen_ids[sport_id] = index
        metadata = sport.get("metadata")
        variants = sport.get("variants") or []

        if isinstance(metadata, dict):
            _validate_metadata_block(metadata, f"sports[{index}].metadata", report)
        elif metadata is None and not variants:
            report.errors.append(
                ValidationIssue(
                    severity="error",
                    path=f"sports[{index}].metadata",
                    message="Sport must define metadata or variants with metadata",
                    code="metadata-missing",
                )
            )
        elif metadata is not None and not isinstance(metadata, dict):
            report.errors.append(
                ValidationIssue(
                    severity="error",
                    path=f"sports[{index}].metadata",
                    message="Metadata must be a mapping when provided",
                    code="metadata-structure",
                )
            )

        if variants:
            for variant_index, variant in enumerate(variants):
                if not isinstance(variant, dict):
                    report.errors.append(
                        ValidationIssue(
                            severity="error",
                            path=f"sports[{index}].variants[{variant_index}]",
                            message="Variant entries must be mappings",
                            code="variant-structure",
                        )
                    )
                    continue
                variant_metadata = variant.get("metadata")
                if not isinstance(variant_metadata, dict):
                    report.errors.append(
                        ValidationIssue(
                            severity="error",
                            path=f"sports[{index}].variants[{variant_index}].metadata",
                            message="Variant must provide a metadata block",
                            code="metadata-missing",
                        )
                    )
                    continue
                _validate_metadata_block(
                    variant_metadata,
                    f"sports[{index}].variants[{variant_index}].metadata",
                    report,
                )

    known_sets = _collect_pattern_set_names(data)
    for index, sport in enumerate(sports):
        if not isinstance(sport, dict):
            continue
        requested_sets = sport.get("pattern_sets") or []
        for name in requested_sets:
            if name not in known_sets:
                report.errors.append(
                    ValidationIssue(
                        severity="error",
                        path=f"sports[{index}].pattern_sets",
                        message=f"Unknown pattern set '{name}'",
                        code="pattern-set",
                    )
                )


__all__ = ["ValidationIssue", "ValidationReport", "validate_config_data", "CONFIG_SCHEMA"]

