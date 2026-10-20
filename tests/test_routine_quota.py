from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.services import routines

JAKARTA = ZoneInfo("Asia/Jakarta")


def routine(routine_id: str, target: int) -> dict:
    return {
        "id": routine_id,
        "title": f"Rutin {routine_id}",
        "target_sessions_per_week": target,
        "session_duration_minutes": 45,
    }


def test_week_start_is_monday_midnight_local() -> None:
    start = routines.week_start(datetime(2026, 10, 21, 15, 0, tzinfo=UTC), "Asia/Jakarta")
    assert start.weekday() == 0
    assert (start.hour, start.minute, start.second) == (0, 0, 0)


def test_week_start_same_week_for_two_days() -> None:
    wed = routines.week_start(datetime(2026, 10, 21, 15, 0, tzinfo=UTC), "Asia/Jakarta")
    sun_local = datetime(2026, 10, 25, 10, 0, tzinfo=JAKARTA)
    sun = routines.week_start(sun_local, "Asia/Jakarta")
    assert wed == sun


def test_progress_reports_remaining(monkeypatch) -> None:
    monkeypatch.setattr(routines, "list_routines", lambda active_only=True: [routine("r1", 3)])
    monkeypatch.setattr(routines, "completed_counts", lambda now, tz: {"r1": 1})
    items = routines.progress(datetime(2026, 10, 21, tzinfo=UTC), "Asia/Jakarta")
    assert items[0]["completed_this_week"] == 1
    assert items[0]["remaining"] == 2


def test_progress_never_negative(monkeypatch) -> None:
    monkeypatch.setattr(routines, "list_routines", lambda active_only=True: [routine("r1", 2)])
    monkeypatch.setattr(routines, "completed_counts", lambda now, tz: {"r1": 5})
    items = routines.progress(datetime(2026, 10, 21, tzinfo=UTC), "Asia/Jakarta")
    assert items[0]["remaining"] == 0


def test_under_quota_filters_completed(monkeypatch) -> None:
    monkeypatch.setattr(
        routines,
        "list_routines",
        lambda active_only=True: [routine("r1", 3), routine("r2", 1)],
    )
    monkeypatch.setattr(routines, "completed_counts", lambda now, tz: {"r1": 3, "r2": 0})
    pending = routines.under_quota(datetime(2026, 10, 21, tzinfo=UTC), "Asia/Jakarta")
    assert [p["routine"]["id"] for p in pending] == ["r2"]
