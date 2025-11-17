from __future__ import annotations

import json
import logging
import smtplib
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests import Response
from requests.exceptions import RequestException

from .config import NotificationSettings
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


@dataclass(slots=True)
class NotificationEvent:
    sport_id: str
    sport_name: str
    show_title: str
    season: str
    session: str
    episode: str
    summary: Optional[str]
    destination: str
    source: str
    action: str
    link_mode: str
    replaced: bool = False
    skip_reason: Optional[str] = None
    trace_path: Optional[str] = None
    match_details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str = "unknown"  # new, changed, refresh, skipped, error, dry-run


class NotificationBatcher:
    """Persisted per-sport batches that group notifications by day."""

    def __init__(self, cache_dir: Path, settings: NotificationSettings) -> None:
        self._settings = settings
        self._path = cache_dir / "state" / "discord-batches.json"
        self._state: Dict[str, Dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def prepare_event(self, event: NotificationEvent, now: datetime) -> BatchRequest:
        sport_id = event.sport_id
        sport_name = event.sport_name
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
            "season": event.season,
            "session": event.session,
            "episode": event.episode,
            "destination": event.destination,
            "source": event.source,
            "summary": event.summary,
            "action": event.action,
            "link_mode": event.link_mode,
            "replaced": event.replaced,
            "skip_reason": event.skip_reason,
            "trace_path": event.trace_path,
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type,
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
                trace_path = item.get("trace_path")
                trace_str = str(trace_path) if trace_path else None
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
                        "action": str(item.get("action") or "link"),
                        "link_mode": str(item.get("link_mode") or ""),
                        "replaced": bool(item.get("replaced") or False),
                        "skip_reason": item.get("skip_reason"),
                        "trace_path": trace_str,
                        "timestamp": str(item.get("timestamp") or ""),
                        "event_type": str(item.get("event_type") or "unknown"),
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


class NotificationTarget:
    name: str = "target"

    def enabled(self) -> bool:
        return True

    def send(self, event: NotificationEvent) -> None:
        raise NotImplementedError


class DiscordTarget(NotificationTarget):
    name = "discord"

    def __init__(
        self,
        webhook_url: Optional[str],
        *,
        cache_dir: Path,
        settings: NotificationSettings,
        batch: Optional[bool] = None,
    ) -> None:
        self.webhook_url = webhook_url.strip() if isinstance(webhook_url, str) else None
        self._settings = settings
        use_batch = batch if batch is not None else settings.batch_daily
        self._batcher: Optional[NotificationBatcher]
        if self.enabled() and use_batch:
            self._batcher = NotificationBatcher(cache_dir, settings)
        else:
            self._batcher = None

    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send(self, event: NotificationEvent) -> None:
        if not self.enabled():
            return

        now = event.timestamp
        if self._batcher is not None:
            request = self._batcher.prepare_event(event, now)
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

        payload = self._build_single_payload(event, now)
        self._send_with_retries("POST", self.webhook_url, payload)

    def _build_single_payload(self, event: NotificationEvent, now: datetime) -> Dict[str, Any]:
        embed: Dict[str, Any] = {
            "title": self._trim(f"{event.show_title} – {event.session}", 256),
            "color": self._embed_color(event),
            "timestamp": now.isoformat(),
            "fields": [field for field in self._fields_for_event(event) if field is not None],
            "footer": {"text": "Playbook"},
        }
        if event.summary:
            embed["description"] = self._trim(str(event.summary), 2048)

        indicator = self._event_indicator(event.event_type)
        prefix = f"{indicator} " if indicator else ""
        content = self._trim(prefix + self._render_content(event), 2000)
        return {"content": content, "embeds": [embed]}

    def _build_batch_payload(self, request: BatchRequest, now: datetime) -> Dict[str, Any]:
        events = request.events
        total = len(events)
        visible_events = events[-20:]

        lines: List[str] = []
        for item in visible_events:
            action = item.get("action", "link")
            mode = item.get("link_mode") or ""
            indicator = self._event_indicator(item.get("event_type"))
            season_part = f"{item.get('season')} – " if item.get("season") else ""
            reason = f" [{item.get('skip_reason')}]" if item.get("skip_reason") else ""
            line = (
                f"• {indicator+' ' if indicator else ''}{season_part}{item.get('episode')} ({item.get('session')}) → "
                f"`{item.get('destination')}` [{action}{' '+mode if mode else ''}]{reason}"
            )
            lines.append(self._trim(line, 190))

        hidden_count = total - len(visible_events)
        if hidden_count > 0:
            lines.append(f"… and {hidden_count} more.")

        description = self._trim("\n".join(lines), 2048) if lines else None
        latest_payload = events[-1]
        latest_timestamp = latest_payload.get("timestamp") or now.isoformat()

        fields = [
            self._embed_field("Sport", request.sport_name, inline=True),
            self._embed_field("Updates", str(total), inline=True),
        ]
        latest_value = (
            f"{self._event_indicator(latest_payload.get('event_type'))} "
            f"{latest_payload.get('episode')} ({latest_payload.get('session')}) → "
            f"`{latest_payload.get('destination')}` [{latest_payload.get('action')}]"
        )
        fields.append(self._embed_field("Latest", latest_value, inline=False))

        embed: Dict[str, Any] = {
            "title": self._trim(f"{request.sport_name} – {request.bucket_date.isoformat()}", 256),
            "color": 0x5865F2,
            "timestamp": latest_timestamp,
            "fields": [field for field in fields if field is not None],
            "footer": {"text": "Playbook"},
        }
        if description:
            embed["description"] = description

        content = self._trim(
            f"{request.sport_name} updates for {request.bucket_date.isoformat()}: {total} item{'s' if total != 1 else ''}",
            limit=2000,
        )
        return {"content": content, "embeds": [embed]}

    def _render_content(self, event: NotificationEvent) -> str:
        if event.action == "skipped":
            reason = f" — {event.skip_reason}" if event.skip_reason else ""
            return f"Skipped {event.sport_name}: {event.episode} ({event.session}){reason}"
        if event.action == "error":
            reason = f" — {event.skip_reason}" if event.skip_reason else ""
            return f"Failed {event.sport_name}: {event.episode} ({event.session}){reason}"
        if event.action == "dry-run":
            return f"[Dry-Run] {event.sport_name}: {event.episode} ({event.session}) via {event.link_mode}"

        replaced = " (replaced existing)" if event.replaced else ""
        return f"{event.sport_name}: {event.episode} ({event.session}) {event.action} via {event.link_mode}{replaced}"

    def _fields_for_event(self, event: NotificationEvent) -> List[Optional[Dict[str, Any]]]:
        fields = [
            self._embed_field("Sport", event.sport_name, inline=True),
            self._embed_field("Season", event.season, inline=True),
            self._embed_field("Session", event.session, inline=True),
            self._embed_field("Episode", event.episode, inline=True),
            self._embed_field(
                "Action",
                f"{event.action} ({event.link_mode}){' – replaced' if event.replaced else ''}",
                inline=True,
            ),
            self._embed_field("Destination", f"`{event.destination}`", inline=False),
            self._embed_field("Source", f"`{event.source}`", inline=False),
        ]
        if event.skip_reason:
            fields.append(self._embed_field("Reason", event.skip_reason, inline=False))
        if event.trace_path:
            fields.append(self._embed_field("Trace", event.trace_path, inline=False))
        return fields

    def _embed_color(self, event: NotificationEvent) -> int:
        if event.action == "error":
            return 0xED4245
        if event.action == "skipped":
            return 0xFEE75C
        if event.action == "dry-run":
            return 0x95A5A6
        return 0x5865F2

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
        return {"name": self._trim(name, 256), "value": text, "inline": inline}

    @staticmethod
    def _event_indicator(event_type: Optional[str]) -> str:
        mapping = {
            "new": "[NEW]",
            "changed": "[UPDATED]",
            "error": "[ERROR]",
        }
        if not event_type:
            return ""
        return mapping.get(str(event_type).lower(), "")

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
        return DiscordTarget._trim(text or "<empty>", 200)

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


class SlackTarget(NotificationTarget):
    name = "slack"

    def __init__(self, webhook_url: Optional[str], template: Optional[str] = None) -> None:
        self.webhook_url = webhook_url.strip() if isinstance(webhook_url, str) else None
        self.template = template

    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send(self, event: NotificationEvent) -> None:
        if not self.enabled():
            return
        payload = {"text": self._render(event)}
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
        except RequestException as exc:
            LOGGER.warning("Failed to send Slack notification: %s", exc)
            return

        if response.status_code >= 400:
            LOGGER.warning("Slack webhook responded with %s: %s", response.status_code, response.text)

    def _render(self, event: NotificationEvent) -> str:
        if self.template:
            return self.template.format(
                sport=event.sport_name,
                season=event.season,
                session=event.session,
                episode=event.episode,
                destination=event.destination,
                source=event.source,
                action=event.action,
                link_mode=event.link_mode,
                skip_reason=event.skip_reason or "",
            )
        base = f"{event.sport_name}: {event.episode} ({event.session})"
        if event.action == "error":
            return f":warning: Failed {base}{' - '+event.skip_reason if event.skip_reason else ''}"
        if event.action == "skipped":
            return f":information_source: Skipped {base}{' - '+event.skip_reason if event.skip_reason else ''}"
        if event.action == "dry-run":
            return f":grey_question: [Dry-Run] {base} via {event.link_mode}"
        replaced = " (replaced)" if event.replaced else ""
        return f":white_check_mark: {base} {event.action} via {event.link_mode}{replaced} → {event.destination}"


class GenericWebhookTarget(NotificationTarget):
    name = "webhook"

    def __init__(
        self,
        url: Optional[str],
        *,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        template: Optional[Any] = None,
    ) -> None:
        self.url = url.strip() if isinstance(url, str) else None
        self.method = method.upper()
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.template = template

    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, event: NotificationEvent) -> None:
        if not self.enabled():
            return
        payload = self._build_payload(event)
        try:
            response = requests.request(
                self.method,
                self.url,
                json=payload,
                headers=self.headers or None,
                timeout=10,
            )
        except RequestException as exc:
            LOGGER.warning("Failed to send webhook notification: %s", exc)
            return

        if response.status_code >= 400:
            LOGGER.warning("Webhook %s responded with %s: %s", self.url, response.status_code, response.text)

    def _build_payload(self, event: NotificationEvent) -> Any:
        data = _flatten_event(event)
        template = self.template
        if template is None:
            return data
        return _render_template(template, data)


