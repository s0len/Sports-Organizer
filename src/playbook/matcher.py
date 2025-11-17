from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

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

_NOISE_TOKENS = (
    "f1live",
    "f1tv",
    "f1kids",
    "sky",
    "intl",
    "international",
    "proper",
    "verum",
)


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
        if selector.aliases:
            alias_target = selector.aliases.get(title)
            if alias_target is None:
                normalized_title = normalize_token(title)
                for alias_key, mapped_title in selector.aliases.items():
                    if normalize_token(alias_key) == normalized_title:
                        alias_target = mapped_title
                        break
            if alias_target:
                title = alias_target
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
    trace: Optional[Dict[str, Any]] = None,
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

    def _strip_noise(normalized: str) -> str:
        result = normalized
        for token in _NOISE_TOKENS:
            if token and token in result:
                result = result.replace(token, "")
        return result

    normalized = _strip_noise(normalize_token(raw_value))
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

    lookup_attempts: List[Tuple[str, str, str]] = []
    trace_lookup_records: List[Dict[str, str]] = []
    seen_tokens: Set[str] = set()

    def add_lookup(label: str, value: Optional[str]) -> None:
        if not value:
            return

        variants: List[str] = []
        source_variants: List[str] = []

        def push_variant(candidate: Optional[str]) -> None:
            if not candidate:
                return
            if candidate in variants:
                return
            variants.append(candidate)
            source_variants.append(candidate)

        push_variant(value)

        split_variants = [segment for segment in re.split(r"[\s._-]+", value) if segment]
        if split_variants:
            push_variant(" ".join(split_variants))
            without_noise_words = " ".join(
                word for word in split_variants if _strip_noise(normalize_token(word))
            )
            push_variant(without_noise_words)
            for index in range(1, len(split_variants)):
                truncated = " ".join(split_variants[index:])
                push_variant(truncated)

        for variant in variants:
            normalized_variant = _strip_noise(normalize_token(variant))
            if not normalized_variant or normalized_variant in seen_tokens:
                continue
            seen_tokens.add(normalized_variant)
            lookup_attempts.append((label, variant, normalized_variant))
            if trace is not None:
                trace_lookup_records.append(
                    {
                        "label": label,
                        "value": variant,
                        "normalized": normalized_variant,
                    }
                )

    add_lookup("session", raw_value)

    if normalized_without_part and normalized_without_part not in seen_tokens:
        lookup_attempts.append(("session_without_part", raw_value, normalized_without_part))
        if trace is not None:
            trace_lookup_records.append(
                {
                    "label": "session_without_part",
                    "value": raw_value,
                    "normalized": normalized_without_part,
                }
            )
        seen_tokens.add(normalized_without_part)

    for key, value in match_groups.items():
        if key == group:
            continue
        add_lookup(key, value)

    away_value = match_groups.get("away")
    home_value = match_groups.get("home")
    separator_value = match_groups.get("separator")
    if away_value and home_value:
        separator_candidates: List[str] = []
        if separator_value:
            separator_candidates.append(separator_value)
        separator_candidates.extend(["at", "vs", "v", "@"])
        seen_separators: Set[str] = set()
        for separator_candidate in separator_candidates:
            if not separator_candidate:
                continue
            normalized_separator = normalize_token(separator_candidate)
            if normalized_separator in seen_separators:
                continue
            seen_separators.add(normalized_separator)
            add_lookup("away_home", f"{away_value}.{separator_candidate}.{home_value}")
            add_lookup("away_home", f"{away_value} {separator_candidate} {home_value}")
            add_lookup("home_away", f"{home_value}.{separator_candidate}.{away_value}")
            add_lookup("home_away", f"{home_value} {separator_candidate} {away_value}")

    venue_value = match_groups.get("venue")
    if venue_value:
        add_lookup("venue+session", f"{venue_value} {raw_value}")
        add_lookup("session+venue", f"{raw_value} {venue_value}")

    def find_episode_for_token(token: str) -> Optional[Episode]:
        for episode in season.episodes:
            episode_token = normalize_token(episode.title)
            if tokens_match(episode_token, token):
                return episode
            alias_tokens = [normalize_token(alias) for alias in episode.aliases]
            if any(tokens_match(alias_token, token) for alias_token in alias_tokens):
                return episode
        return None

    lookup_attempts.sort(key=lambda item: len(item[2]), reverse=True)

    attempted_variants: List[str] = []

    for label, variant, normalized_variant in lookup_attempts:
        attempted_variants.append(f"{label}:{variant}")
        metadata_title = _resolve_session_lookup(session_lookup, normalized_variant)
        candidate_tokens: List[str] = []
        if metadata_title:
            target_token = normalize_token(metadata_title)
            candidate_tokens.append(target_token)
        candidate_tokens.append(normalized_variant)

        metadata_token = normalize_token(metadata_title) if metadata_title else None

        for token in candidate_tokens:
            if not token:
                continue
            if metadata_token and token == metadata_token:
                episode = next(
                    (item for item in season.episodes if normalize_token(item.title) == metadata_token),
                    None,
                )
                if episode:
                    if trace is not None:
                        trace["match"] = {
                            "label": label,
                            "value": variant,
                            "normalized": normalized_variant,
                            "token": token,
                            "episode_title": episode.title,
                            "matched_via_alias": False,
                        }
                        trace["lookup_attempts"] = trace_lookup_records
                    return episode
            episode = find_episode_for_token(token)
            if episode:
                if trace is not None:
                    trace["match"] = {
                        "label": label,
                        "value": variant,
                        "normalized": normalized_variant,
                        "token": token,
                        "episode_title": episode.title,
                        "matched_via_alias": normalize_token(episode.title) != token,
                    }
                    trace["lookup_attempts"] = trace_lookup_records
                return episode

    if attempted_variants:
        match_groups["_attempted_session_tokens"] = attempted_variants
    if trace is not None:
        trace.setdefault("match", None)
        trace["lookup_attempts"] = trace_lookup_records
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
    trace: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, object]]:
    matched_patterns = 0
    failed_resolutions: List[str] = []
    trace_attempts: Optional[List[Dict[str, Any]]] = None
    if trace is not None:
        trace_attempts = trace.setdefault("attempts", [])
        trace.setdefault("messages", [])
        trace["matched_patterns"] = 0

    def record(severity: str, message: str) -> None:
        if diagnostics is not None:
            diagnostics.append((severity, message))
        if trace is not None:
            trace["messages"].append({"severity": severity, "message": message})

    def summarize_groups(groups: Dict[str, str]) -> str:
        if not groups:
            return "none"
        parts = [
            f"{key}={value!r}"
            for key, value in sorted(groups.items())
            if not key.startswith("_")
        ]
        return ", ".join(parts)

    def summarize_episode_candidates(season: Season, *, limit: int = 5) -> str:
        titles = [episode.title for episode in season.episodes[:limit]]
        if len(season.episodes) > limit:
            titles.append("…")
        return ", ".join(titles) if titles else "none"
    for pattern_runtime in patterns:
        descriptor = pattern_runtime.config.description or pattern_runtime.config.regex
        match = pattern_runtime.regex.search(filename)
        if not match:
            if trace_attempts is not None:
                trace_attempts.append(
                    {
                        "pattern": descriptor,
                        "regex": pattern_runtime.config.regex,
                        "status": "regex-no-match",
                    }
                )
            continue

        matched_patterns += 1
        if trace is not None:
            trace["matched_patterns"] = matched_patterns
        groups = {key: value for key, value in match.groupdict().items() if value is not None}
        groups_for_trace = dict(groups)
        season = _select_season(show, pattern_runtime.config.season_selector, groups)
        if not season:
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
            if trace_attempts is not None:
                trace_attempts.append(
                    {
                        "pattern": descriptor,
                        "regex": pattern_runtime.config.regex,
                        "status": "season-unresolved",
                        "season_selector": {
                            "mode": selector.mode,
                            "group": selector.group,
                            "value": candidate_value,
                        },
                        "groups": groups_for_trace,
                        "message": message,
                    }
                )
            continue

        pattern_runtime.session_lookup = _build_session_lookup(pattern_runtime.config, season)
        episode_trace: Dict[str, Any] = {}
        episode = _select_episode(
            pattern_runtime.config,
            season,
            pattern_runtime.session_lookup,
            groups,
            trace=episode_trace,
        )
        if not episode:
            selector = pattern_runtime.config.episode_selector
            raw_value = groups.get(selector.group)
            normalized_value = normalize_token(raw_value) if raw_value else None
            attempted_tokens = groups.pop("_attempted_session_tokens", None)
            attempted_display = ""
            if attempted_tokens:
                max_items = 5
                display_items = list(attempted_tokens[:max_items])
                if len(attempted_tokens) > max_items:
                    display_items.append("…")
                attempted_display = f", attempted={'; '.join(display_items)}"
            message = (
                f"{descriptor}: episode not resolved "
                f"(group={selector.group!r}, raw_value={raw_value!r}, normalized={normalized_value!r}, "
                f"season='{season.title}', candidates={summarize_episode_candidates(season)}, "
                f"groups={summarize_groups(groups)}{attempted_display})"
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
            if trace_attempts is not None:
                trace_entry = {
                    "pattern": descriptor,
                    "regex": pattern_runtime.config.regex,
                    "status": "episode-unresolved",
                    "season": {
                        "title": season.title,
                        "round_number": season.round_number,
                        "display_number": season.display_number,
                    },
                    "episode_selector": {
                        "group": selector.group,
                        "allow_fallback_to_title": selector.allow_fallback_to_title,
                    },
                    "groups": groups_for_trace,
                    "message": message,
                }
                if episode_trace:
                    trace_entry["episode_trace"] = episode_trace
                trace_attempts.append(trace_entry)
            continue

        result = {
            "season": season,
            "episode": episode,
            "pattern": pattern_runtime.config,
            "groups": groups,
        }
        if trace_attempts is not None:
            trace_entry = {
                "pattern": descriptor,
                "regex": pattern_runtime.config.regex,
                "status": "matched",
                "groups": groups_for_trace,
                "season": {
                    "title": season.title,
                    "round_number": season.round_number,
                    "display_number": season.display_number,
                },
                "episode": {
                    "title": episode.title,
                    "index": episode.index,
                    "display_number": episode.display_number,
                },
            }
            if episode_trace:
                trace_entry["episode_trace"] = episode_trace
            trace_attempts.append(trace_entry)
            trace["status"] = "matched"
            trace["result"] = {
                "season": trace_entry["season"],
                "episode": trace_entry["episode"],
                "pattern": descriptor,
            }
        return result
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
        if trace is not None:
            trace["status"] = "unresolved"
    elif matched_patterns == 0:
        LOGGER.debug("File %s did not match any configured patterns", filename)
        record("ignored", "Did not match any configured patterns")
        if trace is not None:
            trace["status"] = "no-match"
    return None
