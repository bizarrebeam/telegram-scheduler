from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.db import get_conn_dict


def create_session(
    task_id: str, start: datetime, end: datetime, duration_minutes: int
) -> dict:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            INSERT INTO task_sessions
                (task_id, planned_start_at, planned_end_at, planned_duration_minutes)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (task_id, start, end, duration_minutes),
        ).fetchone()


def get_session(session_id: str) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            "SELECT * FROM task_sessions WHERE id = %s", (session_id,)
        ).fetchone()


def get_session_with_task(session_id: str) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT s.*, t.title AS task_title, t.id AS task_id_full, t.first_step, t.category
            FROM task_sessions s
            JOIN tasks t ON t.id = s.task_id
            WHERE s.id = %s
            """,
            (session_id,),
        ).fetchone()


def latest_active_session() -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT s.*, t.title AS task_title
            FROM task_sessions s
            JOIN tasks t ON t.id = s.task_id
            WHERE s.status IN ('planned', 'in_progress')
            ORDER BY s.planned_start_at DESC
            LIMIT 1
            """
        ).fetchone()


def session_for_task(task_id: str) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT s.*, t.title AS task_title
            FROM task_sessions s
            JOIN tasks t ON t.id = s.task_id
            WHERE s.task_id = %s AND s.status IN ('planned', 'in_progress')
            ORDER BY s.planned_start_at
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()


def planned_intervals(
    start: datetime, end: datetime, *, exclude_session_id: str | None = None
) -> list[tuple[datetime, datetime]]:
    with get_conn_dict() as conn:
        rows = conn.execute(
            """
            SELECT planned_start_at, planned_end_at
            FROM task_sessions
            WHERE status IN ('planned', 'in_progress')
              AND planned_end_at > %s
              AND planned_start_at < %s
              AND (%s::uuid IS NULL OR id <> %s::uuid)
            """,
            (start, end, exclude_session_id, exclude_session_id),
        ).fetchall()
        return [(r["planned_start_at"], r["planned_end_at"]) for r in rows]


def set_status(session_id: str, status: str) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            "UPDATE task_sessions SET status = %s WHERE id = %s RETURNING *",
            (status, session_id),
        ).fetchone()


def mark_started(session_id: str, when: datetime) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            UPDATE task_sessions
            SET status = 'in_progress', actual_start_at = %s
            WHERE id = %s AND status = 'planned'
            RETURNING *
            """,
            (when, session_id),
        ).fetchone()


def mark_completed(session_id: str, when: datetime) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            UPDATE task_sessions
            SET status = 'completed', actual_end_at = %s
            WHERE id = %s AND status IN ('planned', 'in_progress')
            RETURNING *
            """,
            (when, session_id),
        ).fetchone()


def extend(
    session_id: str, extra_minutes: int, *, max_extra_minutes: int = 240
) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            UPDATE task_sessions
            SET additional_duration_minutes = additional_duration_minutes + %s,
                planned_end_at = planned_end_at + %s::interval
            WHERE id = %s
              AND status IN ('planned', 'in_progress')
              AND additional_duration_minutes + %s <= %s
            RETURNING *
            """,
            (
                extra_minutes,
                f"{extra_minutes} minutes",
                session_id,
                extra_minutes,
                max_extra_minutes,
            ),
        ).fetchone()


def sessions_between(
    start: datetime, end: datetime, *, timezone: str = "Asia/Jakarta"
) -> list[dict]:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT s.*, t.title AS task_title
            FROM task_sessions s
            JOIN tasks t ON t.id = s.task_id
            WHERE s.planned_start_at < %s
              AND s.planned_end_at > %s
              AND s.status IN ('planned', 'in_progress')
            ORDER BY s.planned_start_at
            """,
            (end, start),
        ).fetchall()


def day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def next_upcoming(now: datetime, timezone: str) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT s.*, t.title AS task_title
            FROM task_sessions s
            JOIN tasks t ON t.id = s.task_id
            WHERE s.planned_end_at > %s
              AND s.status IN ('planned', 'in_progress')
            ORDER BY s.planned_start_at
            LIMIT 1
            """,
            (now,),
        ).fetchone()
