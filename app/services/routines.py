from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import get_conn_dict


def add_routine(
    *,
    title: str,
    session_duration_minutes: int,
    target_sessions_per_week: int,
    active_days: list[int] | None = None,
    description: str | None = None,
    available_from=None,
    available_until=None,
) -> dict:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            INSERT INTO routines
                (title, description, session_duration_minutes,
                 target_sessions_per_week, active_days, available_from, available_until)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                title,
                description,
                session_duration_minutes,
                target_sessions_per_week,
                active_days or [0, 1, 2, 3, 4],
                available_from,
                available_until,
            ),
        ).fetchone()


def list_routines(active_only: bool = True) -> list[dict]:
    query = "SELECT * FROM routines"
    if active_only:
        query += " WHERE active = true"
    query += " ORDER BY created_at"
    with get_conn_dict() as conn:
        return conn.execute(query).fetchall()


def deactivate_routine(routine_id: str) -> bool:
    with get_conn_dict() as conn:
        row = conn.execute(
            "UPDATE routines SET active = false WHERE id = %s RETURNING id",
            (routine_id,),
        ).fetchone()
        return row is not None


def week_start(now: datetime, timezone: str) -> datetime:
    local = now.astimezone(ZoneInfo(timezone))
    monday = local - timedelta(days=local.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def completed_counts(now: datetime, timezone: str) -> dict[str, int]:
    start = week_start(now, timezone)
    with get_conn_dict() as conn:
        rows = conn.execute(
            """
            SELECT t.routine_id, count(*) AS done
            FROM task_sessions s
            JOIN tasks t ON t.id = s.task_id
            WHERE t.routine_id IS NOT NULL
              AND s.status = 'completed'
              AND s.planned_start_at >= %s
            GROUP BY t.routine_id
            """,
            (start,),
        ).fetchall()
        return {str(r["routine_id"]): r["done"] for r in rows}


def progress(now: datetime, timezone: str) -> list[dict]:
    counts = completed_counts(now, timezone)
    result = []
    for routine in list_routines():
        done = counts.get(str(routine["id"]), 0)
        target = routine["target_sessions_per_week"]
        result.append(
            {
                "routine": routine,
                "completed_this_week": done,
                "target": target,
                "remaining": max(target - done, 0),
            }
        )
    return result


def under_quota(now: datetime, timezone: str) -> list[dict]:
    return [p for p in progress(now, timezone) if p["remaining"] > 0]


def create_routine_instance(routine: dict) -> dict:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            INSERT INTO tasks
                (title, description, category, estimated_duration_minutes,
                 duration_source, routine_id, status)
            VALUES (%s, %s, 'routine', %s, 'user', %s, 'captured')
            RETURNING *
            """,
            (
                routine["title"],
                routine.get("description"),
                routine["session_duration_minutes"],
                routine["id"],
            ),
        ).fetchone()


def now_local(timezone: str) -> datetime:
    return datetime.now(UTC).astimezone(ZoneInfo(timezone))
