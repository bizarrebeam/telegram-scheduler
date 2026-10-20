from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Availability:
    timezone: str = "Asia/Jakarta"
    weekday_start: time = time(8, 0)
    weekday_end: time = time(21, 0)
    weekend_enabled: bool = True
    weekend_start: time | None = time(8, 0)
    weekend_end: time | None = time(21, 0)
    max_days_ahead: int = 14
    step_minutes: int = 15
    gap_minutes: int = 30

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def windows_for_day(availability: Availability, day: date) -> list[tuple[datetime, datetime]]:
    tz = availability.tz
    is_weekend = day.weekday() >= 5
    if is_weekend:
        if not availability.weekend_enabled:
            return []
        if availability.weekend_start is None or availability.weekend_end is None:
            return []
        start_t, end_t = availability.weekend_start, availability.weekend_end
    else:
        start_t, end_t = availability.weekday_start, availability.weekday_end
    if end_t <= start_t:
        return []
    return [
        (
            datetime.combine(day, start_t, tzinfo=tz),
            datetime.combine(day, end_t, tzinfo=tz),
        )
    ]


def first_window_on_or_after(
    availability: Availability, moment: datetime
) -> tuple[datetime, datetime] | None:
    local = moment.astimezone(availability.tz)
    for offset in range(availability.max_days_ahead + 1):
        day = (local + timedelta(days=offset)).date()
        for window in windows_for_day(availability, day):
            if window[1] > local:
                return window
    return None
