from __future__ import annotations

import textwrap

from playbook.config import AppConfig, load_config


def write_yaml(path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_load_config_expands_variants_and_merges_patterns(tmp_path) -> None:
    config_path = tmp_path / "playbook.yaml"
    write_yaml(
        config_path,
        f"""
        settings:
          source_dir: "{tmp_path / 'source'}"
          destination_dir: "{tmp_path / 'dest'}"
          cache_dir: "{tmp_path / 'cache'}"

        pattern_sets:
          shared:
            - regex: '(?P<round>\\d+)[._-](?P<session>[A-Za-z0-9]+)'
              priority: 50

        sports:
          - id: formula1
            name: Formula 1
            metadata:
              url: https://example.com/default.yaml
            pattern_sets:
              - shared
            file_patterns:
              - regex: 'custom'
                priority: 10
            variants:
              - year: 2024
                metadata:
                  ttl_hours: 1
              - id_suffix: pro
                name: Formula 1 Pro
                metadata:
                  url: https://example.com/pro.yaml
        """,
    )

    config: AppConfig = load_config(config_path)

    assert config.settings.source_dir == tmp_path / "source"
    assert config.settings.destination_dir == tmp_path / "dest"

    sport_ids = [sport.id for sport in config.sports]
    assert sport_ids == ["formula1_2024", "formula1_pro"]

    first, second = config.sports

    assert first.name == "Formula 1"
    assert first.metadata.url == "https://example.com/default.yaml"
    assert first.metadata.ttl_hours == 1

    assert second.name == "Formula 1 Pro"
    assert second.metadata.url == "https://example.com/pro.yaml"

    first_patterns = [pattern.regex for pattern in first.patterns]
    assert first_patterns == ["custom", "(?P<round>\\d+)[._-](?P<session>[A-Za-z0-9]+)"]


def test_file_watcher_settings_defaults_and_overrides(tmp_path) -> None:
    config_path = tmp_path / "playbook.yaml"
    source_dir = tmp_path / "downloads"
    write_yaml(
        config_path,
        f"""
        settings:
          source_dir: "{source_dir}"
          destination_dir: "{tmp_path / 'dest'}"
          cache_dir: "{tmp_path / 'cache'}"
          file_watcher:
            enabled: true
            paths:
              - "{source_dir}"
              - relative-folder
            include: "*.mkv"
            ignore:
              - "*.part"
              - "*.tmp"
            debounce_seconds: 2.5
            reconcile_interval: 60

        sports:
          - id: demo
            metadata:
              url: https://example.com/demo.yaml
        """,
    )

    config = load_config(config_path)
    watcher = config.settings.file_watcher

    assert watcher.enabled is True
    assert watcher.paths == [str(source_dir), "relative-folder"]
    assert watcher.include == ["*.mkv"]
    assert watcher.ignore == ["*.part", "*.tmp"]
    assert watcher.debounce_seconds == 2.5
    assert watcher.reconcile_interval == 60


def test_kometa_trigger_settings_round_trip(tmp_path) -> None:
    config_path = tmp_path / "playbook.yaml"
    write_yaml(
        config_path,
        f"""
        settings:
          source_dir: "{tmp_path / 'source'}"
          destination_dir: "{tmp_path / 'dest'}"
          cache_dir: "{tmp_path / 'cache'}"
          kometa_trigger:
            enabled: true
            namespace: custom
            cronjob_name: custom-sport
            job_name_prefix: manual-run

        sports:
          - id: demo
            metadata:
              url: https://example.com/demo.yaml
        """,
    )

    config = load_config(config_path)
    trigger = config.settings.kometa_trigger

    assert trigger.enabled is True
    assert trigger.namespace == "custom"
    assert trigger.cronjob_name == "custom-sport"
    assert trigger.job_name_prefix == "manual-run"

