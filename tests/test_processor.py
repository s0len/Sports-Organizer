from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest

from playbook.config import AppConfig, MetadataConfig, PatternConfig, Settings, SportConfig
from playbook.metadata import (
    MetadataChangeResult,
    MetadataFingerprintStore,
    MetadataNormalizer,
    ShowFingerprint,
    compute_show_fingerprint,
)
from playbook.models import Episode, Season, Show
from playbook.processor import Processor
from playbook.utils import sanitize_component


def _build_raw_metadata(episode_number: int) -> dict:
    return {
        "metadata": {
            "demo": {
                "title": "Demo Series",
                "seasons": {
                    "01": {
                        "title": "Season 1",
                        "episodes": [
                            {
                                "title": "Race",
                                "episode_number": episode_number,
                            }
                        ],
                    }
                },
            }
        }
    }


def test_metadata_fingerprint_tracks_episode_changes(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    settings = Settings(
        source_dir=tmp_path / "src",
        destination_dir=tmp_path / "dest",
        cache_dir=cache_dir,
    )

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", show_key="demo")
    normalizer = MetadataNormalizer(metadata_cfg)

    raw_v1 = {
        "metadata": {
            "demo": {
                "title": "Demo Series",
                "seasons": {
                    "01": {
                        "title": "Season 1",
                        "episodes": [
                            {
                                "title": "Qualifying",
                                "summary": "Initial",
                                "episode_number": 1,
                            }
                        ],
                    }
                },
            }
        }
    }
    raw_v2 = {
        "metadata": {
            "demo": {
                "title": "Demo Series",
                "seasons": {
                    "01": {
                        "title": "Season 1",
                        "episodes": [
                            {
                                "title": "Qualifying",
                                "summary": "Updated",
                                "episode_number": 1,
                            }
                        ],
                    }
                },
            }
        }
    }

    fingerprint_v1 = compute_show_fingerprint(normalizer.load_show(raw_v1), metadata_cfg)
    fingerprint_v2 = compute_show_fingerprint(normalizer.load_show(raw_v2), metadata_cfg)

    store = MetadataFingerprintStore(settings.cache_dir)

    initial = store.update("demo", fingerprint_v1)
    assert initial.updated is True
    assert initial.changed_seasons == set()
    assert initial.changed_episodes == {}
    assert initial.invalidate_all is False

    change = store.update("demo", fingerprint_v2)
    assert change.updated is True
    assert change.changed_seasons == set()
    assert change.invalidate_all is False
    assert set(change.changed_episodes.keys()) == {"01"}
    episode_key = next(iter(fingerprint_v1.episode_hashes["01"].keys()))
    assert change.changed_episodes["01"] == {episode_key}


def test_processor_removes_changed_entries_when_metadata_changes(tmp_path, monkeypatch) -> None:
    settings = Settings(
        source_dir=tmp_path / "source",
        destination_dir=tmp_path / "dest",
        cache_dir=tmp_path / "cache",
    )
    settings.source_dir.mkdir(parents=True)
    settings.destination_dir.mkdir(parents=True)
    settings.cache_dir.mkdir(parents=True)

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", show_key="demo")
    sport = SportConfig(id="demo", name="Demo", metadata=metadata_cfg)
    config = AppConfig(settings=settings, sports=[sport])

    normalizer = MetadataNormalizer(metadata_cfg)
    raw_v1 = _build_raw_metadata(1)
    raw_v2 = _build_raw_metadata(2)
    fingerprint_v1 = compute_show_fingerprint(normalizer.load_show(raw_v1), metadata_cfg)
    fingerprint_v2 = compute_show_fingerprint(normalizer.load_show(raw_v2), metadata_cfg)

    state_dir = settings.cache_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "metadata-digests.json").write_text(
        json.dumps({"demo": fingerprint_v1.to_dict()})
    )

    call_counter = {"value": 0}

    def fake_load_show(settings_arg, metadata_cfg_arg, **kwargs):
        index = 0 if call_counter["value"] == 0 else 1
        call_counter["value"] += 1
        raw = raw_v1 if index == 0 else raw_v2
        return normalizer.load_show(raw)

    monkeypatch.setattr("playbook.processor.load_show", fake_load_show)

    processor = Processor(config, enable_notifications=False)
    remove_calls: List[Dict[str, MetadataChangeResult]] = []
    original_remove = processor.processed_cache.remove_by_metadata_changes

    def tracking_remove(self, changes):
        remove_calls.append(dict(changes))
        return original_remove(changes)

    monkeypatch.setattr(
        type(processor.processed_cache),
        "remove_by_metadata_changes",
        tracking_remove,
    )

    processor.run_once()
    assert remove_calls == []
    assert call_counter["value"] == 1

    processor.run_once()
    assert len(remove_calls) == 1
    demo_change = remove_calls[0]["demo"]
    assert demo_change.changed_seasons == set()
    assert set(demo_change.changed_episodes.keys()) == {"01"}
    episode_key = next(iter(fingerprint_v1.episode_hashes["01"].keys()))
    assert demo_change.changed_episodes["01"] == {episode_key}
    assert demo_change.invalidate_all is False
    assert call_counter["value"] == 2
    assert processor.metadata_fingerprints.get("demo") == fingerprint_v2