class EmailTarget(NotificationTarget):
    name = "email"

    def __init__(self, config: Dict[str, Any]) -> None:
        smtp_config = config.get("smtp") or {}
        self.host = smtp_config.get("host")
        self.port = int(smtp_config.get("port", 587))
        self.username = smtp_config.get("username")
        self.password = smtp_config.get("password")
        self.use_tls = bool(smtp_config.get("use_tls", True))
        self.timeout = int(smtp_config.get("timeout", 10))
        self.sender = config.get("from")
        recipients = config.get("to") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        self.recipients = [addr.strip() for addr in recipients if addr]
        self.subject_template = config.get("subject")
        self.body_template = config.get("body")

    def enabled(self) -> bool:
        return bool(self.host and self.sender and self.recipients)

    def send(self, event: NotificationEvent) -> None:
        if not self.enabled():
            return

        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = self._compose_subject(event)
        message.set_content(self._compose_body(event))

        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(message)
        except Exception as exc:  # pragma: no cover - environment dependent
            LOGGER.warning("Failed to send email notification via %s:%s - %s", self.host, self.port, exc)

    def _compose_subject(self, event: NotificationEvent) -> str:
        if self.subject_template:
            return self.subject_template.format(**_flatten_event(event))
        return f"{event.sport_name}: {event.episode} ({event.session}) [{event.action}]"

    def _compose_body(self, event: NotificationEvent) -> str:
        if self.body_template:
            return self.body_template.format(**_flatten_event(event))

        lines = [
            f"Sport: {event.sport_name}",
            f"Season: {event.season}",
            f"Session: {event.session}",
            f"Episode: {event.episode}",
            f"Action: {event.action} ({event.link_mode})",
            f"Destination: {event.destination}",
            f"Source: {event.source}",
        ]
        if event.skip_reason:
            lines.append(f"Reason: {event.skip_reason}")
        if event.trace_path:
            lines.append(f"Trace: {event.trace_path}")
        if event.summary:
            lines.append("")
            lines.append("Summary:")
            lines.append(event.summary)
        return "\n".join(lines)


