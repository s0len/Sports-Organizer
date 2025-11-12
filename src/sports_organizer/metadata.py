from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

import requests
import yaml

from .config import MetadataConfig, Settings
from .models import Episode, Season, Show
from .utils import ensure_directory, sha1_of_text

if TYPE_CHECKING:
    from .cache import MetadataHttpCache
else:  # pragma: no cover - runtime typing fallback
    MetadataHttpCache = Any  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

MAX_FETCH_RETRIES = 3


class MetadataFetchStatistics:
    """Thread-safe accumulator for metadata cache metrics."""

    def __init__(self) -> None:
        self.cache_hits = 0
        self.cache_misses = 0
        self.network_requests = 0
        self.not_modified = 0
        self.stale_used = 0
        self.failures = 0
        self._lock = threading.Lock()

    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self.cache_misses += 1

    def record_network_request(self) -> None:
        with self._lock:
            self.network_requests += 1

    def record_not_modified(self) -> None:
        with self._lock:
            self.not_modified += 1

    def record_stale_used(self) -> None:
        with self._lock:
            self.stale_used += 1

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "network_requests": self.network_requests,
                "not_modified": self.not_modified,
                "stale_used": self.stale_used,
                "failures": self.failures,
            }

    def has_activity(self) -> bool:
        with self._lock:
            return any(
                (
                    self.cache_hits,
                    self.cache_misses,
                    self.network_requests,
                    self.not_modified,
                    self.stale_used,
                    self.failures,
                )
            )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, dt.datetime):
        return obj.isoformat(timespec="seconds")
    if isinstance(obj, dt.date):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = sha1_of_text(url)
    return cache_dir / "metadata" / f"{digest}.json"


def _season_identifier(season: Season) -> str:
    key = getattr(season, "key", None)
    if key:
        return str(key)
    if season.display_number is not None:
        return f"display:{season.display_number}"
    return f"index:{season.index}"


def _episode_identifier(episode: Episode) -> str:
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


def _clean_season_metadata(metadata: Any) -> Any:
    if not isinstance(metadata, dict):
        return metadata
    cleaned = dict(metadata)
    cleaned.pop("episodes", None)
    return cleaned


def _clean_episode_metadata(metadata: Any) -> Any:
    if not isinstance(metadata, dict):
        return metadata
    return dict(metadata)


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