def test_metadata_change_relinks_and_removes_old_destination(tmp_path, monkeypatch) -> None:
    settings = Settings(
        source_dir=tmp_path / "source",
        destination_dir=tmp_path / "dest",
        cache_dir=tmp_path / "cache",
    )
    settings.source_dir.mkdir(parents=True)
    settings.destination_dir.mkdir(parents=True)
    settings.cache_dir.mkdir(parents=True)

    source_file = settings.source_dir / "demo.r01.qualifying.mkv"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"demo")

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", show_key="demo")
    pattern = PatternConfig(
        regex=r"(?i)^demo\.r(?P<round>\d{2})\.(?P<session>qualifying)\.mkv$",
    )
    sport = SportConfig(id="demo", name="Demo", metadata=metadata_cfg, patterns=[pattern])
    config = AppConfig(settings=settings, sports=[sport])

    normalizer = MetadataNormalizer(metadata_cfg)

    def build_metadata(episode_number: int) -> dict:
        return {
            "metadata": {
                "demo": {
                    "title": "Demo Series",
                    "seasons": {
                        "01": {
                            "title": "Season 1",
                            "episodes": [
                                {
                                    "title": "Qualifying",
                                    "episode_number": episode_number,
                                }
                            ],
                        }
                    },
                }
            }
        }

    raw_v1 = build_metadata(1)
    raw_v2 = build_metadata(2)

    call_counter = {"value": 0}

    def fake_load_show(settings_arg, metadata_cfg_arg, **kwargs):
        index = 0 if call_counter["value"] == 0 else 1
        call_counter["value"] += 1
        raw = raw_v1 if index == 0 else raw_v2
        return normalizer.load_show(raw)

    monkeypatch.setattr("playbook.processor.load_show", fake_load_show)

    processor = Processor(config, enable_notifications=False)
    processor.run_once()

    old_destination = (
        settings.destination_dir
        / "Demo Series"
        / "01 Season 1"
        / "Demo Series - S01E01 - Qualifying.mkv"
    )
    assert old_destination.exists()

    processor.run_once()

    new_destination = (
        settings.destination_dir
        / "Demo Series"
        / "01 Season 1"
        / "Demo Series - S01E02 - Qualifying.mkv"
    )

    assert new_destination.exists()
    assert not old_destination.exists()


def test_skips_mac_resource_fork_files(tmp_path, monkeypatch) -> None:
    settings = Settings(
        source_dir=tmp_path / "source",
        destination_dir=tmp_path / "dest",
        cache_dir=tmp_path / "cache",
        dry_run=True,
    )
    settings.source_dir.mkdir(parents=True)
    settings.destination_dir.mkdir(parents=True)
    settings.cache_dir.mkdir(parents=True)

    noise_file = settings.source_dir / "._demo.r01.qualifying.mkv"
    noise_file.write_bytes(b"meta")
    valid_file = settings.source_dir / "demo.r01.qualifying.mkv"
    valid_file.write_bytes(b"video")

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", show_key="demo")
    pattern = PatternConfig(
        regex=r"(?i)^demo\.r(?P<round>\d{2})\.(?P<session>qualifying)\.mkv$",
    )
    sport = SportConfig(id="demo", name="Demo", metadata=metadata_cfg, patterns=[pattern])
    config = AppConfig(settings=settings, sports=[sport])

    episode = Episode(
        title="Qualifying",
        summary=None,
        originally_available=None,
        index=1,
        display_number=1,
    )
    season = Season(
        key="01",
        title="Season 1",
        summary=None,
        index=1,
        episodes=[episode],
        display_number=1,
        round_number=1,
    )
    show = Show(key="demo", title="Demo Series", summary=None, seasons=[season])

    monkeypatch.setattr("playbook.processor.load_show", lambda settings_arg, metadata_cfg_arg, **kwargs: show)
    monkeypatch.setattr(
        "playbook.processor.compute_show_fingerprint",
        lambda show_arg, metadata_cfg_arg: ShowFingerprint(digest="fingerprint", season_hashes={}, episode_hashes={}),
    )

    processor = Processor(config, enable_notifications=False)
    stats = processor.run_once()

    assert stats.processed == 1
    assert stats.skipped == 0
    assert stats.ignored == 0
    assert stats.errors == []
    assert stats.warnings == []


