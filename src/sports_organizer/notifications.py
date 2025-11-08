from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from requests import Response
from requests.exceptions import RequestException

from .models import SportFileMatch

LOGGER = logging.getLogger(__name__)


class DiscordNotifier:
    """Send Discord notifications for processed files."""

    def __init__(self, webhook_url: Optional[str]) -> None:
        self.webhook_url = webhook_url.strip() if isinstance(webhook_url, str) else None

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def notify_processed(self, match: SportFileMatch, *, destination_display: str) -> None:
        if not self.enabled:
            return

        payload = self._build_payload(match, destination_display)
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
        except RequestException as exc:
            LOGGER.warning("Failed to send Discord notification: %s", exc)
            return

        if response.status_code >= 400:
            LOGGER.warning(
                "Discord webhook responded with %s: %s",
                response.status_code,
                self._excerpt_response(response),
            )

    def _build_payload(self, match: SportFileMatch, destination_display: str) -> Dict[str, Any]:
        show_title = match.show.title
        season_display = str(match.context.get("season_title") or match.season.title or "Season")
        session_display = str(match.context.get("session") or match.episode.title or "Session")
        episode_display = str(match.context.get("episode_title") or match.episode.title or session_display)
        summary = match.context.get("episode_summary") or match.episode.summary

        embed_title = self._trim(f"{show_title} – {session_display}", 256)
        description: Optional[str]
        if summary:
            description = self._trim(str(summary), 2048)
        else:
            description = None

        fields = [
            self._embed_field("Sport", match.sport.name, inline=True),
            self._embed_field("Season", season_display, inline=True),
            self._embed_field("Session", session_display, inline=True),
            self._embed_field("Episode", episode_display, inline=True),
            self._embed_field("Destination", f"`{destination_display}`", inline=False),
            self._embed_field("Source", f"`{match.source_path.name}`", inline=False),
        ]

        embed: Dict[str, Any] = {
            "title": embed_title,
            "color": 0x5865F2,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "fields": [field for field in fields if field is not None],
            "footer": {"text": "Sports Organizer"},
        }
        if description:
            embed["description"] = description

        content = self._trim(
            f"New {match.sport.name} entry is ready: {episode_display}",
            limit=2000,
        )

        return {
            "content": content,
            "embeds": [embed],
        }

    def _embed_field(self, name: str, value: Optional[str], *, inline: bool) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        text = self._trim(str(value), 1024)
        if not text:
            return None
        return {
            "name": self._trim(name, 256),
            "value": text,
            "inline": inline,
        }

    @staticmethod
    def _trim(value: str, limit: int) -> str:
        stripped = value.strip()
        if len(stripped) <= limit:
            return stripped
        if limit <= 3:
            return stripped[:limit]
        return stripped[: limit - 3] + "..."

    @staticmethod
    def _excerpt_response(response: Response) -> str:
        try:
            text = response.text
        except Exception:  # pragma: no cover - defensive fallback
            return "<no response body>"
        return DiscordNotifier._trim(text or "<empty>", 200)

