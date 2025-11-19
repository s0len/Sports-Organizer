from __future__ import annotations

import fnmatch
import logging
import time
from pathlib import Path
from queue import Empty, Queue
from typing import List, Optional, Sequence, Set, TYPE_CHECKING

from .config import WatcherSettings

if TYPE_CHECKING:  # pragma: no cover
    from .processor import Processor

try:  # pragma: no cover - imported lazily during runtime
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - fallback when watchdog is missing
    FileSystemEventHandler = object  # type: ignore[misc, assignment]
    Observer = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)


class WatchdogUnavailableError(RuntimeError):
    """Raised when watchdog is not installed but watcher mode is enabled."""


class _FileChangeHandler(FileSystemEventHandler):  # type: ignore[misc]
    def __init__(self, queue: Queue[Path], include: Sequence[str], ignore: Sequence[str]) -> None:
        self._queue = queue
        self._include = list(include)
        self._ignore = list(ignore)

    def on_created(self, event) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        self._emit(Path(event.src_path))

    def on_modified(self, event) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        self._emit(Path(event.src_path))

    def on_moved(self, event) -> None:  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        self._emit(Path(event.dest_path))

    def _emit(self, path: Path) -> None:
        if not self._matches(path):
            return
        self._queue.put(path)

    def _matches(self, path: Path) -> bool:
        target = str(path)
        filename = path.name
        if self._include:
            if not any(fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(target, pattern) for pattern in self._include):
                return False
        if self._ignore:
            if any(fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(target, pattern) for pattern in self._ignore):
                return False
        return True


class FileWatcherLoop:
    """Watches the filesystem for changes and triggers processor runs."""

    def __init__(self, processor: "Processor", settings: WatcherSettings) -> None:
        if Observer is None:
            raise WatchdogUnavailableError(
                "Filesystem watcher mode requires the 'watchdog' dependency. Install via 'pip install watchdog'."
            )
        self._processor = processor
        self._settings = settings
        self._queue: "Queue[Path]" = Queue()
        self._handler = _FileChangeHandler(self._queue, settings.include, settings.ignore)
        self._observer = Observer()
        self._roots = self._resolve_roots()
        for root in self._roots:
            self._observer.schedule(self._handler, str(root), recursive=True)

    def run_forever(self) -> None:
        if not self._roots:
            LOGGER.warning("Filesystem watcher has no valid directories; using the source_dir as fallback.")
        self._observer.start()
        watched_str = ", ".join(str(path) for path in self._roots) or str(self._processor.config.settings.source_dir)
        LOGGER.info("Filesystem watcher monitoring: %s", watched_str)

        pending: Set[Path] = set()
        last_run = 0.0
        reconcile_interval = self._settings.reconcile_interval
        next_reconcile = time.monotonic() + reconcile_interval if reconcile_interval > 0 else None

        try:
            while True:
                try:
                    changed = self._queue.get(timeout=1.0)
                    pending.add(changed)
                except Empty:
                    pass

                now = time.monotonic()
                if pending and (now - last_run) >= self._settings.debounce_seconds:
                    self._run_processor(pending)
                    pending.clear()
                    last_run = now

                if next_reconcile is not None and now >= next_reconcile:
                    LOGGER.info("Filesystem watcher reconcile triggered; running a full scan.")
                    self._processor.run_once()
                    next_reconcile = now + reconcile_interval
        finally:
            self._observer.stop()
            self._observer.join(timeout=5)

    def _run_processor(self, pending: Set[Path]) -> None:
        sample = ", ".join(sorted({str(path.parent) for path in pending})[:3])
        LOGGER.info(
            "Detected %d filesystem change(s)%s; running processor once.",
            len(pending),
            f" near {sample}" if sample else "",
        )
        self._processor.run_once()

    def _resolve_roots(self) -> List[Path]:
        roots = self._settings.paths or []
        default_root = self._processor.config.settings.source_dir
        if not roots:
            roots = [str(default_root)]
        resolved: List[Path] = []
        for raw in roots:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = (default_root / path).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            resolved.append(path)
        return resolved


__all__ = ["FileWatcherLoop", "WatchdogUnavailableError"]

