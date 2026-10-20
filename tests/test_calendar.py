from datetime import UTC, datetime

import pytest

from app.google.calendar import GoogleAuthRequired, create_event, get_busy_intervals


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_get_busy_intervals_parses(monkeypatch) -> None:
    payload = {
        "calendars": {
            "primary": {
                "busy": [
                    {"start": "2026-10-20T13:00:00Z", "end": "2026-10-20T14:00:00Z"}
                ]
            }
        }
    }
    monkeypatch.setattr(
        "app.google.calendar.httpx.post", lambda *a, **k: FakeResponse(200, payload)
    )
    creds = type("C", (), {"token": "x"})()
    intervals = get_busy_intervals(
        creds,
        datetime(2026, 10, 20, tzinfo=UTC),
        datetime(2026, 10, 21, tzinfo=UTC),
        "primary",
    )
    assert len(intervals) == 1
    assert intervals[0][0] == datetime(2026, 10, 20, 13, 0, tzinfo=UTC)
    assert intervals[0][1] == datetime(2026, 10, 20, 14, 0, tzinfo=UTC)


def test_get_busy_intervals_raises_on_401(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.google.calendar.httpx.post",
        lambda *a, **k: FakeResponse(401, {"error": "unauthorized"}),
    )
    creds = type("C", (), {"token": "x"})()
    with pytest.raises(GoogleAuthRequired):
        get_busy_intervals(
            creds,
            datetime(2026, 10, 20, tzinfo=UTC),
            datetime(2026, 10, 21, tzinfo=UTC),
            "primary",
        )


def test_create_event_returns_id(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.google.calendar.httpx.post",
        lambda *a, **k: FakeResponse(200, {"id": "evt_123"}),
    )
    creds = type("C", (), {"token": "x"})()
    event_id = create_event(
        creds,
        "primary",
        summary="[Task] Apply MT",
        description="Task ID: abc",
        start=datetime(2026, 10, 20, 13, 0, tzinfo=UTC),
        end=datetime(2026, 10, 20, 14, 0, tzinfo=UTC),
    )
    assert event_id == "evt_123"
