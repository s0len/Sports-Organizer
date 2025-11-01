from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .config import SportConfig


@dataclass(slots=True)
class Episode:
    title: str
    summary: Optional[str]
    originally_available: Optional[dt.date]
    index: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    display_number: Optional[int] = None
    aliases: List[str] = field(default_factory=list)


@dataclass(slots=True)
class Season:
    key: str
    title: str
    summary: Optional[str]
    index: int
    episodes: List[Episode]
    sort_title: Optional[str] = None
    display_number: Optional[int] = None
    round_number: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Show:
    key: str
    title: str
    summary: Optional[str]
    seasons: List[Season]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SportFileMatch:
    source_path: Path
    destination_path: Path
    show: Show
    season: Season
    episode: Episode
    context: Dict[str, Any]
    sport: "SportConfig"


@dataclass(slots=True)
class ProcessingStats:
    processed: int = 0
    skipped: int = 0
    ignored: int = 0
    errors: List[str] = field(default_factory=list)

    def register_processed(self) -> None:
        self.processed += 1

    def register_skipped(self, reason: str) -> None:
        self.skipped += 1
        self.errors.append(reason)

    def register_ignored(self) -> None:
        self.ignored += 1
