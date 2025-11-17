from __future__ import annotations

from playbook.cache import CachedFileRecord, ProcessedFileCache
from playbook.metadata import MetadataChangeResult


def test_remove_by_metadata_changes_drops_only_matching_entries(tmp_path) -> None:
    cache = ProcessedFileCache(tmp_path)
    cache._records = {
        "/videos/demo1.mkv": CachedFileRecord(
            mtime_ns=1,
            size=100,
            destination="/library/demo1.mkv",
            sport_id="demo",
            season_key="01",
            episode_key="episode1",
        ),
        "/videos/demo2.mkv": CachedFileRecord(
            mtime_ns=2,
            size=200,
            destination="/library/demo2.mkv",
            sport_id="demo",
            season_key="02",
            episode_key="episode2",
        ),
        "/videos/other.mkv": CachedFileRecord(
            mtime_ns=3,
            size=300,
            destination="/library/other.mkv",
            sport_id="other",
            season_key="99",
            episode_key="episodeA",
        ),
    }

    change = MetadataChangeResult(
        updated=True,
        changed_seasons={"01"},
        changed_episodes={},
        invalidate_all=False,
    )

    removed = cache.remove_by_metadata_changes({"demo": change})

    assert "/videos/demo1.mkv" in removed
    assert "/videos/demo2.mkv" not in removed
    assert "/videos/other.mkv" not in removed
    assert "/videos/demo2.mkv" in cache._records
    assert "/videos/other.mkv" in cache._records


def test_remove_by_metadata_changes_respects_episode_scope(tmp_path) -> None:
    cache = ProcessedFileCache(tmp_path)
    cache._records = {
        "/videos/demo1.mkv": CachedFileRecord(
            mtime_ns=1,
            size=100,
            destination="/library/demo1.mkv",
            sport_id="demo",
            season_key="01",
            episode_key="episode1",
        ),
        "/videos/demo2.mkv": CachedFileRecord(
            mtime_ns=2,
            size=200,
            destination="/library/demo2.mkv",
            sport_id="demo",
            season_key="01",
            episode_key="episode2",
        ),
    }

    change = MetadataChangeResult(
        updated=True,
        changed_seasons=set(),
        changed_episodes={"01": {"episode1"}},
        invalidate_all=False,
    )

    removed = cache.remove_by_metadata_changes({"demo": change})

    assert "/videos/demo1.mkv" in removed
    assert "/videos/demo2.mkv" not in removed


def test_remove_by_metadata_changes_drops_legacy_entries_without_ownership(tmp_path) -> None:
    cache = ProcessedFileCache(tmp_path)
    cache._records = {
        "/videos/legacy.mkv": CachedFileRecord(
            mtime_ns=1,
            size=100,
            destination="/library/legacy.mkv",
        )
    }

    change = MetadataChangeResult(
        updated=True,
        changed_seasons={"01"},
        changed_episodes={},
        invalidate_all=False,
    )

    removed = cache.remove_by_metadata_changes({"demo": change})

    assert "/videos/legacy.mkv" in removed
    assert "/videos/legacy.mkv" not in cache._records

