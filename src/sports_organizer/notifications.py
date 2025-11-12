from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests import Response
from requests.exceptions import RequestException

from .config import NotificationSettings
from .models import SportFileMatch
from .utils import ensure_directory

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BatchRequest:
    action: str  # "POST" or "PATCH"
    sport_id: str
    sport_name: str
    bucket_date: date
    message_id: Optional[str]
    events: List[Dict[str, Any]]


class NotificationBatcher:
    """Persisted per-sport batches that group notifications by day."""

    def __init__(self, cache_dir: Path, settings: NotificationSettings) -> None:
        self._settings = settings
        self._path = cache_dir / "state" / "discord-batches.json"
        self._state: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def prepare_event(self, context: Dict[str, Any], now: datetime) -> BatchRequest:
        sport_id = context["sport_id"]
        sport_name = context["sport_name"]
        bucket = self._bucket_date(now)
        bucket_key = bucket.isoformat()

        entry = self._state.get(sport_id)
        if not entry or entry.get("bucket_date") != bucket_key:
            entry = {
                "bucket_date": bucket_key,
                "message_id": None,
                "sport_name": sport_name,
                "events": [],
            }

        event_payload = {
            "sport_id": sport_id,
            "sport_name": sport_name,
            "season": context.get("season"),
            "session": context["session"],
            "episode": context["episode"],
            "destination": context["destination"],
            "source": context["source"],
            "summary": context.get("summary"),
            "timestamp": now.isoformat(),
        }

        events = entry["events"]
        events.append(event_payload)
        entry["events"] = events
        entry["sport_name"] = sport_name
        entry["last_event_at"] = event_payload["timestamp"]

        self._state[sport_id] = entry
        self._dirty = True
        self._save()

        message_id = entry.get("message_id")
        action = "PATCH" if message_id else "POST"

        return BatchRequest(
            action=action,
            sport_id=sport_id,
            sport_name=sport_name,
            bucket_date=bucket,
            message_id=message_id,
            events=[dict(item) for item in events],
        )

    def register_message_id(self, sport_id: str, bucket_date: date, message_id: str) -> None:
        entry = self._state.get(sport_id)
        if not entry:
            return
        if entry.get("bucket_date") != bucket_date.isoformat():
            return
        if entry.get("message_id") == message_id:
            return

        entry["message_id"] = message_id
        self._dirty = True
        self._save()

    def _bucket_date(self, now: datetime) -> date:
        local_now = now.astimezone()
        flush_time = self._settings.flush_time
        if flush_time and local_now.time() < flush_time:
            return local_now.date() - timedelta(days=1)
        return local_now.date()

    def _load(self) -> None:
        if not self._path.exists():
            return

        try:
            with self._path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to load notification batch cache %s: %s", self._path, exc)
            return

        if not isinstance(payload, dict):
            LOGGER.warning("Ignoring malformed notification batch cache %s", self._path)
            return

        state: Dict[str, Dict[str, Any]] = {}
        for sport_id, entry in payload.items():
            if not isinstance(sport_id, str) or not isinstance(entry, dict):
                continue

            events_raw = entry.get("events") or []
            if not isinstance(events_raw, list):
                events_raw = []

            events: List[Dict[str, Any]] = []
            for item in events_raw:
                if not isinstance(item, dict):
                    continue
                events.append(
                    {
                        "sport_id": str(item.get("sport_id") or sport_id),
                        "sport_name": str(item.get("sport_name") or entry.get("sport_name") or ""),
                        "season": item.get("season"),
                        "session": str(item.get("session") or ""),
                        "episode": str(item.get("episode") or ""),
                        "destination": str(item.get("destination") or ""),
                        "source": str(item.get("source") or ""),
                        "summary": item.get("summary"),
                        "timestamp": str(item.get("timestamp") or ""),
                    }
                )

            state[sport_id] = {
                "bucket_date": str(entry.get("bucket_date") or ""),
                "message_id": entry.get("message_id"),
                "sport_name": str(entry.get("sport_name") or ""),
                "events": events,
                "last_event_at": str(entry.get("last_event_at") or ""),
            }

        self._state = state

    def _save(self) -> None:
        if not self._dirty:
            return

        ensure_directory(self._path.parent)
        try:
            with self._path.open("w", encoding="utf-8") as handle:
                json.dump(self._state, handle, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to write notification batch cache %s: %s", self._path, exc)
            return

        self._dirty = False


class DiscordNotifier:
    """Send Discord notifications for processed files."""

    def __init__(
        self,
        webhook_url: Optional[str],
        *,
        cache_dir: Path,
        settings: NotificationSettings,
    ) -> None:
        self.webhook_url = webhook_url.strip() if isinstance(webhook_url, str) else None
        self._settings = settings
        self._batcher: Optional[NotificationBatcher]
        if self.enabled and settings.batch_daily:
            self._batcher = NotificationBatcher(cache_dir, settings)
        else:
            self._batcher = None

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def notify_processed(self, match: SportFileMatch, *, destination_display: str) -> None:
        if not self.enabled:
            return

        context = self._gather_context(match, destination_display)
        now = datetime.now(timezone.utc)

        if self._batcher is not None:
            request = self._batcher.prepare_event(context, now)
            payload = self._build_batch_payload(request, now)
            response = self._send_with_retries(
                request.action,
                self._message_url(request.message_id),
                payload,
            )
            if response is not None and request.action == "POST":
                message_id = self._extract_message_id(response)
                if message_id:
                    self._batcher.register_message_id(request.sport_id, request.bucket_date, message_id)
            return

        payload = self._build_single_payload(context, now)
        self._send_with_retries("POST", self.webhook_url, payload)

    def _gather_context(self, match: SportFileMatch, destination_display: str) -> Dict[str, Any]:
        show_title = match.show.title
        season_display = str(match.context.get("season_title") or match.season.title or "Season")
        session_display = str(match.context.get("session") or match.episode.title or "Session")
        episode_display = str(match.context.get("episode_title") or match.episode.title or session_display)
        summary = match.context.get("episode_summary") or match.episode.summary

        return {
            "sport_id": match.sport.id,
            "sport_name": match.sport.name,
            "show_title": show_title,
            "season": season_display,
            "session": session_display,
            "episode": episode_display,
            "summary": summary,
            "destination": destination_display,
            "source": match.source_path.name,
        }

    def _build_single_payload(self, context: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        embed_title = self._trim(f"{context['show_title']} – {context['session']}", 256)
        description = self._trim(str(context["summary"]), 2048) if context.get("summary") else None

        fields = [
            self._embed_field("Sport", context["sport_name"], inline=True),
            self._embed_field("Season", context["season"], inline=True),
            self._embed_field("Session", context["session"], inline=True),
            self._embed_field("Episode", context["episode"], inline=True),
            self._embed_field("Destination", f"`{context['destination']}`", inline=False),
            self._embed_field("Source", f"`{context['source']}`", inline=False),
        ]

        embed: Dict[str, Any] = {
            "title": embed_title,
            "color": 0x5865F2,
            "timestamp": now.isoformat(),
            "fields": [field for field in fields if field is not None],
            "footer": {"text": "Sports Organizer"},
        }
        if description:
            embed["description"] = description

        content = self._trim(
            f"New {context['sport_name']} entry is ready: {context['episode']}",
            limit=2000,
        )

        return {
            "content": content,
            "embeds": [embed],
        }

    def _build_batch_payload(self, request: BatchRequest, now: datetime) -> Dict[str, Any]:
        events = request.events
        total = len(events)

        # Keep recent entries visible to stay within Discord limits.
        visible_events = events[-20:]
        lines: List[str] = []
        for event in visible_events:
            season_part = f"{event['season']} – " if event.get("season") else ""
            line = f"• {season_part}{event['episode']} ({event['session']}) → `{event['destination']}`"
            lines.append(self._trim(line, 190))

        hidden_count = total - len(visible_events)
        if hidden_count > 0:
            lines.append(f"… and {hidden_count} more.")

        description = self._trim("\n".join(lines), 2048) if lines else None

        latest_timestamp = events[-1].get("timestamp") or now.isoformat()
        fields = [
            self._embed_field("Sport", request.sport_name, inline=True),
            self._embed_field("Updates", str(total), inline=True),
        ]

        latest_event = events[-1]
        latest_value = f"{latest_event['episode']} ({latest_event['session']}) → `{latest_event['destination']}`"
        fields.append(self._embed_field("Latest", latest_value, inline=False))

        embed: Dict[str, Any] = {
            "title": self._trim(f"{request.sport_name} – {request.bucket_date.isoformat()}", 256),
            "color": 0x5865F2,
            "timestamp": latest_timestamp,
            "fields": [field for field in fields if field is not None],
            "footer": {"text": "Sports Organizer"},
        }
        if description:
            embed["description"] = description

        content = self._trim(
            f"{request.sport_name} updates for {request.bucket_date.isoformat()}: {total} item{'s' if total != 1 else ''}",
            limit=2000,
        )

        return {
            "content": content,
            "embeds": [embed],
        }

    def _send_with_retries(self, method: str, url: str, payload: Dict[str, Any]) -> Optional[Response]:
        attempt = 0
        max_attempts = 5
        backoff = 1.0

        while attempt < max_attempts:
            try:
                response = requests.request(method, url, json=payload, timeout=10)
            except RequestException as exc:
                LOGGER.warning("Failed to send Discord notification: %s", exc)
                return None

            if response.status_code == 429:
                wait_seconds = self._retry_after_seconds(response, backoff)
                LOGGER.warning(
                    "Discord rate limited notification request; retrying in %.2fs (attempt %d/%d)",
                    wait_seconds,
                    attempt + 1,
                    max_attempts,
                )
                time.sleep(wait_seconds)
                attempt += 1
                backoff *= 2
                continue

            if response.status_code >= 400:
                LOGGER.warning(
                    "Discord webhook responded with %s: %s",
                    response.status_code,
                    self._excerpt_response(response),
                )
                return None

            return response

        LOGGER.error("Discord notification failed after %d attempts due to rate limiting.", max_attempts)
        return None

    def _message_url(self, message_id: Optional[str]) -> str:
        if not message_id:
            return self.webhook_url
        return f"{self.webhook_url}/messages/{message_id}"

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

    @staticmethod
    def _extract_message_id(response: Response) -> Optional[str]:
        try:
            payload = response.json()
        except ValueError:
            return None
        message_id = payload.get("id")
        return str(message_id) if message_id else None

    @staticmethod
    def _retry_after_seconds(response: Response, fallback: float) -> float:
        wait = fallback
        try:
            data = response.json()
        except ValueError:
            data = {}

        retry_after = data.get("retry_after")
        if isinstance(retry_after, (int, float)):
            wait = max(wait, float(retry_after))

        header_retry = response.headers.get("Retry-After")
        if header_retry:
            try:
                wait = max(wait, float(header_retry))
            except ValueError:
                pass

        return max(wait, 1.0)

