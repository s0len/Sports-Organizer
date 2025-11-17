from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pytest
import yaml

from playbook.config import (
    DestinationTemplates,
    MetadataConfig,
    SportConfig,
    _build_pattern_config,
)
from playbook.matcher import compile_patterns, match_file_to_episode
from playbook.models import Episode, Season, Show
from playbook.pattern_templates import load_builtin_pattern_sets


DATA_PATH = Path(__file__).resolve().parent / "data" / "pattern_samples.yaml"


@dataclass
class FilenameExpectation:
    value: str
    expect_episode: Optional[str] = None


@dataclass
class PatternSample:
    description: str
    sport: SportConfig
    show: Show
    filenames: List[FilenameExpectation]


def _load_yaml(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        content = yaml.safe_load(handle) or {}
    return content


def _build_episode(index: int, data: Dict[str, object]) -> Episode:
    aliases = data.get("aliases", []) or []
    if isinstance(aliases, str):
        aliases = [aliases]
    return Episode(
        title=str(data.get("title", f"Episode {index}")),
        summary=data.get("summary"),
        originally_available=None,
        index=index,
        metadata=dict(data),
        display_number=int(data["display_number"]) if data.get("display_number") is not None else None,
        aliases=list(aliases),
    )


def _build_season(index: int, data: Dict[str, object]) -> Season:
    raw_episodes = data.get("episodes", []) or []
    episodes = [_build_episode(ep_idx + 1, ep_data) for ep_idx, ep_data in enumerate(raw_episodes)]
    return Season(
        key=str(data.get("key", index)),
        title=str(data.get("title", f"Season {index}")),
        summary=data.get("summary"),
        index=index,
        episodes=episodes,
        sort_title=data.get("sort_title"),
        display_number=int(data["display_number"]) if data.get("display_number") is not None else None,
        round_number=int(data["round_number"]) if data.get("round_number") is not None else None,
        metadata=dict(data),
    )


def _build_show(data: Dict[str, object]) -> Show:
    raw_seasons = data.get("seasons", []) or []
    seasons = [_build_season(idx + 1, season_data) for idx, season_data in enumerate(raw_seasons)]
    return Show(
        key=str(data.get("key", "sample")),
        title=str(data.get("title", "Sample Sport")),
        summary=data.get("summary"),
        seasons=seasons,
        metadata=dict(data),
    )


def _build_sport(data: Dict[str, object]) -> SportConfig:
    pattern_definitions: List[Dict[str, object]] = []
    for set_name in data.get("pattern_sets", []) or []:
        builtin_sets = load_builtin_pattern_sets()
        if set_name not in builtin_sets:
            raise AssertionError(f"Unknown pattern_set '{set_name}' referenced in pattern_samples.yaml")
        pattern_definitions.extend(deepcopy(builtin_sets[set_name]))

    pattern_definitions.extend(deepcopy(data.get("file_patterns", []) or []))

    patterns = sorted(
        (_build_pattern_config(pattern) for pattern in pattern_definitions),
        key=lambda cfg: cfg.priority,
    )

    return SportConfig(
        id=str(data.get("id")),
        name=str(data.get("name", data.get("id"))),
        enabled=True,
        metadata=MetadataConfig(url=str(data.get("metadata_url", "https://example.com"))),
        patterns=patterns,
        destination=DestinationTemplates(),
        source_globs=list(data.get("source_globs", [])),
        source_extensions=list(
            data.get("source_extensions", [".mkv", ".mp4", ".ts", ".m4v", ".avi"])
        ),
        link_mode=str(data.get("link_mode", "hardlink")),
        allow_unmatched=bool(data.get("allow_unmatched", False)),
    )


def _build_filenames(entries: Iterable[object]) -> List[FilenameExpectation]:
    expectations: List[FilenameExpectation] = []
    for entry in entries:
        if isinstance(entry, str):
            expectations.append(FilenameExpectation(value=entry))
            continue
        if isinstance(entry, dict):
            expectations.append(
                FilenameExpectation(
                    value=str(entry.get("value")),
                    expect_episode=(
                        str(entry.get("expect_episode")) if entry.get("expect_episode") is not None else None
                    ),
                )
            )
            continue
        raise AssertionError(f"Unsupported filename entry in pattern_samples.yaml: {entry!r}")
    return expectations


def _load_samples() -> List[PatternSample]:
    raw = _load_yaml(DATA_PATH)
    samples: List[PatternSample] = []
    for entry in raw.get("samples", []):
        sport_data = entry.get("sport") or {}
        show_data = entry.get("show") or {}
        filenames = entry.get("filenames") or []
        description = str(entry.get("description") or sport_data.get("id") or "pattern-sample")

        sample = PatternSample(
            description=description,
            sport=_build_sport(sport_data),
            show=_build_show(show_data),
            filenames=_build_filenames(filenames),
        )
        samples.append(sample)
    return samples


SAMPLES = _load_samples()


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda item: item.description)
def test_pattern_samples(sample: PatternSample) -> None:
    patterns = compile_patterns(sample.sport)
    for expectation in sample.filenames:
        diagnostics: List = []
        result = match_file_to_episode(
            expectation.value,
            sample.sport,
            sample.show,
            patterns,
            diagnostics=diagnostics,
        )
        assert result is not None, (
            f"{sample.description}: '{expectation.value}' did not resolve. Diagnostics: {diagnostics}"
        )
        if expectation.expect_episode is not None:
            assert (
                result["episode"].title == expectation.expect_episode
            ), f"{sample.description}: '{expectation.value}' matched {result['episode'].title!r}, expected {expectation.expect_episode!r}"

