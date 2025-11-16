from __future__ import annotations

from typing import List

import pytest
import requests

from sports_organizer.cache import MetadataHttpCache
from sports_organizer.config import MetadataConfig, Settings
from sports_organizer.metadata import MetadataFetchStatistics, MetadataNormalizer, fetch_metadata


class DummyResponse:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        source_dir=tmp_path / "source",
        destination_dir=tmp_path / "dest",
        cache_dir=tmp_path / "cache",
    )


def test_fetch_metadata_uses_cache(monkeypatch, settings) -> None:
    payload = """
    metadata:
      demo:
        title: Demo Series
    """

    requests_called: List[str] = []

    def fake_get(url, headers=None, timeout=None):
        requests_called.append(url)
        return DummyResponse(payload)

    monkeypatch.setattr("sports_organizer.metadata.requests.get", fake_get)

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml")

    first = fetch_metadata(metadata_cfg, settings)
    assert first["metadata"]["demo"]["title"] == "Demo Series"
    assert requests_called == ["https://example.com/demo.yaml"]

    second = fetch_metadata(metadata_cfg, settings)
    assert second == first
    assert requests_called == ["https://example.com/demo.yaml"]


def test_fetch_metadata_respects_conditional_requests(monkeypatch, settings) -> None:
    payload = """
    metadata:
      demo:
        title: Demo Series
    """

    http_cache = MetadataHttpCache(settings.cache_dir)
    stats = MetadataFetchStatistics()
    call_count = {"value": 0}

    def fake_get(url, headers=None, timeout=None):
        call_count["value"] += 1
        if call_count["value"] == 1:
            response = DummyResponse(payload)
            response.status_code = 200
            response.headers = {"ETag": '"abc"', "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"}
            return response
        assert headers is not None
        assert headers.get("If-None-Match") == '"abc"'
        response = DummyResponse("")
        response.status_code = 304
        response.headers = {"ETag": '"abc"'}
        return response

    monkeypatch.setattr("sports_organizer.metadata.requests.get", fake_get)

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", ttl_hours=0)
    first = fetch_metadata(metadata_cfg, settings, http_cache=http_cache, stats=stats)
    second = fetch_metadata(metadata_cfg, settings, http_cache=http_cache, stats=stats)

    assert first["metadata"]["demo"]["title"] == "Demo Series"
    assert second == first
    snapshot = stats.snapshot()
    assert snapshot["cache_hits"] == 0
    assert snapshot["cache_misses"] == 2
    assert snapshot["network_requests"] == 2
    assert snapshot["not_modified"] == 1
    assert snapshot["stale_used"] == 0
    assert snapshot["failures"] == 0


def test_fetch_metadata_uses_stale_on_failure(monkeypatch, settings) -> None:
    payload = """
    metadata:
      demo:
        title: Demo Series
    """

    http_cache = MetadataHttpCache(settings.cache_dir)
    stats = MetadataFetchStatistics()
    call_count = {"value": 0}

    def flaky_get(url, headers=None, timeout=None):
        call_count["value"] += 1
        if call_count["value"] == 1:
            response = DummyResponse(payload)
            response.status_code = 200
            response.headers = {}
            return response
        raise requests.RequestException("boom")

    monkeypatch.setattr("sports_organizer.metadata.requests.get", flaky_get)

    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", ttl_hours=0)
    first = fetch_metadata(metadata_cfg, settings, http_cache=http_cache, stats=stats)
    second = fetch_metadata(metadata_cfg, settings, http_cache=http_cache, stats=stats)

    assert first == second
    assert call_count["value"] == 4
    snapshot = stats.snapshot()
    assert snapshot["stale_used"] == 1
    assert snapshot["failures"] == 0


def test_metadata_normalizer_loads_show_with_rounds() -> None:
    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", show_key="f1")
    normalizer = MetadataNormalizer(metadata_cfg)

    raw = {
        "metadata": {
            "f1": {
                "title": "Formula 1",
                "summary": "Season overview",
                "seasons": {
                    "01": {
                        "title": "01 Bahrain Grand Prix",
                        "sort_title": "01_bahrain",
                        "episodes": [
                            {
                                "title": "Free Practice 1",
                                "episode_number": 1,
                                "originally_available": "2024-03-01",
                                "aliases": ["FP1"],
                            },
                            {
                                "title": "Qualifying",
                                "aliases": ["Quali"],
                            },
                        ],
                    }
                },
            }
        }
    }

    show = normalizer.load_show(raw)

    assert show.key == "f1"
    assert show.title == "Formula 1"
    assert len(show.seasons) == 1

    season = show.seasons[0]
    assert season.round_number == 1
    assert season.display_number == 1

    episode = season.episodes[0]
    assert episode.title == "Free Practice 1"
    assert episode.display_number == 1
    assert episode.aliases == ["FP1"]
    assert episode.originally_available.isoformat() == "2024-03-01"


def test_metadata_normalizer_prefers_ufc_event_numbers_from_title() -> None:
    metadata_cfg = MetadataConfig(url="https://example.com/demo.yaml", show_key="ufc_2025")
    normalizer = MetadataNormalizer(metadata_cfg)

    raw = {
        "metadata": {
            "ufc_2025": {
                "title": "UFC 2025",
                "seasons": {
                    "29": {
                        "title": "UFC 319 du Plessis vs Chimaev",
                        "sort_title": "029_UFC 319 du Plessis vs Chimaev",
                        "episodes": [{"title": "Prelims"}],
                    },
                    "30": {
                        "title": "UFC Fight Night 263 Garcia vs Onama",
                        "sort_title": "030_UFC Fight Night 263 Garcia vs Onama",
                        "episodes": [{"title": "Main Card"}],
                    },
                },
            }
        }
    }

    show = normalizer.load_show(raw)

    assert len(show.seasons) == 2
    numbered = show.seasons[0]
    fight_night = show.seasons[1]

    assert numbered.title.startswith("UFC 319")
    assert numbered.round_number == 319
    assert numbered.display_number == 319

    assert fight_night.title.startswith("UFC Fight Night 263")
    assert fight_night.round_number == 263
    assert fight_night.display_number == 263