def test_destination_stays_within_root_for_hostile_metadata(tmp_path, monkeypatch) -> None:
    settings = Settings(
        source_dir=tmp_path / "source",
        destination_dir=tmp_path / "dest",
        cache_dir=tmp_path / "cache",
    )
    settings.source_dir.mkdir(parents=True)
    settings.destination_dir.mkdir(parents=True)
    settings.cache_dir.mkdir(parents=True)

    source_file = settings.source_dir / "demo.r01.qualifying.mkv"
    source_file.write_bytes(b"payload")

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", show_key="demo")
    pattern = PatternConfig(
        regex=r"(?i)^demo\.r(?P<round>\d{2})\.(?P<session>qualifying)\.mkv$",
    )
    sport = SportConfig(id="demo", name="Demo", metadata=metadata_cfg, patterns=[pattern])
    config = AppConfig(settings=settings, sports=[sport])

    episode = Episode(
        title="../Episode",
        summary=None,
        originally_available=None,
        index=1,
        display_number=1,
        aliases=["qualifying"],
    )
    season = Season(
        key="01",
        title="..",
        summary=None,
        index=1,
        episodes=[episode],
        display_number=1,
        round_number=1,
    )
    show = Show(key="demo", title="../Evil Series", summary=None, seasons=[season])

    monkeypatch.setattr("playbook.processor.load_show", lambda *args, **kwargs: show)
    monkeypatch.setattr(
        "playbook.processor.compute_show_fingerprint",
        lambda *args, **kwargs: ShowFingerprint(digest="fingerprint", season_hashes={}, episode_hashes={}),
    )

    processor = Processor(config, enable_notifications=False)
    stats = processor.run_once()

    assert stats.processed == 1
    files = [path for path in settings.destination_dir.rglob("*") if path.is_file()]
    assert len(files) == 1
    destination = files[0]

    base_resolved = settings.destination_dir.resolve()
    assert destination.resolve().is_relative_to(base_resolved)

    relative_parts = destination.relative_to(settings.destination_dir).parts
    assert all(part not in {".", ".."} for part in relative_parts)
    expected_root = sanitize_component(show.title)
    expected_season_template = f"{season.display_number:02d} {season.title}"
    expected_season = sanitize_component(expected_season_template)
    expected_episode_template = (
        f"{show.title} - S{season.display_number:02d}E{episode.display_number:02d} - {episode.title}.mkv"
    )
    expected_episode = sanitize_component(expected_episode_template)

    assert relative_parts[0] == expected_root
    assert relative_parts[1] == expected_season
    assert relative_parts[2] == expected_episode


def test_symlink_sources_are_skipped(tmp_path, monkeypatch) -> None:
    settings = Settings(
        source_dir=tmp_path / "source",
        destination_dir=tmp_path / "dest",
        cache_dir=tmp_path / "cache",
        dry_run=True,
    )
    settings.source_dir.mkdir(parents=True)
    settings.destination_dir.mkdir(parents=True)
    settings.cache_dir.mkdir(parents=True)

    real_file = settings.source_dir / "demo.r01.qualifying.mkv"
    real_file.write_bytes(b"video")

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    symlink_path = settings.source_dir / "symlink.mkv"
    try:
        symlink_path.symlink_to(outside)
    except OSError as exc:  # pragma: no cover - platform specific guard
        pytest.skip(f"symlinks not supported: {exc}")

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", show_key="demo")
    pattern = PatternConfig(
        regex=r"(?i)^demo\.r(?P<round>\d{2})\.(?P<session>qualifying)\.mkv$",
    )
    sport = SportConfig(id="demo", name="Demo", metadata=metadata_cfg, patterns=[pattern])
    config = AppConfig(settings=settings, sports=[sport])

    episode = Episode(
        title="Qualifying",
        summary=None,
        originally_available=None,
        index=1,
        display_number=1,
    )
    season = Season(
        key="01",
        title="Season 1",
        summary=None,
        index=1,
        episodes=[episode],
        display_number=1,
        round_number=1,
    )
    show = Show(key="demo", title="Demo Series", summary=None, seasons=[season])

    monkeypatch.setattr("playbook.processor.load_show", lambda *args, **kwargs: show)
    monkeypatch.setattr(
        "playbook.processor.compute_show_fingerprint",
        lambda *args, **kwargs: ShowFingerprint(digest="fingerprint", season_hashes={}, episode_hashes={}),
    )

    processor = Processor(config, enable_notifications=False)
    stats = processor.run_once()

    assert stats.processed == 1
    assert stats.skipped == 0
    assert stats.ignored == 0
    assert stats.errors == []
    assert stats.warnings == []

def test_should_suppress_sample_variants() -> None:
    assert Processor._should_suppress_sample_ignored(Path("sample.mkv"))
    assert Processor._should_suppress_sample_ignored(
        Path("nba.2025.11.08.chicago.bulls.vs.cleveland.cavaliers.1080p.web.h264-gametime-sample.mkv")
    )
    assert Processor._should_suppress_sample_ignored(Path("nba.sample.1080p.web.h264-gametime.mkv"))
    assert not Processor._should_suppress_sample_ignored(Path("nba.sampleshow.1080p.mkv"))
    assert not Processor._should_suppress_sample_ignored(Path("nba.example.1080p.mkv"))

