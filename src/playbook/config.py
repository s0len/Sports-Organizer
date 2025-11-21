from __future__ import annotations

import dataclasses
import datetime as dt
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .pattern_templates import expand_regex_with_tokens, load_builtin_pattern_sets
from .utils import load_yaml_file


@dataclass(slots=True)
class SeasonSelector:
    mode: str = "round"  # round | key | title | sequential
    group: Optional[str] = None
    offset: int = 0
    mapping: Dict[str, int] = field(default_factory=dict)
    aliases: Dict[str, str] = field(default_factory=dict)


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
        return re.compile(self.regex, re.IGNORECASE)


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
class NotificationSettings:
    batch_daily: bool = False
    flush_time: dt.time = field(default_factory=lambda: dt.time(hour=0, minute=0))
    targets: List[Dict[str, Any]] = field(default_factory=list)
    throttle: Dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class WatcherSettings:
    enabled: bool = False
    paths: List[str] = field(default_factory=list)
    include: List[str] = field(default_factory=list)
    ignore: List[str] = field(default_factory=list)
    debounce_seconds: float = 5.0
    reconcile_interval: int = 900


@dataclass(slots=True)
class KometaTriggerSettings:
    enabled: bool = False
    namespace: str = "media"
    cronjob_name: str = "kometa-sport"
    job_name_prefix: str = "kometa-sport-triggered-by-playbook"


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
    discord_webhook_url: Optional[str] = None
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    file_watcher: WatcherSettings = field(default_factory=WatcherSettings)
    kometa_trigger: KometaTriggerSettings = field(default_factory=KometaTriggerSettings)


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
        aliases={str(k): str(v) for k, v in data.get("aliases", {}).items()},
    )
    return selector


def _build_episode_selector(data: Dict[str, Any]) -> EpisodeSelector:
    return EpisodeSelector(
        group=data.get("group", "session"),
        allow_fallback_to_title=bool(data.get("allow_fallback_to_title", True)),
    )


