from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from rapidfuzz.distance import DamerauLevenshtein, Levenshtein
except ImportError:  # pragma: no cover - optional dependency
    DamerauLevenshtein = None  # type: ignore[assignment]
    Levenshtein = None  # type: ignore[assignment]


def _token_similarity(candidate: str, target: str) -> float:
    if DamerauLevenshtein and Levenshtein:
        similarity = Levenshtein.normalized_similarity(candidate, target)
        if similarity > 1:
            similarity /= 100
        return float(similarity)
    return difflib.SequenceMatcher(None, candidate, target, autojunk=False).ratio()


def _tokens_close(candidate: str, target: str) -> bool:
    if len(candidate) < 4 or len(target) < 4:
        return False
    if abs(len(candidate) - len(target)) > 1:
        return False
    if candidate[0] != target[0]:
        return False

    if len(candidate) == len(target):
        differing_indices = [idx for idx, (cand_char, targ_char) in enumerate(zip(candidate, target)) if cand_char != targ_char]
        if len(differing_indices) == 2:
            first, second = differing_indices
            if candidate[first] == target[second] and candidate[second] == target[first]:
                return True

    if DamerauLevenshtein and Levenshtein:
        distance = DamerauLevenshtein.distance(candidate, target)
        if distance <= 1:
            return True
        similarity = Levenshtein.normalized_similarity(candidate, target)
        if similarity > 1:
            similarity /= 100
        return similarity >= 0.92

    return _token_similarity(candidate, target) >= 0.9


