from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

from sports_organizer.config import NotificationSettings
from sports_organizer.notifications import NotificationEvent, NotificationService


class FakeResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self) -> Dict[str, Any]:
        if self._payload is None:
            raise ValueError("No JSON payload")
        return self._payload


def _build_event(destination: str = "Demo.mkv", action: str = "link") -> NotificationEvent:
    return NotificationEvent(
        sport_id="demo",
        sport_name="Demo Sport",
        show_title="Demo Series",
        season="Season 1",
        session="Qualifying",
        episode="Qualifying",
        summary="Session summary",
        destination=destination,
        source="source.mkv",
        action=action,
        link_mode="hardlink",
        timestamp=dt.datetime.now(dt.timezone.utc),
    )


def test_notification_service_sends_discord_message(tmp_path, monkeypatch) -> None:
    settings = NotificationSettings(batch_daily=False, flush_time=dt.time(hour=0, minute=0))
    service = NotificationService(
        settings,
        cache_dir=tmp_path,
        default_discord_webhook="https://discord.test/webhook",
        enabled=True,
    )

    calls: List[Dict[str, Any]] = []

    def fake_request(method, url, json=None, timeout=None, headers=None):
        calls.append({"method": method, "url": url, "json": json})
        return FakeResponse(204)

    monkeypatch.setattr("sports_organizer.notifications.requests.request", fake_request)

    service.notify(_build_event())

    assert len(calls) == 1
    request = calls[0]
    assert request["method"] == "POST"
    assert request["url"] == "https://discord.test/webhook"
    payload = request["json"]
    assert payload["embeds"][0]["fields"][0]["value"] == "Demo Sport"


def test_notification_service_batches_discord_messages(tmp_path, monkeypatch) -> None:
    settings = NotificationSettings(batch_daily=True, flush_time=dt.time(hour=0, minute=0))
    service = NotificationService(
        settings,
        cache_dir=tmp_path,
        default_discord_webhook="https://discord.test/webhook",
        enabled=True,
    )

    responses = [
        FakeResponse(200, {"id": "message123"}),
        FakeResponse(200, {"id": "message123"}),
    ]
    calls: List[Dict[str, Any]] = []

    def fake_request(method, url, json=None, timeout=None, headers=None):
        calls.append({"method": method, "url": url, "json": json})
        return responses.pop(0)

    monkeypatch.setattr("sports_organizer.notifications.requests.request", fake_request)

    service.notify(_build_event(destination="Demo-1.mkv"))
    service.notify(_build_event(destination="Demo-2.mkv"))

    assert [call["method"] for call in calls] == ["POST", "PATCH"]
    assert calls[0]["url"] == "https://discord.test/webhook"
    assert calls[1]["url"].endswith("/messages/message123")
    assert calls[1]["json"]["embeds"][0]["fields"][1]["value"] == "2"

    state_path = tmp_path / "state" / "discord-batches.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["demo"]["message_id"] == "message123"


def test_notification_service_handles_rate_limiting(tmp_path, monkeypatch) -> None:
    settings = NotificationSettings(batch_daily=False, flush_time=dt.time(hour=0, minute=0))
    service = NotificationService(
        settings,
        cache_dir=tmp_path,
        default_discord_webhook="https://discord.test/webhook",
        enabled=True,
    )

    responses = [
        FakeResponse(429, {"retry_after": 0.3}, headers={"Retry-After": "0.2"}),
        FakeResponse(204),
    ]
    request_calls: List[str] = []
    sleep_calls: List[float] = []

    def fake_request(method, url, json=None, timeout=None, headers=None):
        request_calls.append(method)
        return responses.pop(0)

    monkeypatch.setattr("sports_organizer.notifications.requests.request", fake_request)
    monkeypatch.setattr("sports_organizer.notifications.time.sleep", lambda seconds: sleep_calls.append(seconds))

    service.notify(_build_event())

    assert request_calls == ["POST", "POST"]
    assert sleep_calls and sleep_calls[0] >= 1.0

