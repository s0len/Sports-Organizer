from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from sports_organizer.config import NotificationSettings, PatternConfig
from sports_organizer.models import Episode, Season, Show, SportFileMatch
from sports_organizer.notifications import DiscordNotifier


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


def _build_match(tmp_path: Path, *, sport_id: str = "demo", sport_name: str = "Demo", session: str = "Qualifying") -> SportFileMatch:
    episode = Episode(
        title=session,
        summary="Session summary",
        originally_available=None,
        index=1,
        display_number=1,
    )
    season = Season(
        key="01",
        title="Season 1",
        summary=None,
        index=1,
        episodes=[episode],
        display_number=1,
        round_number=1,
    )
    show = Show(key="demo", title="Demo Series", summary=None, seasons=[season])
    pattern = PatternConfig(regex=r".*")
    sport = SimpleNamespace(id=sport_id, name=sport_name, link_mode="hardlink")
    context = {
        "season_title": "Season 1",
        "session": session,
        "episode_title": session,
        "episode_summary": "Session summary",
    }
    source = tmp_path / "source.mkv"
    destination = tmp_path / "dest.mkv"
    return SportFileMatch(
        source_path=source,
        destination_path=destination,
        show=show,
        season=season,
        episode=episode,
        pattern=pattern,
        context=context,
        sport=sport,
    )


def test_discord_notifier_sends_direct_message_without_batching(tmp_path, monkeypatch) -> None:
    settings = NotificationSettings(batch_daily=False, flush_time=dt.time(hour=0, minute=0))
    notifier = DiscordNotifier("https://discord.test/webhook", cache_dir=tmp_path, settings=settings)
    match = _build_match(tmp_path)

    calls: List[Dict[str, Any]] = []

    def fake_request(method, url, json=None, timeout=None):
        calls.append({"method": method, "url": url, "json": json})
        return FakeResponse(204)

    monkeypatch.setattr("sports_organizer.notifications.requests.request", fake_request)

    notifier.notify_processed(match, destination_display="Demo.mkv")

    assert len(calls) == 1
    request = calls[0]
    assert request["method"] == "POST"
    assert request["url"] == "https://discord.test/webhook"
    payload = request["json"]
    assert payload["embeds"][0]["fields"][0]["value"] == "Demo"


def test_discord_notifier_batches_and_edits_message(tmp_path, monkeypatch) -> None:
    settings = NotificationSettings(batch_daily=True, flush_time=dt.time(hour=0, minute=0))
    notifier = DiscordNotifier("https://discord.test/webhook", cache_dir=tmp_path, settings=settings)
    match = _build_match(tmp_path)

    responses = [
        FakeResponse(200, {"id": "message123"}),
        FakeResponse(200, {"id": "message123"}),
    ]
    calls: List[Dict[str, Any]] = []

    def fake_request(method, url, json=None, timeout=None):
        calls.append({"method": method, "url": url, "json": json})
        return responses.pop(0)

    monkeypatch.setattr("sports_organizer.notifications.requests.request", fake_request)

    notifier.notify_processed(match, destination_display="Demo-1.mkv")
    notifier.notify_processed(match, destination_display="Demo-2.mkv")

    assert [call["method"] for call in calls] == ["POST", "PATCH"]
    assert calls[0]["url"] == "https://discord.test/webhook"
    assert calls[1]["url"].endswith("/messages/message123")
    assert calls[1]["json"]["embeds"][0]["fields"][1]["value"] == "2"

    state_path = tmp_path / "state" / "discord-batches.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["demo"]["message_id"] == "message123"


def test_discord_notifier_retries_after_rate_limit(tmp_path, monkeypatch) -> None:
    settings = NotificationSettings(batch_daily=False, flush_time=dt.time(hour=0, minute=0))
    notifier = DiscordNotifier("https://discord.test/webhook", cache_dir=tmp_path, settings=settings)
    match = _build_match(tmp_path)

    request_calls: List[str] = []
    responses = [
        FakeResponse(429, {"retry_after": 0.3}, headers={"Retry-After": "0.2"}),
        FakeResponse(204),
    ]

    def fake_request(method, url, json=None, timeout=None):
        request_calls.append(method)
        return responses.pop(0)

    sleep_calls: List[float] = []

    monkeypatch.setattr("sports_organizer.notifications.requests.request", fake_request)
    monkeypatch.setattr("sports_organizer.notifications.time.sleep", lambda seconds: sleep_calls.append(seconds))

    notifier.notify_processed(match, destination_display="Demo.mkv")

    assert request_calls == ["POST", "POST"]
    assert sleep_calls and sleep_calls[0] >= 1.0

