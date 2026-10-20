from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.scheduler.availability import Availability, windows_for_day
from app.scheduler.planner import find_slot
from app.scheduler.priority import availability_from_settings

JAKARTA = ZoneInfo("Asia/Jakarta")


def local(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=JAKARTA)


def avail(**kwargs) -> Availability:
    return Availability(**kwargs)


def at(y, m, d, hh, mm=0) -> datetime:
    return local(y, m, d, hh, mm).astimezone(UTC)


def test_window_is_0800_to_2100_every_day() -> None:
    for day in range(19, 26):
        windows = windows_for_day(avail(), datetime(2026, 10, day).date())
        assert len(windows) == 1
        start, end = windows[0]
        assert (start.hour, start.minute) == (8, 0)
        assert (end.hour, end.minute) == (21, 0)


def test_settings_cannot_shrink_the_daily_window() -> None:
    availability = availability_from_settings(
        {
            "timezone": "Asia/Jakarta",
            "weekday_start_time": "20:00",
            "weekday_end_time": "23:00",
            "weekend_enabled": False,
        }
    )
    assert availability.weekday_start.isoformat() == "08:00:00"
    assert availability.weekday_end.isoformat() == "21:00:00"
    assert availability.weekend_enabled is True
    assert availability.gap_minutes == 30


def test_finds_first_slot_today() -> None:
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=60,
        busy=[],
    )
    assert result.reason == "ok"
    assert result.start == local(2026, 10, 19, 8)
    assert result.end == local(2026, 10, 19, 9)


def test_skips_existing_calendar_event() -> None:
    busy = [(local(2026, 10, 19, 8), local(2026, 10, 19, 10))]
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=60,
        busy=busy,
    )
    assert result.start == local(2026, 10, 19, 10)


def test_next_task_starts_thirty_minutes_after_previous_task_ends() -> None:
    task_busy = [(local(2026, 10, 19, 8), local(2026, 10, 19, 10))]
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=60,
        busy=[],
        task_busy=task_busy,
    )
    assert result.start == local(2026, 10, 19, 10, 30)
    assert result.end == local(2026, 10, 19, 11, 30)


def test_task_keeps_gap_before_the_next_task() -> None:
    task_busy = [(local(2026, 10, 19, 10), local(2026, 10, 19, 11))]
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=90,
        busy=[],
        task_busy=task_busy,
    )
    assert result.start == local(2026, 10, 19, 8)
    assert result.end == local(2026, 10, 19, 9, 30)


def test_task_skips_slot_that_would_leave_less_than_gap() -> None:
    task_busy = [(local(2026, 10, 19, 10), local(2026, 10, 19, 11))]
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=120,
        busy=[],
        task_busy=task_busy,
    )
    assert result.start == local(2026, 10, 19, 11, 30)
    assert result.start >= local(2026, 10, 19, 11, 30)


def test_saturday_and_sunday_are_available() -> None:
    saturday = find_slot(
        now=at(2026, 10, 24, 6),
        availability=avail(),
        duration_minutes=60,
        busy=[],
    )
    sunday = find_slot(
        now=at(2026, 10, 25, 6),
        availability=avail(),
        duration_minutes=60,
        busy=[],
    )
    assert saturday.start == local(2026, 10, 24, 8)
    assert sunday.start == local(2026, 10, 25, 8)


def test_tasks_never_start_before_0800_or_end_after_2100() -> None:
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=90,
        busy=[
            (local(2026, 10, 19, 8), local(2026, 10, 19, 19, 45)),
        ],
    )
    assert result.start == local(2026, 10, 20, 8)

    window = windows_for_day(avail(), datetime(2026, 10, 19).date())[0]
    assert result.start is not None
    assert result.end is not None
    assert window[0] <= result.start
    assert result.end <= window[1].replace(day=20)


def test_task_that_cannot_finish_before_2100_moves_to_next_valid_day() -> None:
    result = find_slot(
        now=at(2026, 10, 19, 20),
        availability=avail(),
        duration_minutes=120,
        busy=[],
    )
    assert result.start == local(2026, 10, 20, 8)
    assert result.end == local(2026, 10, 20, 10)


def test_weekend_fallback_when_weekdays_are_full() -> None:
    busy = []
    for offset in range(5):
        day = local(2026, 10, 19, 0) + timedelta(days=offset)
        busy.append((day.replace(hour=8), day.replace(hour=21)))
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=60,
        busy=busy,
    )
    assert result.start == local(2026, 10, 24, 8)


def test_weekend_tasks_also_keep_thirty_minute_gap() -> None:
    task_busy = [(local(2026, 10, 24, 8), local(2026, 10, 24, 9))]
    result = find_slot(
        now=at(2026, 10, 24, 6),
        availability=avail(),
        duration_minutes=60,
        busy=[],
        task_busy=task_busy,
    )
    assert result.start == local(2026, 10, 24, 9, 30)


def test_deadline_unmet_reports_reason() -> None:
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=60,
        busy=[],
        deadline_at=local(2026, 10, 19, 8, 30),
    )
    assert result.start is None
    assert result.reason == "deadline_unmet"


def test_deadline_met_when_slot_fits() -> None:
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=60,
        busy=[],
        deadline_at=local(2026, 10, 19, 9),
    )
    assert result.start == local(2026, 10, 19, 8)


def test_no_slot_when_all_windows_busy() -> None:
    busy = []
    day = local(2026, 10, 19, 0)
    for offset in range(15):
        current = day + timedelta(days=offset)
        busy.append((current.replace(hour=8), current.replace(hour=21)))
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=60,
        busy=busy,
    )
    assert result.reason == "no_slot"


def test_duration_longer_than_window_never_fits() -> None:
    result = find_slot(
        now=at(2026, 10, 19, 6),
        availability=avail(),
        duration_minutes=14 * 60,
        busy=[],
    )
    assert result.reason == "no_slot"


def test_never_uses_past_time() -> None:
    result = find_slot(
        now=at(2026, 10, 19, 12, 7),
        availability=avail(),
        duration_minutes=60,
        busy=[],
    )
    assert result.start == local(2026, 10, 19, 12, 15)
