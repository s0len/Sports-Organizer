from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import yaml

from .config import MetadataConfig, Settings
from .models import Episode, Season, Show
from .utils import ensure_directory, sha1_of_text

LOGGER = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, dt.datetime):
        return obj.isoformat(timespec="seconds")
    if isinstance(obj, dt.date):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = sha1_of_text(url)
    return cache_dir / "metadata" / f"{digest}.json"


def _load_cached_metadata(cache_file: Path, ttl_hours: int, *, allow_expired: bool = False) -> Optional[Dict[str, Any]]:
    if not cache_file.exists():
        return None

    try:
        with cache_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:  # noqa: BLE001
        LOGGER.warning("Failed to load cache file %s", cache_file)
        return None

    fetched_at_raw = payload.get("fetched_at")
    if not fetched_at_raw:
        return None

    try:
        fetched_at = dt.datetime.fromisoformat(fetched_at_raw)
    except (TypeError, ValueError):
        LOGGER.warning("Invalid fetched_at value in cache file %s", cache_file)
        return None

    if fetched_at.tzinfo is None:
        # Backwards compatibility with caches written prior to UTC-aware timestamps.
        fetched_at = fetched_at.replace(tzinfo=dt.timezone.utc)

    age = dt.datetime.now(dt.timezone.utc) - fetched_at
    if age > dt.timedelta(hours=ttl_hours) and not allow_expired:
        return None

    return payload.get("content")


def _store_cache(cache_file: Path, content: Dict[str, Any]) -> None:
    ensure_directory(cache_file.parent)
    payload = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "content": content,
    }
    with cache_file.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=_json_default)


class MetadataFingerprintStore:
    """Tracks a lightweight hash of each sport's metadata to detect updates."""

    def __init__(self, cache_dir: Path, filename: str = "metadata-digests.json") -> None:
        self.cache_dir = cache_dir
        self.filename = filename
        self.path = self.cache_dir / "state" / self.filename
        self._fingerprints: Dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to load metadata fingerprint cache %s: %s", self.path, exc)
            return

        if not isinstance(payload, dict):
            LOGGER.warning("Ignoring malformed metadata fingerprint cache %s", self.path)
            return

        fingerprints: Dict[str, str] = {}
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, str):
                fingerprints[key] = value
        self._fingerprints = fingerprints

    def get(self, key: str) -> Optional[str]:
        return self._fingerprints.get(key)

    def update(self, key: str, fingerprint: str) -> bool:
        if self._fingerprints.get(key) == fingerprint:
            return False
        self._fingerprints[key] = fingerprint
        self._dirty = True
        return True

    def remove(self, key: str) -> None:
        if key in self._fingerprints:
            del self._fingerprints[key]
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return

        ensure_directory(self.path.parent)
        try:
            with self.path.open("w", encoding="utf-8") as handle:
                json.dump(self._fingerprints, handle, ensure_ascii=False, indent=2, sort_keys=True)
            self._dirty = False
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to write metadata fingerprint cache %s: %s", self.path, exc)


def compute_show_fingerprint(show: Show, metadata_cfg: MetadataConfig) -> str:
    """Compute a hash representing the effective metadata for a sport."""

    fingerprint_payload = {
        "show_key": metadata_cfg.show_key,
        "season_overrides": metadata_cfg.season_overrides,
        "metadata": show.metadata,
    }
    serialized = json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return sha1_of_text(serialized)


class MetadataFetchError(RuntimeError):
    """Raised when metadata cannot be retrieved from remote or cache."""


def fetch_metadata(metadata: MetadataConfig, settings: Settings) -> Dict[str, Any]:
    cache_file = _cache_path(settings.cache_dir, metadata.url)
    cached = _load_cached_metadata(cache_file, metadata.ttl_hours)
    if cached is not None:
        LOGGER.debug("Using cached metadata for %s", metadata.url)
        return cached

    LOGGER.info("Fetching metadata from %s", metadata.url)
    try:
        response = requests.get(metadata.url, headers=metadata.headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:  # noqa: BLE001 - propagate cleanly below
        LOGGER.warning("Failed to fetch metadata from %s: %s", metadata.url, exc)
        stale = _load_cached_metadata(cache_file, metadata.ttl_hours, allow_expired=True)
        if stale is not None:
            LOGGER.info("Using stale cached metadata for %s", metadata.url)
            return stale
        raise MetadataFetchError(f"Unable to fetch metadata from {metadata.url}") from exc
    content = yaml.safe_load(response.text)
    if not isinstance(content, dict):
        raise ValueError(f"Unexpected metadata structure at {metadata.url}")

    if settings.dry_run:
        LOGGER.debug("Dry-run: skipping metadata cache write for %s", metadata.url)
    else:
        _store_cache(cache_file, content)
    return content


def _season_round_from_sort_title(sort_title: Optional[str]) -> Optional[int]:
    if not sort_title:
        return None
    parts = sort_title.split("_", 1)
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def _season_round_from_title(title: str) -> Optional[int]:
    # Attempt to parse leading digits
    for chunk in title.split():
        if chunk.isdigit():
            return int(chunk)
        if chunk.strip("#").isdigit():
            return int(chunk.strip("#"))
    return None


def _parse_originally_available(value: Optional[str]) -> Optional[dt.date]:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).split(" ")[0])
    except ValueError:
        return None


