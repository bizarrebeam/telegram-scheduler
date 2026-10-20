import json
from datetime import datetime

from app.db import get_conn_dict


def enqueue(
    kind: str,
    payload: dict,
    *,
    run_at: datetime,
    idempotency_key: str | None = None,
) -> None:
    with get_conn_dict() as conn:
        conn.execute(
            """
            INSERT INTO job_queue (kind, payload, run_at, idempotency_key)
            VALUES (%s, %s::jsonb, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (kind, json.dumps(payload), run_at, idempotency_key),
        )


def claim_due(limit: int = 20) -> list[dict]:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            UPDATE job_queue
            SET status = 'running', attempts = attempts + 1
            WHERE id IN (
                SELECT id FROM job_queue
                WHERE status = 'pending' AND run_at <= now()
                ORDER BY run_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, kind, payload, attempts
            """,
            (limit,),
        ).fetchall()


def mark_done(job_id: int) -> None:
    with get_conn_dict() as conn:
        conn.execute(
            "UPDATE job_queue SET status = 'done' WHERE id = %s", (job_id,)
        )


def mark_failed(job_id: int, error: str, retry_after_seconds: int = 60) -> None:
    with get_conn_dict() as conn:
        row = conn.execute(
            "SELECT attempts FROM job_queue WHERE id = %s", (job_id,)
        ).fetchone()
        attempts = row["attempts"] if row else 1
        if attempts >= 3:
            conn.execute(
                """
                UPDATE job_queue SET status = 'failed', last_error = %s WHERE id = %s
                """,
                (error[:500], job_id),
            )
            return
        conn.execute(
            """
            UPDATE job_queue
            SET status = 'pending',
                last_error = %s,
                run_at = now() + %s::interval
            WHERE id = %s
            """,
            (error[:500], f"{retry_after_seconds} seconds", job_id),
        )


def reset_stale_running(max_age_minutes: int = 30) -> int:
    with get_conn_dict() as conn:
        rows = conn.execute(
            """
            UPDATE job_queue
            SET status = 'pending'
            WHERE status = 'running' AND updated_at < now() - %s::interval
            RETURNING id
            """,
            (f"{max_age_minutes} minutes",),
        ).fetchall()
        return len(rows)


def reap_expired_proposals() -> int:
    with get_conn_dict() as conn:
        rows = conn.execute(
            """
            UPDATE schedule_proposals
            SET status = 'expired'
            WHERE status = 'pending' AND expires_at <= now()
            RETURNING id
            """
        ).fetchall()
        return len(rows)


def cancel_session_jobs(session_id: str) -> int:
    with get_conn_dict() as conn:
        rows = conn.execute(
            """
            DELETE FROM job_queue
            WHERE status = 'pending' AND idempotency_key LIKE %s
            RETURNING id
            """,
            (f"session:{session_id}:%",),
        ).fetchall()
        return len(rows)
