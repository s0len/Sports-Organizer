from __future__ import annotations

from pathlib import Path

import pytest

from playbook.utils import load_yaml_file
from playbook.validation import validate_config_data


def test_sample_configuration_passes_validation() -> None:
    project_root = Path(__file__).resolve().parents[1]
    sample_path = project_root / "config" / "playbook.sample.yaml"
    if not sample_path.exists():
        pytest.skip("Sample configuration not present in repository checkout")
    data = load_yaml_file(sample_path)
    report = validate_config_data(data)
    assert report.errors == []


def test_validation_flags_invalid_flush_time_and_metadata_url() -> None:
    config = {
        "settings": {
            "notifications": {
                "flush_time": "25:61",
            }
        },
        "sports": [
            {
                "id": "test",
                "metadata": {
                    "url": "",
                },
            }
        ],
    }

    report = validate_config_data(config)
    codes = {issue.code for issue in report.errors}
    assert "flush-time" in codes
    assert "metadata-url" in codes


def test_validation_rejects_invalid_watcher_block() -> None:
    config = {
        "settings": {
            "file_watcher": {
                "debounce_seconds": -1,
                "reconcile_interval": -5,
                "paths": 123,
            }
        },
        "sports": [
            {"id": "demo", "metadata": {"url": "https://example.com/demo.yaml"}},
        ],
    }

    report = validate_config_data(config)
    assert any(issue.path == "settings.file_watcher.paths" for issue in report.errors)

