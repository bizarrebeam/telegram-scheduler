import sys

from app.config import get_settings
from app.db import get_conn_dict
from app.google.calendar import delete_event, get_event
from app.google.oauth import load_user_credentials
from app.services import proposals
from app.services.tasks import create_task


def _event_ids(task_id: str) -> list[tuple[str, str]]:
    with get_conn_dict() as conn:
        rows = conn.execute(
            """
            SELECT ce.google_calendar_id, ce.google_event_id
            FROM calendar_events ce
            JOIN task_sessions s ON s.id = ce.task_session_id
            WHERE s.task_id = %s
            """,
            (task_id,),
        ).fetchall()
        return [(r["google_calendar_id"], r["google_event_id"]) for r in rows]


def _session_ids(task_id: str) -> list[str]:
    with get_conn_dict() as conn:
        rows = conn.execute(
            "SELECT id::text FROM task_sessions WHERE task_id = %s", (task_id,)
        ).fetchall()
        return [r["id"] for r in rows]


def purge(task_id: str, creds) -> None:
    for calendar_id, event_id in _event_ids(task_id):
        delete_event(creds, calendar_id, event_id)
    session_ids = _session_ids(task_id)
    with get_conn_dict() as conn:
        conn.execute("DELETE FROM task_history WHERE task_id = %s", (task_id,))
        for session_id in session_ids:
            conn.execute(
                "DELETE FROM reminder_state WHERE task_session_id = %s", (session_id,)
            )
            conn.execute(
                "DELETE FROM calendar_events WHERE task_session_id = %s", (session_id,)
            )
            conn.execute(
                "DELETE FROM job_queue WHERE idempotency_key LIKE %s",
                (f"session:{session_id}:%",),
            )
        conn.execute("DELETE FROM schedule_proposals WHERE task_id = %s", (task_id,))
        conn.execute("DELETE FROM task_sessions WHERE task_id = %s", (task_id,))
        conn.execute("DELETE FROM task_attachments WHERE task_id = %s", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))


def purge_leftovers(creds) -> int:
    with get_conn_dict() as conn:
        rows = conn.execute(
            "SELECT id::text FROM tasks WHERE title LIKE '[SMOKE]%'"
        ).fetchall()
    for row in rows:
        purge(row["id"], creds)
    return len(rows)


def main() -> None:
    settings = get_settings()
    users = sorted(settings.allowed_telegram_user_ids)
    if not users:
        sys.exit("TELEGRAM_ALLOWED_USER_IDS is empty; run with a real .env")
    user_id = users[0]
    creds = load_user_credentials(user_id)
    if creds is None:
        sys.exit("no google credentials for user; connect with /auth first")

    cleaned = purge_leftovers(creds)
    if cleaned:
        print(f"leftover smoke tasks cleaned: {cleaned}")

    with get_conn_dict() as conn:
        row = conn.execute(
            "SELECT timezone, google_calendar_id FROM user_settings WHERE telegram_user_id = %s",
            (user_id,),
        ).fetchone()
    tz = row["timezone"] if row else "Asia/Jakarta"
    calendar_id = (row["google_calendar_id"] if row else None) or "primary"

    task_id = create_task(
        title="[SMOKE] cek alur jadwal",
        description="dibuat oleh scripts/smoke_scheduling.py",
        estimated_duration_minutes=60,
        duration_source="estimate",
    )
    print(f"task {task_id[:8]} created")

    result = proposals.propose_for_task(task_id, user_id)
    print("propose:", result.get("status"))
    if result.get("status") != "proposed":
        purge(task_id, creds)
        sys.exit(f"unexpected propose status: {result.get('status')}")

    proposal = result["proposal"]
    session_id = str(proposal["session_id"])
    print(f"slot ({tz}): {proposal['proposed_start_at']} -> {proposal['proposed_end_at']}")

    approve = proposals.approve_proposal(str(proposal["id"]), user_id)
    print("approve:", approve.get("status"))
    assert approve["status"] == "approved", approve
    event_id = approve["event_id"]
    assert get_event(creds, calendar_id, event_id) is not None
    print("google event exists:", event_id[:12], "...")

    again = proposals.approve_proposal(str(proposal["id"]), user_id)
    assert again["status"] == "already_approved", again
    print("idempotent approve: ok")

    with get_conn_dict() as conn:
        event_rows = conn.execute(
            "SELECT count(*) AS n FROM calendar_events WHERE task_session_id = %s",
            (session_id,),
        ).fetchone()
        job_rows = conn.execute(
            """
            SELECT id, kind FROM job_queue
            WHERE idempotency_key LIKE %s ORDER BY run_at
            """,
            (f"session:{session_id}:%",),
        ).fetchall()
    assert event_rows["n"] == 1, event_rows
    print("calendar_events rows:", event_rows["n"])
    print("reminder jobs:", [r["kind"] for r in job_rows])
    assert len(job_rows) >= 4, job_rows

    purge(task_id, creds)
    assert _event_ids(task_id) == []
    print("cleanup: db rows + google event removed")
    print("SMOKE OK")


if __name__ == "__main__":
    main()