class NotificationService:
    def __init__(
        self,
        settings: NotificationSettings,
        *,
        cache_dir: Path,
        default_discord_webhook: Optional[str],
        enabled: bool = True,
    ) -> None:
        self._settings = settings
        self._enabled = enabled
        self._targets = self._build_targets(settings.targets, cache_dir, default_discord_webhook)
        self._throttle_map = settings.throttle
        self._last_sent: Dict[str, datetime] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled and any(target.enabled() for target in self._targets)

    def notify(self, event: NotificationEvent) -> None:
        if not self.enabled:
            return
        allowed_types = {"new", "changed", "error"}
        event_type = (event.event_type or "unknown").lower()
        if event_type not in allowed_types:
            LOGGER.debug(
                "Skipping notification for %s because event_type is %s",
                event.sport_id,
                event.event_type,
            )
            return
        throttle_seconds = self._resolve_throttle(event.sport_id)
        last_event = self._last_sent.get(event.sport_id)
        if throttle_seconds and last_event:
            delta = (event.timestamp - last_event).total_seconds()
            if delta < throttle_seconds:
                LOGGER.debug(
                    "Skipping notification for %s due to throttle (%ss remaining)",
                    event.sport_id,
                    round(throttle_seconds - delta, 2),
                )
                return

        for target in self._targets:
            if not target.enabled():
                continue
            try:
                target.send(event)
            except Exception as exc:  # pragma: no cover - defensive logging
                LOGGER.warning("Notification target %s failed: %s", target.name, exc)

        self._last_sent[event.sport_id] = event.timestamp

    def _build_targets(
        self,
        targets_raw: List[Dict[str, Any]],
        cache_dir: Path,
        default_discord_webhook: Optional[str],
    ) -> List[NotificationTarget]:
        targets: List[NotificationTarget] = []
        configs = list(targets_raw)
        if not configs and default_discord_webhook:
            configs.append(
                {
                    "type": "discord",
                    "webhook_url": default_discord_webhook,
                    "batch": self._settings.batch_daily,
                }
            )

        for entry in configs:
            target_type = entry.get("type", "").lower()
            if target_type == "discord":
                webhook = entry.get("webhook_url") or default_discord_webhook
                if webhook:
                    batch = entry.get("batch")
                    targets.append(
                        DiscordTarget(
                            webhook,
                            cache_dir=cache_dir,
                            settings=self._settings,
                            batch=batch if batch is not None else entry.get("batch_daily"),
                        )
                    )
                else:
                    LOGGER.warning("Skipped Discord target because webhook_url was not provided.")
            elif target_type == "slack":
                url = entry.get("webhook_url") or entry.get("url")
                if url:
                    targets.append(SlackTarget(url, template=entry.get("template")))
                else:
                    LOGGER.warning("Skipped Slack target because webhook_url/url was not provided.")
            elif target_type == "webhook":
                url = entry.get("url")
                if url:
                    targets.append(
                        GenericWebhookTarget(
                            url,
                            method=entry.get("method", "POST"),
                            headers=entry.get("headers"),
                            template=entry.get("template"),
                        )
                    )
                else:
                    LOGGER.warning("Skipped webhook target because url was not provided.")
            elif target_type == "email":
                targets.append(EmailTarget(entry))
            else:
                LOGGER.warning("Unknown notification target type '%s'", target_type or "<missing>")

        return [target for target in targets if target.enabled()]

    def _resolve_throttle(self, sport_id: str) -> int:
        if not self._throttle_map:
            return 0
        if sport_id in self._throttle_map:
            return max(0, int(self._throttle_map[sport_id]))
        default = self._throttle_map.get("default")
        return max(0, int(default)) if default is not None else 0


def _flatten_event(event: NotificationEvent) -> Dict[str, Any]:
    data = {
        "sport_id": event.sport_id,
        "sport_name": event.sport_name,
        "show_title": event.show_title,
        "season": event.season,
        "session": event.session,
        "episode": event.episode,
        "summary": event.summary,
        "destination": event.destination,
        "source": event.source,
        "action": event.action,
        "link_mode": event.link_mode,
        "replaced": event.replaced,
        "skip_reason": event.skip_reason,
        "trace_path": event.trace_path,
        "timestamp": event.timestamp.isoformat(),
        "event_type": event.event_type,
    }
    data.update(event.match_details or {})
    return data


def _render_template(template: Any, data: Dict[str, Any]) -> Any:
    if isinstance(template, dict):
        return {key: _render_template(value, data) for key, value in template.items()}
    if isinstance(template, list):
        return [_render_template(value, data) for value in template]
    if isinstance(template, str):
        try:
            return template.format(**data)
        except Exception:
            return template
    return template