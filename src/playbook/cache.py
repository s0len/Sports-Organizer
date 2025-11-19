from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Optional

from .metadata import MetadataChangeResult
from .utils import ensure_directory

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class MetadataHttpEntry:
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    status_code: Optional[int] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "status_code": self.status_code,
        }


class MetadataHttpCache:
    """Persists HTTP cache metadata (ETag / Last-Modified) for metadata feeds."""

    def __init__(self, cache_dir: Path, filename: str = "metadata-http.json") -> None:
        self.cache_dir = cache_dir
        self.filename = filename
        self.path = self.cache_dir / "state" / self.filename
        self._entries: Dict[str, MetadataHttpEntry] = {}
        self._dirty = False
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to load metadata HTTP cache %s: %s", self.path, exc)
            return

        if not isinstance(payload, dict):
            LOGGER.warning("Ignoring malformed metadata HTTP cache %s", self.path)
            return

        entries: Dict[str, MetadataHttpEntry] = {}
        for url, data in payload.items():
            if not isinstance(url, str) or not isinstance(data, dict):
                continue
            entries[url] = MetadataHttpEntry(
                etag=data.get("etag"),
                last_modified=data.get("last_modified"),
                status_code=data.get("status_code"),
            )
        self._entries = entries

    def get(self, url: str) -> Optional[MetadataHttpEntry]:
        with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return None
            return replace(entry)

    def update(
        self,
        url: str,
        *,
        etag: Optional[str],
        last_modified: Optional[str],
        status_code: Optional[int],
    ) -> None:
        with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                entry = MetadataHttpEntry()
                self._entries[url] = entry
            entry.etag = etag or entry.etag
            entry.last_modified = last_modified or entry.last_modified
            entry.status_code = status_code
            self._dirty = True

    def clear_failure(self, url: str) -> None:
        with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return
            entry.status_code = None
            self._dirty = True

    def invalidate(self, url: str) -> None:
        with self._lock:
            if url in self._entries:
                del self._entries[url]
                self._dirty = True

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            ensure_directory(self.path.parent)
            serialised = {url: entry.to_dict() for url, entry in self._entries.items()}
            try:
                with self.path.open("w", encoding="utf-8") as handle:
                    json.dump(serialised, handle, indent=2, ensure_ascii=False, sort_keys=True)
                self._dirty = False
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("Failed to write metadata HTTP cache %s: %s", self.path, exc)


@dataclass(slots=True)
class CachedFileRecord:
    mtime_ns: int
    size: int
    checksum: Optional[str] = None
    destination: Optional[str] = None
    sport_id: Optional[str] = None
    season_key: Optional[str] = None
    episode_key: Optional[str] = None


@dataclass(slots=True)
class ProcessedFileCache:
    cache_dir: Path
    cache_filename: str = "processed-files.json"
    cache_path: Path = field(init=False)
    _records: Dict[str, CachedFileRecord] = field(default_factory=dict, init=False)
    _dirty: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.cache_path = self.cache_dir / "state" / self.cache_filename
        self._load()

    def _load(self) -> None:
        if not self.cache_path.exists():
            return

        try:
            with self.cache_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to load processed cache %s: %s", self.cache_path, exc)
            return

        records: Dict[str, CachedFileRecord] = {}
        for key, value in payload.items():
            try:
                records[key] = CachedFileRecord(
                    mtime_ns=int(value["mtime_ns"]),
                    size=int(value["size"]),
                    checksum=value.get("checksum"),
                    destination=str(value.get("destination") or "") or None,
                    sport_id=value.get("sport_id"),
                    season_key=value.get("season_key"),
                    episode_key=value.get("episode_key"),
                )
            except Exception:  # noqa: BLE001
                LOGGER.debug("Skipping malformed cache entry for %s", key)
        self._records = records

    def _serialize(self) -> Dict[str, Dict[str, object]]:
        payload: Dict[str, Dict[str, object]] = {}
        for key, record in self._records.items():
            payload[key] = {
                "mtime_ns": record.mtime_ns,
                "size": record.size,
                "checksum": record.checksum,
                "destination": record.destination,
                "sport_id": record.sport_id,
                "season_key": record.season_key,
                "episode_key": record.episode_key,
            }
        return payload

    def save(self) -> None:
        if not self._dirty:
            return

        ensure_directory(self.cache_path.parent)
        try:
            with self.cache_path.open("w", encoding="utf-8") as handle:
                json.dump(self._serialize(), handle, indent=2, ensure_ascii=False)
            self._dirty = False
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to write processed cache %s: %s", self.cache_path, exc)

    def snapshot(self) -> Dict[str, CachedFileRecord]:
        return {
            key: CachedFileRecord(
                mtime_ns=record.mtime_ns,
                size=record.size,
                checksum=record.checksum,
                destination=record.destination,
                sport_id=record.sport_id,
                season_key=record.season_key,
                episode_key=record.episode_key,
            )
            for key, record in self._records.items()
        }

    def prune_missing_sources(self) -> None:
        removed = False
        for key in list(self._records.keys()):
            if not Path(key).exists():
                del self._records[key]
                removed = True
        if removed:
            self._dirty = True

    def is_processed(self, source_path: Path) -> bool:
        record = self._records.get(str(source_path))
        if not record:
            return False

        try:
            stat = source_path.stat()
        except FileNotFoundError:
            return False

        if stat.st_mtime_ns != record.mtime_ns or stat.st_size != record.size:
            return False

        if record.destination:
            destination_path = Path(record.destination)
            if not destination_path.exists():
                return False

        return True

    def mark_processed(
        self,
        source_path: Path,
        destination_path: Optional[Path] = None,
        *,
        sport_id: Optional[str] = None,
        season_key: Optional[str] = None,
        episode_key: Optional[str] = None,
        checksum: Optional[str] = None,
    ) -> None:
        try:
            stat = source_path.stat()
        except FileNotFoundError:
            LOGGER.debug("Source missing when marking processed: %s", source_path)
            return

        destination_str = str(destination_path) if destination_path else None
        self._records[str(source_path)] = CachedFileRecord(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            checksum=checksum,
            destination=destination_str,
            sport_id=sport_id,
            season_key=season_key,
            episode_key=episode_key,
        )
        self._dirty = True

    def clear(self) -> None:
        if self._records:
            self._records.clear()
            self._dirty = True

    def remove_by_metadata_changes(
        self,
        changes: Dict[str, MetadataChangeResult],
    ) -> Dict[str, CachedFileRecord]:
        if not changes:
            return {}

        removed: Dict[str, CachedFileRecord] = {}

        for source, record in list(self._records.items()):
            sport_id = record.sport_id

            if sport_id is None:
                # Legacy entries without ownership information; drop them whenever changes occur
                removed[source] = self._records.pop(source)
                continue

            if sport_id not in changes:
                continue

            change = changes[sport_id]

            if change.invalidate_all:
                removed[source] = self._records.pop(source)
                continue

            season_key = record.season_key
            episode_key = record.episode_key

            if season_key is None:
                if change.changed_seasons or change.changed_episodes:
                    removed[source] = self._records.pop(source)
                continue

            if season_key in change.changed_seasons:
                removed[source] = self._records.pop(source)
                continue

            episodes = change.changed_episodes.get(season_key)
            if episodes and (episode_key is None or episode_key in episodes):
                removed[source] = self._records.pop(source)

        if removed:
            self._dirty = True

        return removed

    def get_checksum(self, source_path: Path) -> Optional[str]:
        record = self._records.get(str(source_path))
        return record.checksum if record else None

