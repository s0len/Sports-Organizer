from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, List, Set

from rich.progress import Progress

from .config import AppConfig, SportConfig
from .matcher import PatternRuntime, compile_patterns, match_file_to_episode
from .metadata import load_show
from .models import ProcessingStats, Show, SportFileMatch
from .templating import render_template
from .utils import ensure_directory, link_file, sanitize_component, slugify

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SportRuntime:
    sport: SportConfig
    show: Show
    patterns: List[PatternRuntime]
    extensions: Set[str]


class Processor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        ensure_directory(self.config.settings.destination_dir)
        ensure_directory(self.config.settings.cache_dir)

    def _load_sports(self) -> List[SportRuntime]:
        runtimes: List[SportRuntime] = []
        for sport in self.config.sports:
            if not sport.enabled:
                LOGGER.info("Skipping disabled sport %s", sport.id)
                continue
            LOGGER.info("Loading metadata for %s", sport.name)
            show = load_show(self.config.settings, sport.metadata)
            patterns = compile_patterns(sport)
            extensions = {ext.lower() for ext in sport.source_extensions}
            runtimes.append(SportRuntime(sport=sport, show=show, patterns=patterns, extensions=extensions))
        return runtimes

    def run_once(self) -> ProcessingStats:
        runtimes = self._load_sports()
        stats = ProcessingStats()

        source_files = list(self._gather_source_files())
        LOGGER.info("Discovered %d candidate files", len(source_files))

        with Progress() as progress:
            task_id = progress.add_task("Processing", total=len(source_files))
            for source_path in source_files:
                handled = self._process_single_file(source_path, runtimes, stats)
                if not handled:
                    stats.register_ignored()
                progress.advance(task_id, 1)

        LOGGER.info(
            "Summary: %d processed, %d skipped, %d ignored", stats.processed, stats.skipped, stats.ignored
        )
        if stats.errors:
            for error in stats.errors:
                LOGGER.error(error)
        return stats

    def _gather_source_files(self) -> Iterable[Path]:
        root = self.config.settings.source_dir
        if not root.exists():
            LOGGER.warning("Source directory %s does not exist", root)
            return []

        for path in root.rglob("*"):
            if path.is_file():
                yield path

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
    ) -> bool:
        for runtime in runtimes:
            if source_path.suffix.lower() not in runtime.extensions:
                continue
            if not self._matches_globs(source_path, runtime.sport):
                continue

            detection = match_file_to_episode(source_path.name, runtime.sport, runtime.show, runtime.patterns)
            if not detection:
                continue

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
                context=context,
                sport=runtime.sport,
            )

            self._handle_match(match, stats)
            return True
        return False

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

        if settings.skip_existing and destination.exists():
            LOGGER.info("Skipping existing destination %s", destination)
            stats.register_skipped(f"Destination exists: {destination}")
            return

        LOGGER.info(
            "%s -> %s (S%sE%s)",
            match.source_path.name,
            destination,
            match.context.get("season_number"),
            match.context.get("episode_number"),
        )

        if settings.dry_run:
            stats.register_processed()
            return

        result = link_file(match.source_path, destination, mode=link_mode)
        if result.created:
            stats.register_processed()
        else:
            stats.register_skipped(f"Failed to link {match.source_path} -> {destination}: {result.reason}")
