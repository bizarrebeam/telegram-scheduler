import json

from app.db import get_conn_dict


def record(
    event_type: str,
    *,
    task_id: str | None = None,
    session_id: str | None = None,
    details: dict | None = None,
) -> None:
    with get_conn_dict() as conn:
        conn.execute(
            """
            INSERT INTO task_history (task_id, session_id, event_type, details)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (task_id, session_id, event_type, json.dumps(details or {})),
        )


def recent_for_task(task_id: str, limit: int = 20) -> list[dict]:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT event_type, details, created_at
            FROM task_history
            WHERE task_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (task_id, limit),
        ).fetchall()
