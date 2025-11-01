from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import re

from .utils import load_yaml_file


@dataclass(slots=True)
class SeasonSelector:
    mode: str = "round"  # round | key | title | sequential
    group: Optional[str] = None
    offset: int = 0
    mapping: Dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class EpisodeSelector:
    group: str = "session"
    allow_fallback_to_title: bool = True


@dataclass(slots=True)
class PatternConfig:
    regex: str
    description: Optional[str] = None
    season_selector: SeasonSelector = field(default_factory=SeasonSelector)
    episode_selector: EpisodeSelector = field(default_factory=EpisodeSelector)
    session_aliases: Dict[str, List[str]] = field(default_factory=dict)
    metadata_filters: Dict[str, Any] = field(default_factory=dict)
    filename_template: Optional[str] = None
    season_dir_template: Optional[str] = None
    destination_root_template: Optional[str] = None
    priority: int = 100

    def compiled_regex(self) -> re.Pattern[str]:
        return re.compile(self.regex)


@dataclass(slots=True)
class MetadataConfig:
    url: str
    show_key: Optional[str] = None
    ttl_hours: int = 12
    headers: Dict[str, str] = field(default_factory=dict)
    season_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class DestinationTemplates:
    root_template: str = "{show_title}"
    season_dir_template: str = "{season_number:02d} {season_title}"
    episode_template: str = (
        "{show_title} - S{season_number:02d}E{episode_number:02d} - {episode_title}.{extension}"
    )


@dataclass(slots=True)
class SportConfig:
    id: str
    name: str
    enabled: bool = True
    metadata: MetadataConfig = field(default_factory=lambda: MetadataConfig(url=""))
    patterns: List[PatternConfig] = field(default_factory=list)
    destination: DestinationTemplates = field(default_factory=DestinationTemplates)
    source_globs: List[str] = field(default_factory=list)
    source_extensions: List[str] = field(
        default_factory=lambda: [".mkv", ".mp4", ".ts", ".m4v", ".avi"]
    )
    link_mode: str = "hardlink"
    allow_unmatched: bool = False


@dataclass(slots=True)
class Settings:
    source_dir: Path
    destination_dir: Path
    cache_dir: Path
    dry_run: bool = False
    skip_existing: bool = True
    poll_interval: int = 0
    default_destination: DestinationTemplates = field(default_factory=DestinationTemplates)
    link_mode: str = "hardlink"


@dataclass(slots=True)
class AppConfig:
    settings: Settings
    sports: List[SportConfig]


def _build_season_selector(data: Dict[str, Any]) -> SeasonSelector:
    selector = SeasonSelector(
        mode=data.get("mode", "round"),
        group=data.get("group"),
        offset=int(data.get("offset", 0)),
        mapping={str(k): int(v) for k, v in data.get("mapping", {}).items()},
    )
    return selector


def _build_episode_selector(data: Dict[str, Any]) -> EpisodeSelector:
    return EpisodeSelector(
        group=data.get("group", "session"),
        allow_fallback_to_title=bool(data.get("allow_fallback_to_title", True)),
    )


def _build_pattern_config(data: Dict[str, Any]) -> PatternConfig:
    pattern = PatternConfig(
        regex=data["regex"],
        description=data.get("description"),
        season_selector=_build_season_selector(data.get("season_selector", {})),
        episode_selector=_build_episode_selector(data.get("episode_selector", {})),
        session_aliases={key: list(value) for key, value in data.get("session_aliases", {}).items()},
        metadata_filters=data.get("metadata_filters", {}),
        filename_template=data.get("filename_template"),
        season_dir_template=data.get("season_dir_template"),
        destination_root_template=data.get("destination_root_template"),
        priority=int(data.get("priority", 100)),
    )
    return pattern


def _build_metadata_config(data: Dict[str, Any]) -> MetadataConfig:
    return MetadataConfig(
        url=data["url"],
        show_key=data.get("show_key"),
        ttl_hours=int(data.get("ttl_hours", 12)),
        headers={str(k): str(v) for k, v in data.get("headers", {}).items()},
        season_overrides=data.get("season_overrides", {}),
    )


def _build_destination_templates(data: Optional[Dict[str, Any]], defaults: DestinationTemplates) -> DestinationTemplates:
    if not data:
        return defaults

    return DestinationTemplates(
        root_template=data.get("root_template", defaults.root_template),
        season_dir_template=data.get("season_dir_template", defaults.season_dir_template),
        episode_template=data.get("episode_template", defaults.episode_template),
    )


def _build_sport_config(data: Dict[str, Any], defaults: DestinationTemplates, global_link_mode: str) -> SportConfig:
    metadata = _build_metadata_config(data["metadata"])
    destination = _build_destination_templates(data.get("destination"), defaults)
    patterns = sorted(
        (_build_pattern_config(pattern) for pattern in data.get("file_patterns", [])),
        key=lambda cfg: cfg.priority,
    )

    return SportConfig(
        id=data["id"],
        name=data.get("name", data["id"]),
        enabled=bool(data.get("enabled", True)),
        metadata=metadata,
        patterns=patterns,
        destination=destination,
        source_globs=list(data.get("source_globs", [])),
        source_extensions=list(data.get("source_extensions", [".mkv", ".mp4", ".ts", ".m4v", ".avi"])),
        link_mode=str(data.get("link_mode", global_link_mode)),
        allow_unmatched=bool(data.get("allow_unmatched", False)),
    )


def _build_settings(data: Dict[str, Any]) -> Settings:
    destination_defaults = DestinationTemplates(
        root_template=data.get("destination", {}).get("root_template", "{show_title}"),
        season_dir_template=data.get("destination", {}).get("season_dir_template", "{season_number:02d} {season_title}"),
        episode_template=data.get("destination", {}).get(
            "episode_template",
            "{show_title} - S{season_number:02d}E{episode_number:02d} - {episode_title}.{extension}",
        ),
    )

    return Settings(
        source_dir=Path(data.get("source_dir", "/data/source")).expanduser(),
        destination_dir=Path(data.get("destination_dir", "/data/destination")).expanduser(),
        cache_dir=Path(data.get("cache_dir", "/data/cache")).expanduser(),
        dry_run=bool(data.get("dry_run", False)),
        skip_existing=bool(data.get("skip_existing", True)),
        poll_interval=int(data.get("poll_interval", 0)),
        default_destination=destination_defaults,
        link_mode=data.get("link_mode", "hardlink"),
    )


def load_config(path: Path) -> AppConfig:
    data = load_yaml_file(path)

    settings = _build_settings(data.get("settings", {}))
    defaults = settings.default_destination
    sports_raw: Iterable[Dict[str, Any]] = data.get("sports", [])
    sports = [
        _build_sport_config(sport_data, defaults, settings.link_mode)
        for sport_data in sports_raw
    ]

    return AppConfig(settings=settings, sports=sports)