def _season_sort_value(key: Any) -> Tuple[int, str]:
    key_str = str(key)
    try:
        return (0, f"{int(key_str):04d}")
    except ValueError:
        numeric_prefix = re.match(r"^(\d+)", key_str)
        if numeric_prefix:
            return (0, f"{int(numeric_prefix.group(1)):04d}-{key_str}")
        return (1, key_str)


class MetadataNormalizer:
    """Normalize remote YAML into structured objects."""

    def __init__(self, metadata_cfg: MetadataConfig) -> None:
        self.metadata_cfg = metadata_cfg

    def load_show(self, raw: Dict[str, Any]) -> Show:
        catalog: Dict[str, Any] = raw.get("metadata") or raw
        if not isinstance(catalog, dict):
            raise ValueError("Metadata file must contain a mapping under 'metadata'")

        if self.metadata_cfg.show_key:
            key = self.metadata_cfg.show_key
            show_raw = catalog.get(key)
            if not show_raw:
                raise KeyError(f"Show key '{key}' not found in metadata")
        else:
            if len(catalog) != 1:
                raise ValueError("Multiple shows found; specify 'show_key' in config")
            key, show_raw = next(iter(catalog.items()))

        title = show_raw.get("title", key)
        summary = show_raw.get("summary")
        seasons_raw = show_raw.get("seasons", {})

        seasons = self._parse_seasons(seasons_raw)
        show = Show(key=key, title=title, summary=summary, seasons=seasons, metadata=show_raw)
        return show

    def _parse_seasons(self, seasons_raw: Any) -> List[Season]:
        if isinstance(seasons_raw, dict):
            season_items: Iterable[Tuple[str, Any]] = seasons_raw.items()
        elif isinstance(seasons_raw, list):
            season_items = enumerate(seasons_raw)
        else:
            raise ValueError("Unexpected season data structure")

        season_list = sorted(season_items, key=lambda item: _season_sort_value(item[0]))
        seasons: List[Season] = []
        for index, (key, season_raw) in enumerate(season_list):
            if isinstance(season_raw, (list, tuple)):
                # Some metadata might supply list of episodes directly.
                season_dict = {"episodes": season_raw}
            else:
                season_dict = season_raw or {}

            title = season_dict.get("title", str(key))
            summary = season_dict.get("summary")
            sort_title = season_dict.get("sort_title") or season_dict.get("slug")
            episodes_raw = season_dict.get("episodes", [])

            episodes = self._parse_episodes(episodes_raw)

            season = Season(
                key=str(key),
                title=title,
                summary=summary,
                index=index + 1,
                episodes=episodes,
                sort_title=sort_title,
                metadata=season_dict,
            )

            round_override = self.metadata_cfg.season_overrides.get(title, {}).get("round")
            display_override = self.metadata_cfg.season_overrides.get(title, {}).get("season_number")

            season.round_number = (
                int(round_override)
                if round_override is not None
                else (
                    _season_round_from_sort_title(sort_title)
                    or self._season_number_from_key(str(key))
                    or _season_round_from_title(title)
                )
            )
            season.display_number = (
                int(display_override)
                if display_override is not None
                else season.round_number
            )
            seasons.append(season)

        return seasons

    @staticmethod
    def _season_number_from_key(key: str) -> Optional[int]:
        try:
            return int(key)
        except ValueError:
            return None

    def _parse_episodes(self, episodes_raw: Any) -> List[Episode]:
        if isinstance(episodes_raw, dict):
            episodes_items = sorted(episodes_raw.items(), key=lambda item: _season_sort_value(item[0]))
        else:
            episodes_items = list(enumerate(episodes_raw))

        episodes: List[Episode] = []
        for index, (_, episode_raw) in enumerate(episodes_items):
            episode_dict = episode_raw or {}
            title = episode_dict.get("title") or episode_dict.get("name") or f"Episode {index+1}"
            summary = episode_dict.get("summary")
            originally_available = _parse_originally_available(episode_dict.get("originally_available"))
            display_number = episode_dict.get("episode_number")
            aliases = episode_dict.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [aliases]
            episode = Episode(
                title=title,
                summary=summary,
                originally_available=originally_available,
                index=index + 1,
                metadata=episode_dict,
                display_number=int(display_number) if display_number is not None else None,
                aliases=list(aliases),
            )
            episodes.append(episode)
        return episodes


def load_show(settings: Settings, metadata_cfg: MetadataConfig) -> Show:
    raw = fetch_metadata(metadata_cfg, settings)
    normalizer = MetadataNormalizer(metadata_cfg)
    return normalizer.load_show(raw)
