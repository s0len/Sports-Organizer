from __future__ import annotations

from typing import List

import pytest

from sports_organizer.config import MetadataConfig, Settings
from sports_organizer.metadata import MetadataNormalizer, fetch_metadata


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