@dataclass(slots=True)
class ShowFingerprint:
    digest: str
    season_hashes: Dict[str, str]
    episode_hashes: Dict[str, Dict[str, str]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "digest": self.digest,
            "seasons": dict(self.season_hashes),
            "episodes": {season: dict(episodes) for season, episodes in self.episode_hashes.items()},
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ShowFingerprint":
        digest_raw = payload.get("digest")
        digest = str(digest_raw) if digest_raw is not None else ""
        seasons_raw = payload.get("seasons") or {}
        season_hashes = {str(key): str(value) for key, value in seasons_raw.items()}
        episodes_raw = payload.get("episodes") or {}
        episode_hashes: Dict[str, Dict[str, str]] = {}
        for season_key, mapping in episodes_raw.items():
            if not isinstance(mapping, dict):
                continue
            episode_hashes[str(season_key)] = {str(ep_key): str(ep_hash) for ep_key, ep_hash in mapping.items()}
        return cls(digest=digest, season_hashes=season_hashes, episode_hashes=episode_hashes)


@dataclass(slots=True)
class MetadataChangeResult:
    updated: bool
    changed_seasons: Set[str]
    changed_episodes: Dict[str, Set[str]]
    invalidate_all: bool = False


class MetadataFingerprintStore:
    """Tracks a lightweight hash of each sport's metadata to detect updates."""

    def __init__(self, cache_dir: Path, filename: str = "metadata-digests.json") -> None:
        self.cache_dir = cache_dir
        self.filename = filename
        self.path = self.cache_dir / "state" / self.filename
        self._fingerprints: Dict[str, ShowFingerprint] = {}
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

        fingerprints: Dict[str, ShowFingerprint] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            if isinstance(value, str):
                fingerprints[key] = ShowFingerprint(digest=value, season_hashes={}, episode_hashes={})
            elif isinstance(value, dict):
                try:
                    fingerprints[key] = ShowFingerprint.from_dict(value)
                except Exception:  # pragma: no cover - defensive
                    LOGGER.debug("Skipping malformed metadata fingerprint entry for %s", key)
            else:
                LOGGER.debug("Skipping malformed metadata fingerprint entry for %s", key)
        self._fingerprints = fingerprints

    def get(self, key: str) -> Optional[ShowFingerprint]:
        return self._fingerprints.get(key)

    def update(self, key: str, fingerprint: ShowFingerprint) -> MetadataChangeResult:
        existing = self._fingerprints.get(key)
        if existing is None:
            self._fingerprints[key] = fingerprint
            self._dirty = True
            return MetadataChangeResult(
                updated=True,
                changed_seasons=set(),
                changed_episodes={},
                invalidate_all=False,
            )

        if existing.digest == fingerprint.digest:
            if (
                existing.season_hashes != fingerprint.season_hashes
                or existing.episode_hashes != fingerprint.episode_hashes
            ):
                self._fingerprints[key] = fingerprint
                self._dirty = True
            return MetadataChangeResult(
                updated=False,
                changed_seasons=set(),
                changed_episodes={},
                invalidate_all=False,
            )

        if (
            not existing.season_hashes and not existing.episode_hashes
        ) or (
            not existing.episode_hashes and any(fingerprint.episode_hashes.values())
        ):
            self._fingerprints[key] = fingerprint
            self._dirty = True
            return MetadataChangeResult(
                updated=True,
                changed_seasons=set(),
                changed_episodes={},
                invalidate_all=True,
            )

        existing_seasons = existing.season_hashes
        new_seasons = fingerprint.season_hashes

        changed_seasons: Set[str] = set()
        for season_key, old_hash in existing_seasons.items():
            new_hash = new_seasons.get(season_key)
            if new_hash is None or new_hash != old_hash:
                changed_seasons.add(season_key)

        existing_episodes = existing.episode_hashes
        new_episodes = fingerprint.episode_hashes
        changed_episodes: Dict[str, Set[str]] = {}

        for season_key, previous_episode_map in existing_episodes.items():
            if season_key in changed_seasons:
                continue
            new_episode_map = new_episodes.get(season_key)
            if new_episode_map is None:
                changed_seasons.add(season_key)
                continue

            episode_changes: Set[str] = set()
            for episode_key, old_hash in previous_episode_map.items():
                new_hash = new_episode_map.get(episode_key)
                if new_hash is None or new_hash != old_hash:
                    episode_changes.add(episode_key)

            if episode_changes:
                changed_episodes[season_key] = episode_changes

        self._fingerprints[key] = fingerprint
        self._dirty = True
        return MetadataChangeResult(
            updated=True,
            changed_seasons=changed_seasons,
            changed_episodes=changed_episodes,
            invalidate_all=False,
        )

    def remove(self, key: str) -> None:
        if key in self._fingerprints:
            del self._fingerprints[key]
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return

        ensure_directory(self.path.parent)
        serialised = {key: fp.to_dict() for key, fp in self._fingerprints.items()}
        try:
            with self.path.open("w", encoding="utf-8") as handle:
                json.dump(serialised, handle, ensure_ascii=False, indent=2, sort_keys=True)
            self._dirty = False
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to write metadata fingerprint cache %s: %s", self.path, exc)


def compute_show_fingerprint(show: Show, metadata_cfg: MetadataConfig) -> ShowFingerprint:
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
    digest = sha1_of_text(serialized)

    season_hashes: Dict[str, str] = {}
    episode_hashes: Dict[str, Dict[str, str]] = {}
    for season in show.seasons:
        season_key = _season_identifier(season)
        season_payload = {
            "key": season_key,
            "title": season.title,
            "summary": season.summary,
            "index": season.index,
            "display_number": season.display_number,
            "round_number": season.round_number,
            "sort_title": season.sort_title,
            "metadata": _clean_season_metadata(season.metadata),
        }
        season_serialized = json.dumps(
            season_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
        season_hashes[season_key] = sha1_of_text(season_serialized)
        episode_hash_map: Dict[str, str] = {}
        for episode in season.episodes:
            episode_payload = {
                "title": episode.title,
                "summary": episode.summary,
                "index": episode.index,
                "display_number": episode.display_number,
                "aliases": episode.aliases,
                "originally_available": (
                    episode.originally_available.isoformat() if episode.originally_available else None
                ),
                "metadata": _clean_episode_metadata(episode.metadata),
            }
            episode_serialized = json.dumps(
                episode_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=_json_default,
            )
            episode_key = _episode_identifier(episode)
            episode_hash_map[episode_key] = sha1_of_text(episode_serialized)
        episode_hashes[season_key] = episode_hash_map

    return ShowFingerprint(digest=digest, season_hashes=season_hashes, episode_hashes=episode_hashes)


class MetadataFetchError(RuntimeError):
    """Raised when metadata cannot be retrieved from remote or cache."""


def fetch_metadata(
    metadata: MetadataConfig,
    settings: Settings,
    *,
    http_cache: Optional[MetadataHttpCache] = None,
    stats: Optional[MetadataFetchStatistics] = None,
) -> Dict[str, Any]:
    cache_file = _cache_path(settings.cache_dir, metadata.url)
    cached = _load_cached_metadata(cache_file, metadata.ttl_hours)
    if cached is not None:
        LOGGER.debug("Using cached metadata for %s", metadata.url)
        if stats:
            stats.record_cache_hit()
        return cached

    if stats:
        stats.record_cache_miss()

    stale = _load_cached_metadata(cache_file, metadata.ttl_hours, allow_expired=True)
    LOGGER.info("Fetching metadata from %s", metadata.url)
    headers = dict(metadata.headers or {})

    if http_cache:
        cached_entry = http_cache.get(metadata.url)
        if cached_entry:
            if cached_entry.etag and "If-None-Match" not in headers:
                headers["If-None-Match"] = cached_entry.etag
            if cached_entry.last_modified and "If-Modified-Since" not in headers:
                headers["If-Modified-Since"] = cached_entry.last_modified

    response = None
    last_exception: Optional[requests.RequestException] = None
    backoff = 1.0

    for attempt in range(MAX_FETCH_RETRIES):
        try:
            response = requests.get(metadata.url, headers=headers or None, timeout=30)
            if stats:
                stats.record_network_request()
            break
        except requests.RequestException as exc:  # noqa: BLE001
            last_exception = exc
            if attempt >= MAX_FETCH_RETRIES - 1:
                response = None
                break
            LOGGER.debug(
                "Metadata fetch attempt %s failed for %s: %s (retrying)",
                attempt + 1,
                metadata.url,
                exc,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 8.0)

    if response is None:
        if stale is not None:
            if stats:
                stats.record_stale_used()
            LOGGER.info("Using stale cached metadata for %s", metadata.url)
            return stale
        if stats:
            stats.record_failure()
        raise MetadataFetchError(f"Unable to fetch metadata from {metadata.url}") from last_exception

    status_code = response.status_code

    if http_cache:
        http_cache.update(
            metadata.url,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            status_code=status_code,
        )

    if status_code == 304:
        if stats:
            stats.record_not_modified()
        if stale is not None:
            LOGGER.debug("Metadata not modified for %s", metadata.url)
            return stale
        if stats:
            stats.record_failure()
        raise MetadataFetchError(
            f"Received 304 Not Modified for {metadata.url} without cached copy",
        )

    try:
        response.raise_for_status()
    except requests.RequestException as exc:  # noqa: BLE001
        LOGGER.warning("Failed to fetch metadata from %s: %s", metadata.url, exc)
        if stale is not None:
            if stats:
                stats.record_stale_used()
            LOGGER.info("Using stale cached metadata for %s", metadata.url)
            return stale
        if stats:
            stats.record_failure()
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


def load_show(
    settings: Settings,
    metadata_cfg: MetadataConfig,
    *,
    http_cache: Optional[MetadataHttpCache] = None,
    stats: Optional[MetadataFetchStatistics] = None,
) -> Show:
    raw = fetch_metadata(metadata_cfg, settings, http_cache=http_cache, stats=stats)
    normalizer = MetadataNormalizer(metadata_cfg)
    return normalizer.load_show(raw)
