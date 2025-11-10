from __future__ import annotations

import json
from pathlib import Path

from sports_organizer.config import AppConfig, MetadataConfig, PatternConfig, Settings, SportConfig
from sports_organizer.metadata import MetadataNormalizer, compute_show_fingerprint
from sports_organizer.models import Episode, Season, Show
from sports_organizer.processor import Processor


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


def test_processor_clears_processed_cache_when_metadata_changes(tmp_path, monkeypatch) -> None:
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
    (state_dir / "metadata-digests.json").write_text(json.dumps({"demo": fingerprint_v1}))

    call_counter = {"value": 0}

    def fake_load_show(settings_arg, metadata_cfg_arg):
        index = 0 if call_counter["value"] == 0 else 1
        call_counter["value"] += 1
        raw = raw_v1 if index == 0 else raw_v2
        return normalizer.load_show(raw)

    monkeypatch.setattr("sports_organizer.processor.load_show", fake_load_show)

    processor = Processor(config, enable_notifications=False)
    original_clear = processor.processed_cache.clear
    clear_calls = []

    def tracking_clear(self):
        clear_calls.append(True)
        return original_clear.__func__(self)

    monkeypatch.setattr(
        type(processor.processed_cache),
        "clear",
        tracking_clear,
    )

    processor.run_once()
    assert clear_calls == []
    assert call_counter["value"] == 1

    processor.run_once()
    assert clear_calls == [True]
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

    def fake_load_show(settings_arg, metadata_cfg_arg):
        index = 0 if call_counter["value"] == 0 else 1
        call_counter["value"] += 1
        raw = raw_v1 if index == 0 else raw_v2
        return normalizer.load_show(raw)

    monkeypatch.setattr("sports_organizer.processor.load_show", fake_load_show)

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

    monkeypatch.setattr("sports_organizer.processor.load_show", lambda settings_arg, metadata_cfg_arg: show)
    monkeypatch.setattr("sports_organizer.processor.compute_show_fingerprint", lambda show_arg, metadata_cfg_arg: "fingerprint")

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

