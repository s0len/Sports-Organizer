from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

from rich.progress import Progress

from .cache import ProcessedFileCache
from .config import AppConfig, SportConfig
from .matcher import PatternRuntime, compile_patterns, match_file_to_episode
from .metadata import (
    MetadataChangeResult,
    MetadataFetchError,
    MetadataFingerprintStore,
    compute_show_fingerprint,
    load_show,
)
from .models import ProcessingStats, Show, SportFileMatch
from .notifications import DiscordNotifier
from .templating import render_template
from .utils import ensure_directory, link_file, sanitize_component, slugify, normalize_token

LOGGER = logging.getLogger(__name__)

SAMPLE_FILENAME_PATTERN = re.compile(r"(?<![a-z0-9])sample(?![a-z0-9])")


@dataclass(slots=True)
class SportRuntime:
    sport: SportConfig
    show: Show
    patterns: List[PatternRuntime]
    extensions: Set[str]


class Processor:
    def __init__(self, config: AppConfig, *, enable_notifications: bool = True) -> None:
        self.config = config
        if not self.config.settings.dry_run:
            ensure_directory(self.config.settings.destination_dir)
            ensure_directory(self.config.settings.cache_dir)
        self.processed_cache = ProcessedFileCache(self.config.settings.cache_dir)
        self.metadata_fingerprints = MetadataFingerprintStore(self.config.settings.cache_dir)
        settings = self.config.settings
        webhook_url = settings.discord_webhook_url if enable_notifications else None
        self.notifier = DiscordNotifier(
            webhook_url,
            cache_dir=settings.cache_dir,
            settings=settings.notifications,
        )
        self._previous_summary: Optional[Tuple[int, int, int]] = None
        self._metadata_changed_sports: List[Tuple[str, str]] = []
        self._metadata_change_map: Dict[str, MetadataChangeResult] = {}
        self._stale_destinations: Dict[str, Path] = {}

    @staticmethod
    def _format_log(event: str, fields: Optional[Mapping[str, object]] = None) -> str:
        lines = [event]
        if fields:
            items = list(fields.items())
            width = max((len(str(key)) for key, _ in items), default=0)
            for key, value in items:
                text = "" if value is None else str(value)
                lines.append(f"  {str(key):<{width}}: {text}")
        return "\n".join(lines)

    @staticmethod
    def _format_inline_log(event: str, fields: Optional[Mapping[str, object]] = None) -> str:
        if not fields:
            return event

        items = list(fields.items())
        width = max((len(str(key)) for key, _ in items), default=0)
        formatted = []
        for key, value in items:
            text = "" if value is None else str(value)
            formatted.append(f"{str(key):<{width}}: {text}")
        return f"{event} | " + " | ".join(formatted)

    def _load_sports(self) -> List[SportRuntime]:
        runtimes: List[SportRuntime] = []
        self._metadata_changed_sports = []
        self._metadata_change_map = {}
        for sport in self.config.sports:
            if not sport.enabled:
                LOGGER.debug(self._format_log("Skipping Disabled Sport", {"Sport": sport.id}))
                continue
            LOGGER.debug(self._format_log("Loading Metadata", {"Sport": sport.name}))
            try:
                show = load_show(self.config.settings, sport.metadata)
            except MetadataFetchError as exc:
                LOGGER.error(
                    self._format_log(
                        "Failed To Fetch Metadata",
                        {
                            "Sport": sport.id,
                            "Name": sport.name,
                            "Error": exc,
                        },
                    )
                )
                continue
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.error(
                    self._format_log(
                        "Failed To Load Metadata",
                        {
                            "Sport": sport.id,
                            "Name": sport.name,
                            "Error": exc,
                        },
                    )
                )
                continue
            patterns = compile_patterns(sport)
            extensions = {ext.lower() for ext in sport.source_extensions}

            try:
                fingerprint = compute_show_fingerprint(show, sport.metadata)
            except Exception as exc:  # pragma: no cover - defensive, should not happen
                LOGGER.warning(
                    self._format_log(
                        "Failed To Compute Metadata Fingerprint",
                        {
                            "Sport": sport.id,
                            "Error": exc,
                        },
                    )
                )
            else:
                change = self.metadata_fingerprints.update(sport.id, fingerprint)
                if change.updated:
                    self._metadata_changed_sports.append((sport.id, sport.name))
                    self._metadata_change_map[sport.id] = change

            runtimes.append(SportRuntime(sport=sport, show=show, patterns=patterns, extensions=extensions))
        return runtimes

    def clear_processed_cache(self) -> None:
        if self.config.settings.dry_run:
            LOGGER.debug(
                self._format_log(
                    "Dry-Run: Skipping Processed Cache Clear",
                    {"Cache": self.processed_cache.cache_path.parent},
                )
            )
            return

        self.processed_cache.clear()
        self.processed_cache.save()
        LOGGER.debug(self._format_log("Processed File Cache Cleared"))

    def run_once(self) -> ProcessingStats:
        self.processed_cache.prune_missing_sources()
        runtimes = self._load_sports()
        self._stale_destinations = {}
        if self._metadata_changed_sports:
            labels = ", ".join(
                f"{sport_id} ({sport_name})" if sport_name and sport_name != sport_id else sport_id
                for sport_id, sport_name in self._metadata_changed_sports
            )
            LOGGER.info(
                self._format_log(
                    "Metadata Updated",
                    {
                        "Sports": labels or "(unknown)",
                    },
                )
            )
            removed_records = self.processed_cache.remove_by_metadata_changes(self._metadata_change_map)
            self._stale_destinations = {
                source: Path(record.destination)
                for source, record in removed_records.items()
                if record.destination
            }
        stats = ProcessingStats()

        try:
            all_source_files = list(self._gather_source_files(stats))
            filtered_source_files: List[Path] = []
            skipped_by_cache = 0
            for source_path in all_source_files:
                if self.processed_cache.is_processed(source_path):
                    skipped_by_cache += 1
                    LOGGER.debug(
                        self._format_log(
                            "Skipping Previously Processed File",
                            {"Path": source_path},
                        )
                    )
                    continue
                filtered_source_files.append(source_path)

            file_count = len(filtered_source_files)
            if LOGGER.isEnabledFor(logging.DEBUG):
                LOGGER.debug(
                    self._format_log(
                        "Discovered Candidate Files",
                        {
                            "Total": len(all_source_files),
                            "Skipped Via Cache": skipped_by_cache,
                        },
                    )
                )

            with Progress(disable=not LOGGER.isEnabledFor(logging.DEBUG)) as progress:
                task_id = progress.add_task("Processing", total=file_count)
                for source_path in filtered_source_files:
                    handled, diagnostics = self._process_single_file(source_path, runtimes, stats)
                    if not handled:
                        if self._should_suppress_sample_ignored(source_path):
                            stats.register_ignored(suppressed_reason="sample")
                        else:
                            detail = self._format_ignored_detail(source_path, diagnostics)
                            stats.register_ignored(detail)
                    progress.advance(task_id, 1)

            summary_counts = (stats.processed, stats.skipped, stats.ignored)
            summary_changed = summary_counts != self._previous_summary
            should_log_summary = (LOGGER.isEnabledFor(logging.DEBUG) or self._has_activity(stats)) and summary_changed
            if should_log_summary:
                LOGGER.info(
                    self._format_inline_log(
                        "Summary",
                        {
                            "Processed": stats.processed,
                            "Skipped": stats.skipped,
                            "Ignored": stats.ignored,
                        },
                    )
                )
            self._previous_summary = summary_counts
            if stats.errors:
                for error in stats.errors:
                    LOGGER.error(
                        self._format_log(
                            "Processing Error",
                            {"Detail": error},
                        )
                    )

            has_details = self._has_detailed_activity(stats)
            has_issues = bool(stats.errors or stats.warnings)
            if LOGGER.isEnabledFor(logging.DEBUG):
                if has_details or has_issues:
                    level = logging.INFO if has_issues else logging.DEBUG
                    self._log_detailed_summary(stats, level=level)
            elif has_issues:
                self._log_detailed_summary(stats)
            return stats
        finally:
            if not self.config.settings.dry_run:
                self.processed_cache.save()
                self.metadata_fingerprints.save()

    def _gather_source_files(self, stats: Optional[ProcessingStats] = None) -> Iterable[Path]:
        root = self.config.settings.source_dir
        if not root.exists():
            LOGGER.warning(
                self._format_log(
                    "Source Directory Missing",
                    {"Path": root},
                )
            )
            if stats is not None:
                stats.register_warning(f"Source directory missing: {root}")
            return []

        for path in root.rglob("*"):
            if not path.is_file():
                continue

            skip_reason = self._skip_reason_for_source_file(path)
            if skip_reason:
                LOGGER.debug(
                    self._format_log(
                        "Skipping Source File",
                        {
                            "Source": path,
                            "Reason": skip_reason,
                        },
                    )
                )
                continue

            yield path

    @staticmethod
    def _skip_reason_for_source_file(path: Path) -> Optional[str]:
        name = path.name
        if name.startswith("._") and len(name) > 2:
            return "macOS resource fork (._ prefix)"
        return None

    def _matches_globs(self, path: Path, sport: SportConfig) -> bool:
        if not sport.source_globs:
            return True
        filename = path.name
        return any(fnmatch(filename, pattern) for pattern in sport.source_globs)

    def _process_single_file(
        self,
        source_path: Path,
        runtimes: List[SportRuntime],
        stats: ProcessingStats,
    ) -> Tuple[bool, List[Tuple[str, str]]]:
        suffix = source_path.suffix.lower()
        matching_runtimes = [runtime for runtime in runtimes if suffix in runtime.extensions]
        ignored_reasons: List[Tuple[str, str]] = []

        if not matching_runtimes:
            message = f"No configured sport accepts extension '{suffix or '<no extension>'}'"
            ignored_reasons.append(("ignored", message))
            LOGGER.debug(
                self._format_log(
                    "Ignoring File",
                    {
                        "Source": source_path,
                        "Reason": message,
                    },
                )
            )
            return False, ignored_reasons

        for runtime in matching_runtimes:
            if not self._matches_globs(source_path, runtime.sport):
                patterns = runtime.sport.source_globs or ["*"]
                message = f"Excluded by source_globs {patterns}"
                tagged_message = f"{runtime.sport.id}: {message}"
                ignored_reasons.append(("ignored", tagged_message))
                LOGGER.debug(
                    self._format_log(
                        "Ignoring File For Sport",
                        {
                            "Source": source_path.name,
                            "Sport": runtime.sport.id,
                            "Reason": message,
                        },
                    )
                )
                continue

            detection_messages: List[Tuple[str, str]] = []
            detection = match_file_to_episode(
                source_path.name,
                runtime.sport,
                runtime.show,
                runtime.patterns,
                diagnostics=detection_messages,
            )
            if detection:
                season = detection["season"]
                episode = detection["episode"]
                pattern = detection["pattern"]
                groups = detection["groups"]

                context = self._build_context(runtime, source_path, season, episode, groups)
                destination = self._build_destination(runtime, pattern, context)

                match = SportFileMatch(
                    source_path=source_path,
                    destination_path=destination,
                    show=runtime.show,
                    season=season,
                    episode=episode,
                    pattern=pattern,
                    context=context,
                    sport=runtime.sport,
                )

                self._handle_match(match, stats)
                return True, []

            if not detection_messages:
                detection_messages.append(("ignored", "No matching pattern resolved to an episode"))

            for severity, message in detection_messages:
                tagged_message = f"{runtime.sport.id}: {message}"
                ignored_reasons.append((severity, tagged_message))
                LOGGER.debug(
                    self._format_log(
                        "Ignoring Detection",
                        {
                            "Source": source_path.name,
                            "Sport": runtime.sport.id,
                            "Severity": severity,
                            "Reason": message,
                        },
                    )
                )
                if severity == "warning":
                    stats.register_warning(f"{source_path.name}: {tagged_message}")
                elif severity == "error":
                    stats.errors.append(f"{source_path.name}: {tagged_message}")

        return False, ignored_reasons

    @staticmethod
    def _should_suppress_sample_ignored(source_path: Path) -> bool:
        name = source_path.name.lower()
        return bool(SAMPLE_FILENAME_PATTERN.search(name))

    def _format_ignored_detail(self, source_path: Path, diagnostics: List[Tuple[str, str]]) -> str:
        if not diagnostics:
            return f"{source_path.name}: ignored with no diagnostics"

        collapsed: List[str] = []
        for severity, message in diagnostics:
            prefix = severity.upper()
            collapsed.append(f"[{prefix}] {message}")

        unique = list(dict.fromkeys(collapsed))
        details = "; ".join(unique)
        return f"{source_path.name}: {details}"

    def _log_detailed_summary(self, stats: ProcessingStats, *, level: int = logging.INFO) -> None:
        def _format_lines(items: List[str]) -> str:
            if not items:
                return "    (none)"
            unique_items = list(dict.fromkeys(items))
            return "\n".join(f"    - {item}" for item in unique_items)

        ignored_details = stats.ignored_details
        suppressed_non_video_count = 0
        if level >= logging.INFO:
            filtered_ignored: List[str] = []
            for detail in ignored_details:
                if "No configured sport accepts extension" in detail:
                    suppressed_non_video_count += 1
                else:
                    filtered_ignored.append(detail)
            ignored_details = filtered_ignored

        suppressed_labels: List[str] = []
        if suppressed_non_video_count:
            suppressed_labels.append(f"{suppressed_non_video_count} suppressed non-video")
        suppressed_samples = stats.suppressed_ignored_samples
        if suppressed_samples:
            label = "sample" if suppressed_samples == 1 else "samples"
            suppressed_labels.append(f"{suppressed_samples} suppressed {label}")

        ignored_suffix = ""
        if suppressed_labels:
            ignored_suffix = ", " + ", ".join(suppressed_labels)

        summary_lines = [
            "Detailed Summary",
            f"  Errors ({len(stats.errors)}):",
            _format_lines(stats.errors),
            f"  Warnings ({len(stats.warnings)}):",
            _format_lines(stats.warnings),
            f"  Skipped ({len(stats.skipped_details)}):",
            _format_lines(stats.skipped_details),
            f"  Ignored ({len(ignored_details)}{ignored_suffix}):",
            _format_lines(ignored_details),
        ]

        LOGGER.log(level, "\n".join(summary_lines))

    @staticmethod
    def _has_activity(stats: ProcessingStats) -> bool:
        return bool(
            stats.processed
            or stats.skipped
            or stats.ignored
            or stats.errors
            or stats.warnings
        )

    @staticmethod
    def _has_detailed_activity(stats: ProcessingStats) -> bool:
        return bool(
            stats.errors
            or stats.warnings
            or stats.skipped_details
            or stats.ignored_details
        )

    def _notify_processed(self, match: SportFileMatch, destination_display: str) -> None:
        if not self.notifier.enabled:
            return
        try:
            self.notifier.notify_processed(match, destination_display=destination_display)
        except Exception as exc:  # pragma: no cover - defensive fallback
            LOGGER.debug(
                self._format_log(
                    "Failed To Send Notification",
                    {
                        "Sport": match.sport.id,
                        "Error": exc,
                    },
                )
            )

    def _build_context(self, runtime: SportRuntime, source_path: Path, season, episode, groups) -> Dict[str, object]:
        show = runtime.show
        sport = runtime.sport

        context: Dict[str, object] = {}
        context.update(groups)

        context.update(
            {
                "sport_id": sport.id,
                "sport_name": sport.name,
                "show_id": show.key,
                "show_key": show.key,
                "show_title": show.title,
                "season_key": season.key,
                "season_title": season.title,
                "season_index": season.index,
                "season_number": season.display_number or season.index,
                "season_round": season.round_number or season.display_number or season.index,
                "season_sort_title": season.sort_title or season.title,
                "season_slug": slugify(season.title),
                "episode_title": episode.title,
                "episode_index": episode.index,
                "episode_number": episode.display_number or episode.index,
                "episode_summary": episode.summary or "",
                "episode_slug": slugify(episode.title),
                "episode_originally_available": (
                    episode.originally_available.isoformat() if episode.originally_available else ""
                ),
                "originally_available": (
                    episode.originally_available.isoformat() if episode.originally_available else ""
                ),
                "extension": source_path.suffix.lstrip("."),
                "suffix": source_path.suffix,
                "source_filename": source_path.name,
                "source_stem": source_path.stem,
                "relative_source": str(source_path.relative_to(self.config.settings.source_dir)),
            }
        )

        year_match = re.search(r"(\d{4})", show.title)
        if year_match:
            context["season_year"] = int(year_match.group(1))

        return context

    def _build_destination(self, runtime: SportRuntime, pattern, context: Dict[str, object]) -> Path:
        settings = self.config.settings
        sport = runtime.sport

        destination_root_template = (
            pattern.destination_root_template
            or sport.destination.root_template
            or settings.default_destination.root_template
        )
        season_template = (
            pattern.season_dir_template
            or sport.destination.season_dir_template
            or settings.default_destination.season_dir_template
        )
        episode_template = (
            pattern.filename_template
            or sport.destination.episode_template
            or settings.default_destination.episode_template
        )

        root_component = sanitize_component(render_template(destination_root_template, context))
        season_component = sanitize_component(render_template(season_template, context))
        episode_filename = render_template(episode_template, context)
        episode_component = sanitize_component(episode_filename)

        destination = (
            settings.destination_dir
            / root_component
            / season_component
            / episode_component
        )
        return destination

    def _handle_match(self, match: SportFileMatch, stats: ProcessingStats) -> None:
        destination = match.destination_path
        settings = self.config.settings
        link_mode = (match.sport.link_mode or settings.link_mode).lower()
        source_key = str(match.source_path)
        old_destination = self._stale_destinations.get(source_key)
        cache_kwargs = {
            "sport_id": match.sport.id,
            "season_key": self._season_cache_key(match),
            "episode_key": self._episode_cache_key(match),
        }

        replace_existing = False
        if destination.exists():
            if settings.skip_existing:
                if self._should_overwrite_existing(match):
                    replace_existing = True
                else:
                    LOGGER.debug(
                        self._format_log(
                            "Skipping Existing Destination",
                            {
                                "Destination": destination,
                                "Source": match.source_path,
                            },
                        )
                    )
                    self._cleanup_old_destination(
                        source_key,
                        old_destination,
                        destination,
                        dry_run=settings.dry_run,
                    )
                    stats.register_skipped(
                        f"Destination exists: {destination} (source {match.source_path})",
                        is_error=False,
                    )
                    if not settings.dry_run:
                        self.processed_cache.mark_processed(match.source_path, destination, **cache_kwargs)
                    return

        if replace_existing:
            LOGGER.debug(
                self._format_log(
                    "Preparing To Replace Destination",
                    {"Destination": destination},
                )
            )
            if not settings.dry_run:
                try:
                    destination.unlink()
                except OSError as exc:
                    LOGGER.error(
                        self._format_log(
                            "Failed To Remove Destination",
                            {
                                "Destination": destination,
                                "Error": exc,
                            },
                        )
                    )
                    stats.register_skipped(
                        f"Failed to replace destination {destination}: {exc}",
                        is_error=True,
                    )
                    return

        destination_display = self._format_relative_destination(destination)

        LOGGER.info(
            self._format_log(
                "Processed",
                {
                    "Action": "replace" if replace_existing else "link",
                    "Sport": match.sport.id,
                    "Season": match.context.get("season_title"),
                    "Session": match.context.get("session"),
                    "Dest": destination_display,
                    "Src": match.source_path.name,
                },
            )
        )

        if LOGGER.isEnabledFor(logging.DEBUG):
            LOGGER.debug(
                self._format_log(
                    "Processing Details",
                    {
                        "Source": match.source_path,
                        "Destination": destination,
                        "Link Mode": link_mode,
                        "Replace": replace_existing,
                    },
                )
            )

        if settings.dry_run:
            stats.register_processed()
            return

        result = link_file(match.source_path, destination, mode=link_mode)
        if result.created:
            stats.register_processed()
            self.processed_cache.mark_processed(match.source_path, destination, **cache_kwargs)
            self._notify_processed(match, destination_display)
            self._cleanup_old_destination(
                source_key,
                old_destination,
                destination,
                dry_run=settings.dry_run,
            )
        else:
            stats.register_skipped(f"Failed to link {match.source_path} -> {destination}: {result.reason}")
            if result.reason == "destination-exists":
                self.processed_cache.mark_processed(match.source_path, destination, **cache_kwargs)
                self._cleanup_old_destination(
                    source_key,
                    old_destination,
                    destination,
                    dry_run=settings.dry_run,
                )

    def _should_overwrite_existing(self, match: SportFileMatch) -> bool:
        source_name = match.source_path.name.lower()
        if any(keyword in source_name for keyword in ("repack", "proper")):
            return True

        if "2160p" in source_name:
            return True

        session_raw = str(match.context.get("session") or "").strip()
        if not session_raw:
            return False

        session_specificity = self._specificity_score(session_raw)
        if session_specificity == 0:
            return False

        session_token = normalize_token(session_raw)
        alias_candidates = self._alias_candidates(match)

        baseline_scores = [
            self._specificity_score(alias)
            for alias in alias_candidates
            if normalize_token(alias) != session_token
        ]

        if not baseline_scores:
            return False

        return session_specificity > min(baseline_scores)

    def _alias_candidates(self, match: SportFileMatch) -> List[str]:
        candidates: List[str] = []

        canonical = match.episode.title
        if canonical:
            candidates.append(canonical)

        candidates.extend(match.episode.aliases)

        session_aliases = match.pattern.session_aliases
        if canonical in session_aliases:
            candidates.extend(session_aliases[canonical])
        else:
            canonical_token = normalize_token(canonical) if canonical else ""
            for key, aliases in session_aliases.items():
                if canonical_token and normalize_token(key) == canonical_token:
                    candidates.extend(aliases)
                    break

        # Deduplicate while preserving order and skip falsy values
        seen: Set[str] = set()
        unique_candidates: List[str] = []
        for value in candidates:
            if not value:
                continue
            if value not in seen:
                seen.add(value)
                unique_candidates.append(value)

        return unique_candidates

    @staticmethod
    def _specificity_score(value: str) -> int:
        if not value:
            return 0

        score = 0
        lower = value.lower()

        digit_count = sum(ch.isdigit() for ch in value)
        score += digit_count * 2

        score += lower.count(".") + lower.count("-") + lower.count("_")

        if re.search(r"\\bpart[\\s._-]*\\d+\\b", lower):
            score += 2
        if re.search(r"\\bstage[\\s._-]*\\d+\\b", lower):
            score += 1
        if re.search(r"\\b(?:heat|round|leg|match|session)[\\s._-]*\\d+\\b", lower):
            score += 1
        if re.search(r"(?:^|[\\s._-])(qf|sf|q|fp|sp)[\\s._-]*\\d+\\b", lower):
            score += 1

        spelled_markers = (
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "seventh",
            "eighth",
            "ninth",
            "tenth",
        )
        for marker in spelled_markers:
            if re.search(rf"\\b{marker}\\b", lower):
                score += 1

        return score

    @staticmethod
    def _season_cache_key(match: SportFileMatch) -> Optional[str]:
        season = match.season
        key = season.key
        if key is not None:
            return str(key)
        if season.display_number is not None:
            return f"display:{season.display_number}"
        return f"index:{season.index}"

    @staticmethod
    def _episode_cache_key(match: SportFileMatch) -> str:
        episode = match.episode
        metadata = episode.metadata or {}
        for field in ("id", "guid", "episode_id", "uuid"):
            value = metadata.get(field)
            if value:
                return f"{field}:{value}"
        if episode.display_number is not None:
            return f"display:{episode.display_number}"
        if episode.title:
            return f"title:{episode.title}"
        return f"index:{episode.index}"

    def _format_relative_destination(self, destination: Path) -> str:
        base = self.config.settings.destination_dir
        try:
            relative = destination.relative_to(base)
        except ValueError:
            return str(destination)
        return str(relative)

    def _cleanup_old_destination(
        self,
        source_key: str,
        old_destination: Optional[Path],
        new_destination: Path,
        *,
        dry_run: bool,
    ) -> None:
        if not old_destination:
            self._stale_destinations.pop(source_key, None)
            return

        if old_destination == new_destination:
            self._stale_destinations.pop(source_key, None)
            return

        if not old_destination.exists() or old_destination.is_dir():
            self._stale_destinations.pop(source_key, None)
            return

        if dry_run:
            LOGGER.debug(
                self._format_log(
                    "Dry-Run: Would Remove Obsolete Destination",
                    {
                        "Source": source_key,
                        "Old Destination": old_destination,
                        "Replaced With": self._format_relative_destination(new_destination),
                    },
                )
            )
            self._stale_destinations.pop(source_key, None)
            return

        try:
            old_destination.unlink()
        except OSError as exc:
            LOGGER.warning(
                self._format_log(
                    "Failed To Remove Obsolete Destination",
                    {
                        "Source": source_key,
                        "Old Destination": old_destination,
                        "Error": exc,
                    },
                )
            )
        else:
            LOGGER.info(
                self._format_log(
                    "Removed Obsolete Destination",
                    {
                        "Source": source_key,
                        "Removed": self._format_relative_destination(old_destination),
                        "Replaced With": self._format_relative_destination(new_destination),
                    },
                )
            )
        finally:
            self._stale_destinations.pop(source_key, None)
