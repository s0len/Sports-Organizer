from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import PatternConfig, SeasonSelector, SportConfig
from .models import Episode, Season, Show
from .utils import normalize_token

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PatternRuntime:
    config: PatternConfig
    regex: re.Pattern[str]
    session_lookup: Dict[str, str]


def _build_session_lookup(pattern: PatternConfig, season: Season) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for episode in season.episodes:
        normalized = normalize_token(episode.title)
        lookup[normalized] = episode.title
        for alias in episode.aliases:
            lookup[normalize_token(alias)] = episode.title

    for canonical, aliases in pattern.session_aliases.items():
        normalized = normalize_token(canonical)
        lookup.setdefault(normalized, canonical)
        for alias in aliases:
            lookup.setdefault(normalize_token(alias), canonical)
    return lookup


def _select_season(show: Show, selector: SeasonSelector, match_groups: Dict[str, str]) -> Optional[Season]:
    mode = selector.mode
    if mode == "sequential":
        index = int(match_groups.get(selector.group or "season", 0))
        for season in show.seasons:
            if season.index == index:
                return season
        return None

    if mode == "round":
        value = match_groups.get(selector.group or "round")
        if value is None:
            return None
        try:
            round_number = int(value)
        except ValueError:
            return None
        round_number += selector.offset
        for season in show.seasons:
            candidates = [season.round_number, season.display_number]
            candidates = [num for num in candidates if num is not None]
            if round_number in candidates:
                return season
        if 0 < round_number <= len(show.seasons):
            return show.seasons[round_number - 1]
        return None

    if mode == "key":
        key = match_groups.get(selector.group or "season")
        if key is None:
            return None
        for season in show.seasons:
            if season.key == key:
                return season
        mapped = selector.mapping.get(key)
        if mapped:
            for season in show.seasons:
                if season.index == mapped:
                    return season
        return None

    if mode == "title":
        title = match_groups.get(selector.group or "season")
        if not title:
            return None
        normalized = normalize_token(title)
        for season in show.seasons:
            if normalize_token(season.title) == normalized:
                return season
        mapped = selector.mapping.get(title)
        if mapped:
            desired_round = int(mapped)
            for season in show.seasons:
                if season.round_number == desired_round or season.display_number == desired_round:
                    return season
        return None

    LOGGER.warning("Unknown season selector mode '%s'", mode)
    return None


def _select_episode(
    pattern_config: PatternConfig,
    season: Season,
    session_lookup: Dict[str, str],
    match_groups: Dict[str, str],
) -> Optional[Episode]:
    group = pattern_config.episode_selector.group
    raw_value = match_groups.get(group)
    if raw_value is None:
        if pattern_config.episode_selector.allow_fallback_to_title:
            for candidate in reversed(sorted(session_lookup.keys(), key=len)):
                if candidate and candidate in normalize_token(" ".join(match_groups.values())):
                    raw_value = candidate
                    break
        if raw_value is None:
            return None

    normalized = normalize_token(raw_value)
    metadata_title = session_lookup.get(normalized)

    candidates = season.episodes
    if metadata_title:
        for episode in candidates:
            if normalize_token(episode.title) == normalize_token(metadata_title):
                return episode
        for episode in candidates:
            if normalize_token(metadata_title) in [normalize_token(alias) for alias in episode.aliases]:
                return episode

    for episode in candidates:
        if normalize_token(episode.title) == normalized:
            return episode
        if any(normalize_token(alias) == normalized for alias in episode.aliases):
            return episode

    return None


def compile_patterns(sport: SportConfig) -> List[PatternRuntime]:
    compiled: List[PatternRuntime] = []
    for pattern in sport.patterns:
        compiled.append(
            PatternRuntime(
                config=pattern,
                regex=pattern.compiled_regex(),
                session_lookup={},
            )
        )
    return compiled


def match_file_to_episode(
    filename: str,
    sport: SportConfig,
    show: Show,
    patterns: List[PatternRuntime],
) -> Optional[Dict[str, object]]:
    for pattern_runtime in patterns:
        match = pattern_runtime.regex.search(filename)
        if not match:
            continue

        groups = {key: value for key, value in match.groupdict().items() if value is not None}
        season = _select_season(show, pattern_runtime.config.season_selector, groups)
        if not season:
            LOGGER.debug("Season not resolved for file %s with pattern %s", filename, pattern_runtime.config.regex)
            continue

        pattern_runtime.session_lookup = _build_session_lookup(pattern_runtime.config, season)
        episode = _select_episode(pattern_runtime.config, season, pattern_runtime.session_lookup, groups)
        if not episode:
            LOGGER.debug(
                "Episode not resolved for file %s in season %s using pattern %s",
                filename,
                season.title,
                pattern_runtime.config.regex,
            )
            continue

        return {
            "season": season,
            "episode": episode,
            "pattern": pattern_runtime.config,
            "groups": groups,
        }
    return None
