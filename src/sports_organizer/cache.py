from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .utils import ensure_directory

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CachedFileRecord:
    mtime_ns: int
    size: int
    destination: Optional[str] = None


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
                    destination=str(value.get("destination") or ""),
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
                "destination": record.destination,
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

    def mark_processed(self, source_path: Path, destination_path: Optional[Path] = None) -> None:
        try:
            stat = source_path.stat()
        except FileNotFoundError:
            LOGGER.debug("Source missing when marking processed: %s", source_path)
            return

        destination_str = str(destination_path) if destination_path else None
        self._records[str(source_path)] = CachedFileRecord(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            destination=destination_str,
        )
        self._dirty = True

    def clear(self) -> None:
        if self._records:
            self._records.clear()
            self._dirty = True