def _resolve_session_lookup(session_lookup: Dict[str, str], token: str) -> Optional[str]:
    direct = session_lookup.get(token)
    if direct:
        return direct

    if len(token) < 4:
        return None

    best_key: Optional[str] = None
    best_score = 0.0

    for candidate in session_lookup.keys():
        if len(candidate) < 4:
            continue
        if not _tokens_close(candidate, token):
            continue
        score = _token_similarity(candidate, token)
        if DamerauLevenshtein:
            distance = DamerauLevenshtein.distance(candidate, token)
            if distance <= 1:
                score = max(score, 0.92)
        if score > best_score:
            best_key = candidate
            best_score = score

    if best_key is not None and best_score >= 0.85:
        return session_lookup[best_key]
    return None


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
        for season in show.seasons:
            season_normalized = normalize_token(season.title)
            if normalized and (normalized in season_normalized or season_normalized in normalized):
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
    normalized_without_part: Optional[str] = None
    if "part" in normalized:
        without_trailing = re.sub(r"part\d+$", "", normalized)
        without_embedded = re.sub(r"part\d+", "", without_trailing)
        cleaned = without_embedded.strip()
        normalized_without_part = cleaned or None

    def tokens_match(candidate: str, target: str) -> bool:
        if not candidate or not target:
            return False
        if candidate == target:
            return True
        if candidate.startswith(target) or target.startswith(candidate):
            return True
        return _tokens_close(candidate, target)

    metadata_title = _resolve_session_lookup(session_lookup, normalized)
    if metadata_title:
        target_token = normalize_token(metadata_title)
        if not any(tokens_match(normalize_token(episode.title), target_token) for episode in season.episodes):
            metadata_title = None

    if not metadata_title and normalized_without_part:
        metadata_title = _resolve_session_lookup(session_lookup, normalized_without_part)
        if metadata_title:
            target_token = normalize_token(metadata_title)
            if not any(tokens_match(normalize_token(episode.title), target_token) for episode in season.episodes):
                metadata_title = None

    candidates = season.episodes
    if metadata_title:
        target_token = normalize_token(metadata_title)
        for episode in candidates:
            episode_token = normalize_token(episode.title)
            alias_tokens = [normalize_token(alias) for alias in episode.aliases]
            if episode_token == target_token:
                return episode
            if target_token in alias_tokens:
                return episode

        for episode in candidates:
            episode_token = normalize_token(episode.title)
            alias_tokens = [normalize_token(alias) for alias in episode.aliases]
            if tokens_match(episode_token, target_token):
                return episode
            if any(tokens_match(alias_token, target_token) for alias_token in alias_tokens):
                return episode

    if normalized_without_part:
        for episode in candidates:
            episode_token = normalize_token(episode.title)
            alias_tokens = [normalize_token(alias) for alias in episode.aliases]
            if tokens_match(episode_token, normalized_without_part):
                return episode
            if any(tokens_match(alias_token, normalized_without_part) for alias_token in alias_tokens):
                return episode

    for episode in candidates:
        episode_token = normalize_token(episode.title)
        alias_tokens = [normalize_token(alias) for alias in episode.aliases]
        if tokens_match(episode_token, normalized):
            return episode
        if any(tokens_match(alias_token, normalized) for alias_token in alias_tokens):
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
    *,
    diagnostics: Optional[List[Tuple[str, str]]] = None,
) -> Optional[Dict[str, object]]:
    matched_patterns = 0
    failed_resolutions: List[str] = []

    def record(severity: str, message: str) -> None:
        if diagnostics is not None:
            diagnostics.append((severity, message))

    def summarize_groups(groups: Dict[str, str]) -> str:
        if not groups:
            return "none"
        parts = [f"{key}={value!r}" for key, value in sorted(groups.items())]
        return ", ".join(parts)

    def summarize_episode_candidates(season: Season, *, limit: int = 5) -> str:
        titles = [episode.title for episode in season.episodes[:limit]]
        if len(season.episodes) > limit:
            titles.append("…")
        return ", ".join(titles) if titles else "none"
    for pattern_runtime in patterns:
        match = pattern_runtime.regex.search(filename)
        if not match:
            continue

        matched_patterns += 1
        groups = {key: value for key, value in match.groupdict().items() if value is not None}
        season = _select_season(show, pattern_runtime.config.season_selector, groups)
        if not season:
            descriptor = pattern_runtime.config.description or pattern_runtime.config.regex
            selector = pattern_runtime.config.season_selector
            selector_group = selector.group or selector.mode or "season"
            candidate_value = groups.get(selector.group or selector.mode or "season")
            message = (
                f"{descriptor}: season not resolved "
                f"(selector mode={selector.mode!r}, group={selector_group!r}, "
                f"value={candidate_value!r}, groups={summarize_groups(groups)})"
            )
            LOGGER.debug("Season not resolved for file %s with pattern %s", filename, pattern_runtime.config.regex)
            failed_resolutions.append(message)
            severity = "ignored" if sport.allow_unmatched else "warning"
            record(severity, message)
            continue

        pattern_runtime.session_lookup = _build_session_lookup(pattern_runtime.config, season)
        episode = _select_episode(pattern_runtime.config, season, pattern_runtime.session_lookup, groups)
        if not episode:
            descriptor = pattern_runtime.config.description or pattern_runtime.config.regex
            selector = pattern_runtime.config.episode_selector
            raw_value = groups.get(selector.group)
            normalized_value = normalize_token(raw_value) if raw_value else None
            message = (
                f"{descriptor}: episode not resolved "
                f"(group={selector.group!r}, raw_value={raw_value!r}, normalized={normalized_value!r}, "
                f"season='{season.title}', candidates={summarize_episode_candidates(season)}, "
                f"groups={summarize_groups(groups)})"
            )
            LOGGER.debug(
                "Episode not resolved for file %s in season %s using pattern %s",
                filename,
                season.title,
                pattern_runtime.config.regex,
            )
            failed_resolutions.append(message)
            severity = "ignored" if sport.allow_unmatched else "warning"
            record(severity, message)
            continue

        return {
            "season": season,
            "episode": episode,
            "pattern": pattern_runtime.config,
            "groups": groups,
        }
    if failed_resolutions:
        log_fn = LOGGER.debug if sport.allow_unmatched else LOGGER.warning
        log_fn(
            "File %s matched %d pattern(s) but could not resolve:%s%s",
            filename,
            matched_patterns,
            "\n  - " if len(failed_resolutions) > 1 else " ",
            "\n  - ".join(failed_resolutions) if len(failed_resolutions) > 1 else failed_resolutions[0],
        )
        message = (
            f"Matched {matched_patterns} pattern(s) but could not resolve: "
            f"{'; '.join(failed_resolutions)}"
        )
        severity = "ignored" if sport.allow_unmatched else "warning"
        record(severity, message)
    elif matched_patterns == 0:
        LOGGER.debug("File %s did not match any configured patterns", filename)
        record("ignored", "Did not match any configured patterns")
    return None