def _build_pattern_config(data: Dict[str, Any]) -> PatternConfig:
    raw_regex = str(data["regex"])
    pattern = PatternConfig(
        regex=expand_regex_with_tokens(raw_regex),
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


def _build_sport_config(
    data: Dict[str, Any],
    defaults: DestinationTemplates,
    global_link_mode: str,
    pattern_sets: Dict[str, List[Dict[str, Any]]],
) -> SportConfig:
    metadata = _build_metadata_config(data["metadata"])
    destination = _build_destination_templates(data.get("destination"), defaults)
    pattern_definitions: List[Dict[str, Any]] = []

    pattern_set_refs = data.get("pattern_sets", []) or []
    if not isinstance(pattern_set_refs, list):
        raise ValueError(
            f"Sport '{data.get('id')}' must declare 'pattern_sets' as a list when provided"
        )

    for set_name in pattern_set_refs:
        if not isinstance(set_name, str):
            raise ValueError(
                f"Sport '{data.get('id')}' pattern set names must be strings, got '{set_name}'"
            )
        if set_name not in pattern_sets:
            raise ValueError(f"Unknown pattern set '{set_name}' referenced by sport '{data.get('id')}'")
        pattern_definitions.extend(deepcopy(pattern_sets[set_name]))

    custom_patterns = data.get("file_patterns", []) or []
    pattern_definitions.extend(deepcopy(custom_patterns))

    patterns = sorted((
        _build_pattern_config(pattern) for pattern in pattern_definitions
    ), key=lambda cfg: cfg.priority)

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


def _deep_update(target: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict):
            existing = target.get(key)
            if isinstance(existing, dict):
                _deep_update(existing, value)
            else:
                target[key] = deepcopy(value)
        elif isinstance(value, list):
            target[key] = deepcopy(value)
        else:
            target[key] = value
    return target


def _expand_sport_variants(sport_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    variants: List[Dict[str, Any]] = sport_data.get("variants", [])
    if not variants:
        return [sport_data]

    base = {key: deepcopy(value) for key, value in sport_data.items() if key != "variants"}
    expanded: List[Dict[str, Any]] = []

    base_id = base.get("id")
    if not base_id:
        raise ValueError("Sport entries with variants must define a base 'id'")

    for variant in variants:
        combined = deepcopy(base)
        _deep_update(combined, {key: value for key, value in variant.items() if key not in {"id_suffix", "year"}})

        variant_id = variant.get("id")
        variant_year = variant.get("year")
        variant_suffix = variant.get("id_suffix") or variant_year

        if variant_id:
            combined_id = variant_id
        elif variant_suffix:
            combined_id = f"{base_id}_{variant_suffix}"
        else:
            raise ValueError(f"Variant for sport '{base_id}' must define 'id', 'id_suffix', or 'year'")

        combined["id"] = combined_id

        if "name" not in combined:
            base_name = base.get("name", base_id)
            if "name" in variant:
                combined["name"] = variant["name"]
            elif variant_year is not None:
                combined["name"] = f"{base_name} {variant_year}"
            elif variant_suffix:
                combined["name"] = f"{base_name} {variant_suffix}"
            else:
                combined["name"] = base_name

        combined.pop("year", None)
        combined.pop("id_suffix", None)
        combined.pop("variants", None)

        expanded.append(combined)

    return expanded


def _parse_time_of_day(value: Any, *, field_name: str) -> dt.time:
    if value is None:
        return dt.time(hour=0, minute=0)
    if isinstance(value, dt.time):
        return value
    if not isinstance(value, str):
        raise ValueError(f"'{field_name}' must be provided as HH:MM or HH:MM:SS")

    parts = value.strip().split(":")
    if len(parts) not in {2, 3}:
        raise ValueError(f"'{field_name}' must be formatted as HH:MM or HH:MM:SS")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError as exc:  # noqa: PERF203
        raise ValueError(f"'{field_name}' components must be integers") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(f"'{field_name}' contains out-of-range values")

    return dt.time(hour=hour, minute=minute, second=second)


def _ensure_string_list(value: Any, *, field_name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"'{field_name}' must be provided as a list of strings")
    result: List[str] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, str):
            raise ValueError(f"'{field_name}[{index}]' must be a string")
        cleaned = entry.strip()
        if cleaned:
            result.append(cleaned)
    return result


def _build_watcher_settings(data: Dict[str, Any]) -> WatcherSettings:
    if not data:
        return WatcherSettings()
    if not isinstance(data, dict):
        raise ValueError("'file_watcher' must be provided as a mapping when specified")

    try:
        debounce = float(data.get("debounce_seconds", 5.0))
    except (TypeError, ValueError) as exc:  # noqa: PERF203
        raise ValueError("'file_watcher.debounce_seconds' must be a number") from exc
    if debounce < 0:
        raise ValueError("'file_watcher.debounce_seconds' must be greater than or equal to 0")

    try:
        reconcile = int(data.get("reconcile_interval", 900))
    except (TypeError, ValueError) as exc:  # noqa: PERF203
        raise ValueError("'file_watcher.reconcile_interval' must be an integer") from exc
    if reconcile < 0:
        raise ValueError("'file_watcher.reconcile_interval' must be greater than or equal to 0")

    return WatcherSettings(
        enabled=bool(data.get("enabled", False)),
        paths=_ensure_string_list(data.get("paths"), field_name="file_watcher.paths"),
        include=_ensure_string_list(data.get("include"), field_name="file_watcher.include"),
        ignore=_ensure_string_list(data.get("ignore"), field_name="file_watcher.ignore"),
        debounce_seconds=debounce,
        reconcile_interval=reconcile,
    )


def _build_kometa_trigger_settings(data: Dict[str, Any]) -> KometaTriggerSettings:
    if not data:
        return KometaTriggerSettings()
    if not isinstance(data, dict):
        raise ValueError("'kometa_trigger' must be provided as a mapping when specified")

    namespace_raw = str(data.get("namespace", "media")).strip()
    cronjob_raw = str(data.get("cronjob_name", "kometa-sport")).strip()

    namespace = namespace_raw or "media"
    cronjob_name = cronjob_raw or "kometa-sport"
    default_prefix = f"{cronjob_name}-triggered-by-playbook"

    job_name_prefix = str(data.get("job_name_prefix", default_prefix)).strip() or default_prefix

    return KometaTriggerSettings(
        enabled=bool(data.get("enabled", False)),
        namespace=namespace,
        cronjob_name=cronjob_name,
        job_name_prefix=job_name_prefix,
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

    raw_webhook = data.get("discord_webhook_url")
    if isinstance(raw_webhook, str):
        discord_webhook_url = raw_webhook.strip() or None
    else:
        discord_webhook_url = raw_webhook if raw_webhook else None

    notifications_raw = data.get("notifications", {}) or {}
    if not isinstance(notifications_raw, dict):
        raise ValueError("'notifications' must be provided as a mapping when specified")

    try:
        flush_time = _parse_time_of_day(
            notifications_raw.get("flush_time", "00:00"),
            field_name="notifications.flush_time",
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    targets_raw = notifications_raw.get("targets", []) or []
    if not isinstance(targets_raw, list):
        raise ValueError("'notifications.targets' must be provided as a list when specified")
    targets: List[Dict[str, Any]] = []
    for entry in targets_raw:
        if not isinstance(entry, dict):
            raise ValueError("Each entry in 'notifications.targets' must be a mapping")
        target_type = entry.get("type")
        if not isinstance(target_type, str):
            raise ValueError("Notification target entries must include a string 'type'")
        normalized_entry: Dict[str, Any] = {str(k): v for k, v in entry.items()}
        normalized_entry["type"] = target_type.strip().lower()
        targets.append(normalized_entry)

    throttle_raw = notifications_raw.get("throttle", {}) or {}
    if not isinstance(throttle_raw, dict):
        raise ValueError("'notifications.throttle' must be provided as a mapping when specified")
    throttle: Dict[str, int] = {}
    for key, value in throttle_raw.items():
        try:
            throttle[str(key)] = int(value)
        except (TypeError, ValueError) as exc:  # noqa: PERF203
            raise ValueError(f"'notifications.throttle[{key}]' must be an integer") from exc

    notifications = NotificationSettings(
        batch_daily=bool(notifications_raw.get("batch_daily", False)),
        flush_time=flush_time,
        targets=targets,
        throttle=throttle,
    )

    source_dir = Path(data.get("source_dir", "/data/source")).expanduser()
    destination_dir = Path(data.get("destination_dir", "/data/destination")).expanduser()
    cache_dir = Path(data.get("cache_dir", "/data/cache")).expanduser()
    watcher_settings = _build_watcher_settings(data.get("file_watcher", {}) or {})
    kometa_trigger = _build_kometa_trigger_settings(data.get("kometa_trigger", {}) or {})

    return Settings(
        source_dir=source_dir,
        destination_dir=destination_dir,
        cache_dir=cache_dir,
        dry_run=bool(data.get("dry_run", False)),
        skip_existing=bool(data.get("skip_existing", True)),
        poll_interval=int(data.get("poll_interval", 0)),
        default_destination=destination_defaults,
        link_mode=data.get("link_mode", "hardlink"),
        discord_webhook_url=discord_webhook_url,
        notifications=notifications,
        file_watcher=watcher_settings,
        kometa_trigger=kometa_trigger,
    )


def load_config(path: Path) -> AppConfig:
    data = load_yaml_file(path)

    builtin_pattern_sets = {
        name: deepcopy(patterns) for name, patterns in load_builtin_pattern_sets().items()
    }
    user_pattern_sets = data.get("pattern_sets", {}) or {}
    if not isinstance(user_pattern_sets, dict):
        raise ValueError("'pattern_sets' must be defined as a mapping of name -> list of patterns")

    for name, patterns in user_pattern_sets.items():
        if patterns is None:
            builtin_pattern_sets[name] = []
            continue
        if not isinstance(patterns, list):
            raise ValueError(
                f"Pattern set '{name}' must be a list of pattern definitions"
            )
        builtin_pattern_sets[name] = deepcopy(patterns)

    settings = _build_settings(data.get("settings", {}))
    defaults = settings.default_destination
    sports_raw: Iterable[Dict[str, Any]] = data.get("sports", [])

    expanded_sports: List[Dict[str, Any]] = []
    for sport_data in sports_raw:
        for variant_data in _expand_sport_variants(sport_data):
            expanded_sports.append(variant_data)

    sports = []
    for sport_data in expanded_sports:
        if "metadata" not in sport_data:
            raise ValueError(f"Sport '{sport_data.get('id')}' is missing required 'metadata' section")
        sports.append(_build_sport_config(sport_data, defaults, settings.link_mode, builtin_pattern_sets))

    return AppConfig(settings=settings, sports=sports)
