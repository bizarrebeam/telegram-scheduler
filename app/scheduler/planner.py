from dataclasses import dataclass
from datetime import datetime, timedelta

from app.scheduler.availability import Availability, windows_for_day


@dataclass(frozen=True)
class SlotResult:
    start: datetime | None
    end: datetime | None
    reason: str


def _ceil_to_step(moment: datetime, step_minutes: int) -> datetime:
    floored = moment.replace(second=0, microsecond=0)
    remainder = floored.minute % step_minutes
    if remainder == 0 and moment.second == 0 and moment.microsecond == 0:
        return floored
    return floored + timedelta(minutes=step_minutes - remainder or step_minutes)


def _overlaps_any(
    start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]
) -> bool:
    return any(start < busy_end and end > busy_start for busy_start, busy_end in busy)


def _breaks_gap(
    start: datetime,
    end: datetime,
    task_busy: list[tuple[datetime, datetime]],
    gap_minutes: int,
) -> bool:
    gap = timedelta(minutes=gap_minutes)
    return any(
        start < busy_end + gap and end > busy_start - gap
        for busy_start, busy_end in task_busy
    )


def find_slot(
    *,
    now: datetime,
    availability: Availability,
    duration_minutes: int,
    busy: list[tuple[datetime, datetime]],
    task_busy: list[tuple[datetime, datetime]] | None = None,
    deadline_at: datetime | None = None,
) -> SlotResult:
    if duration_minutes <= 0:
        return SlotResult(None, None, "invalid_duration")
    task_busy = task_busy or []
    tz = availability.tz
    local_now = now.astimezone(tz)
    deadline_local = deadline_at.astimezone(tz) if deadline_at else None
    earliest = _ceil_to_step(local_now, availability.step_minutes)
    blocked_by_deadline = False
    for offset in range(availability.max_days_ahead + 1):
        day = (local_now + timedelta(days=offset)).date()
        for window_start, window_end in windows_for_day(availability, day):
            cursor = max(window_start, earliest)
            cursor = _ceil_to_step(cursor, availability.step_minutes)
            while cursor + timedelta(minutes=duration_minutes) <= window_end:
                candidate_end = cursor + timedelta(minutes=duration_minutes)
                if deadline_local is not None and candidate_end > deadline_local:
                    blocked_by_deadline = True
                    break
                if (
                    not _overlaps_any(cursor, candidate_end, busy)
                    and not _breaks_gap(
                        cursor,
                        candidate_end,
                        task_busy,
                        availability.gap_minutes,
                    )
                ):
                    return SlotResult(cursor, candidate_end, "ok")
                cursor += timedelta(minutes=availability.step_minutes)
    return SlotResult(None, None, "deadline_unmet" if blocked_by_deadline else "no_slot")
