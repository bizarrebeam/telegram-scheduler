from datetime import datetime

from app.db import get_conn_dict


def claim(session_id: str, reminder_type: str, scheduled_at: datetime) -> bool:
    with get_conn_dict() as conn:
        row = conn.execute(
            """
            INSERT INTO reminder_state (task_session_id, reminder_type, scheduled_at, status)
            VALUES (%s, %s, %s, 'pending')
            ON CONFLICT (task_session_id, reminder_type) DO NOTHING
            RETURNING id
            """,
            (session_id, reminder_type, scheduled_at),
        ).fetchone()
        return row is not None


def mark_sent(session_id: str, reminder_type: str) -> None:
    with get_conn_dict() as conn:
        conn.execute(
            """
            UPDATE reminder_state
            SET status = 'sent', sent_at = now()
            WHERE task_session_id = %s AND reminder_type = %s
            """,
            (session_id, reminder_type),
        )


def mark_failed(session_id: str, reminder_type: str) -> None:
    with get_conn_dict() as conn:
        conn.execute(
            """
            UPDATE reminder_state
            SET status = 'pending'
            WHERE task_session_id = %s AND reminder_type = %s
            """,
            (session_id, reminder_type),
        )


def mark_response(session_id: str) -> None:
    with get_conn_dict() as conn:
        conn.execute(
            """
            UPDATE reminder_state
            SET response_received_at = now()
            WHERE task_session_id = %s AND response_received_at IS NULL
            """,
            (session_id,),
        )


def sent_for(session_id: str) -> list[dict]:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT reminder_type, status, sent_at, followup_sent_at
            FROM reminder_state
            WHERE task_session_id = %s
            ORDER BY scheduled_at
            """,
            (session_id,),
        ).fetchall()
