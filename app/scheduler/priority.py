from datetime import UTC, datetime, time

from app.scheduler.availability import Availability


def timeline_position(
    *,
    deadline_at: datetime | None,
    created_at: datetime,
    priority: int,
    task_id: str,
) -> tuple:
    return (
        deadline_at is None,
        deadline_at or datetime.max.replace(tzinfo=UTC),
        priority,
        created_at.astimezone(UTC),
        str(task_id),
    )


def sort_open_tasks(tasks: list[dict]) -> list[dict]:
    return sorted(
        tasks,
        key=lambda t: timeline_position(
            deadline_at=t["deadline_at"],
            created_at=t["created_at"],
            priority=t["priority"],
            task_id=t["id"],
        ),
    )


def describe_priority(deadline_at: datetime | None, now: datetime) -> str:
    if deadline_at is None:
        return "No deadline"
    delta = deadline_at - now
    hours = delta.total_seconds() / 3600
    if hours < 0:
        return "Deadline has passed"
    if hours < 24:
        return f"Due in {int(hours)} hours"
    return f"Due in {int(hours / 24)} days"


def availability_from_settings(settings_row: dict) -> Availability:
    return Availability(
        timezone=settings_row.get("timezone") or "Asia/Jakarta",
        weekday_start=time(20, 0),
        weekday_end=time(23, 0),
        weekend_enabled=True,
        weekend_start=time(8, 0),
        weekend_end=time(21, 0),
        gap_minutes=30,
    )
