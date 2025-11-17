from __future__ import annotations

from typing import Dict, List, Tuple

from playbook.config import (
    DestinationTemplates,
    MetadataConfig,
    PatternConfig,
    SeasonSelector,
    SportConfig,
)
from playbook.matcher import compile_patterns, match_file_to_episode
from playbook.models import Episode, Season, Show


def build_show() -> Tuple[Show, Season]:
    practice = Episode(
        title="Free Practice 1",
        summary=None,
        originally_available=None,
        index=1,
        aliases=["FP1"],
    )
    qualifying = Episode(
        title="Qualifying",
        summary=None,
        originally_available=None,
        index=2,
        aliases=["Quali"],
    )

    season = Season(
        key="2024",
        title="2024 Bahrain Grand Prix",
        summary=None,
        index=1,
        episodes=[practice, qualifying],
        display_number=1,
        round_number=1,
    )

    show = Show(key="f1", title="Formula 1", summary=None, seasons=[season])
    return show, season


def build_sport(patterns: List[PatternConfig]) -> SportConfig:
    return SportConfig(
        id="f1",
        name="Formula 1",
        metadata=MetadataConfig(url="https://example.com"),
        patterns=patterns,
        destination=DestinationTemplates(),
    )


def test_match_file_to_episode_resolves_aliases() -> None:
    pattern = PatternConfig(
        regex=r"(?i)^(?P<round>\d+)[._-]*(?P<session>[A-Z0-9]+)",
        priority=10,
    )

    sport = build_sport([pattern])
    show, season = build_show()

    patterns = compile_patterns(sport)

    diagnostics: List[Tuple[str, str]] = []
    result = match_file_to_episode("01.fp1.release.mkv", sport, show, patterns, diagnostics=diagnostics)

    assert result is not None
    assert result["season"] is season
    assert result["episode"].title == "Free Practice 1"
    assert result["pattern"] is pattern
    assert diagnostics == []


def test_match_file_to_episode_warns_when_season_missing() -> None:
    pattern = PatternConfig(
        regex=r"(?i)^(?P<round>\d+)[._-]*(?P<session>[A-Z0-9]+)",
        season_selector=SeasonSelector(mode="round", group="round"),
        priority=10,
    )

    sport = build_sport([pattern])
    show, _ = build_show()

    patterns = compile_patterns(sport)

    diagnostics: List[Tuple[str, str]] = []
    result = match_file_to_episode("99.fp1.release.mkv", sport, show, patterns, diagnostics=diagnostics)

    assert result is None
    assert diagnostics
    severity, message = diagnostics[0]
    assert severity == "warning"
    assert "season not resolved" in message


def test_match_file_to_episode_includes_trace_details() -> None:
    pattern = PatternConfig(
        regex=r"(?i)^(?P<round>\d+)[._-]*(?P<session>[A-Za-z]+)",
        season_selector=SeasonSelector(mode="round", group="round"),
        priority=10,
    )

    sport = build_sport([pattern])
    show, season = build_show()

    patterns = compile_patterns(sport)

    trace: Dict[str, object] = {}
    result = match_file_to_episode(
        "01.qualifying.mkv",
        sport,
        show,
        patterns,
        diagnostics=None,
        trace=trace,
    )

    assert result is not None
    assert trace["status"] == "matched"
    attempts = trace["attempts"]
    assert attempts
    matched_attempt = next(item for item in attempts if item["status"] == "matched")
    assert matched_attempt["season"]["title"] == season.title
    assert matched_attempt["episode"]["title"] == "Qualifying"
    assert trace["messages"] == []

