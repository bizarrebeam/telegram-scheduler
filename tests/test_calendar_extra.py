from datetime import UTC, datetime

import pytest

from app.google.calendar import (
    EventConflict,
    create_event,
    delete_event,
    get_event,
    update_event_time,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


CREDS = type("C", (), {"token": "x"})()
START = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)
END = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)


def test_create_event_sends_client_supplied_id(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return FakeResponse(200, {"id": json["id"]})

    monkeypatch.setattr("app.google.calendar.httpx.post", fake_post)
    result = create_event(
        CREDS,
        "primary",
        summary="s",
        description="d",
        start=START,
        end=END,
        event_id="abc123",
    )
    assert result == "abc123"
    assert captured["json"]["id"] == "abc123"


def test_create_event_conflict_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.google.calendar.httpx.post", lambda *a, **k: FakeResponse(409)
    )
    with pytest.raises(EventConflict):
        create_event(
            CREDS,
            "primary",
            summary="s",
            description="d",
            start=START,
            end=END,
            event_id="abc123",
        )


def test_get_event_returns_none_on_404(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.google.calendar.httpx.get", lambda *a, **k: FakeResponse(404)
    )
    assert get_event(CREDS, "primary", "missing") is None


def test_get_event_returns_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.google.calendar.httpx.get",
        lambda *a, **k: FakeResponse(200, {"id": "evt"}),
    )
    assert get_event(CREDS, "primary", "evt")["id"] == "evt"


def test_update_event_time_patches(monkeypatch) -> None:
    captured: dict = {}

    def fake_patch(url, json, headers, timeout):
        captured["json"] = json
        return FakeResponse(200, {"id": "evt"})

    monkeypatch.setattr("app.google.calendar.httpx.patch", fake_patch)
    assert update_event_time(CREDS, "primary", "evt", start=START, end=END) is True
    assert captured["json"]["end"]["dateTime"] == END.isoformat()


def test_update_event_time_missing_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.google.calendar.httpx.patch", lambda *a, **k: FakeResponse(404)
    )
    assert update_event_time(CREDS, "primary", "evt", start=START, end=END) is False


def test_delete_event_treats_404_as_gone(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.google.calendar.httpx.delete", lambda *a, **k: FakeResponse(404)
    )
    assert delete_event(CREDS, "primary", "evt") is False


def test_delete_event_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.google.calendar.httpx.delete", lambda *a, **k: FakeResponse(204)
    )
    assert delete_event(CREDS, "primary", "evt") is True
