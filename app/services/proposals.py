from datetime import UTC, datetime, timedelta

from app.db import get_conn_dict
from app.errors import classify_error, is_uncertain_calendar_error
from app.google.calendar import (
    EventConflict,
    GoogleAuthRequired,
    create_event,
    get_busy_intervals,
    get_event,
)
from app.google.oauth import load_user_credentials
from app.scheduler.planner import _breaks_gap, find_slot
from app.scheduler.priority import availability_from_settings
from app.services import history, jobs, sessions as sessions_service
from app.services import settings as settings_service

PROPOSAL_TTL_MINUTES = 30
PRE_END_REMINDER_MINUTES = 15
MISSED_FOLLOWUP_MINUTES = 10


def _calendar_id(user_settings: dict) -> str:
    from app.config import get_settings

    return (
        (user_settings or {}).get("google_calendar_id")
        or get_settings().google_calendar_id
        or "primary"
    )


def _own_planned_intervals(
    start: datetime, end: datetime, *, exclude_session_id: str | None = None
) -> list[tuple[datetime, datetime]]:
    return sessions_service.planned_intervals(
        start, end, exclude_session_id=exclude_session_id
    )


def propose_for_task(
    task_id: str,
    telegram_user_id: int,
    avoid: list[tuple[datetime, datetime]] | None = None,
) -> dict:
    creds = load_user_credentials(telegram_user_id)
    with get_conn_dict() as conn:
        task = conn.execute(
            "SELECT * FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if task is None:
            return {"status": "not_found"}
        if task["status"] not in ("captured", "pending", "scheduled", "planned"):
            return {"status": "not_actionable", "task_status": task["status"]}
        user_settings = settings_service.get_or_create_user_settings(telegram_user_id)
    if creds is None:
        return {"status": "needs_auth"}

    availability = availability_from_settings(user_settings)
    calendar_id = _calendar_id(user_settings)
    now = datetime.now(UTC)
    horizon = now + timedelta(days=availability.max_days_ahead + 1)
    try:
        busy = get_busy_intervals(creds, now, horizon, calendar_id)
    except GoogleAuthRequired:
        return {"status": "needs_auth"}
    busy = list(busy) + list(avoid or [])
    task_busy = _own_planned_intervals(now, horizon)

    duration = task["estimated_duration_minutes"] or 60
    result = find_slot(
        now=now,
        availability=availability,
        duration_minutes=duration,
        busy=busy,
        task_busy=task_busy,
        deadline_at=task["deadline_at"],
    )
    if result.reason != "ok":
        history.record(
            "proposal_none",
            task_id=task_id,
            details={"reason": result.reason},
        )
        return {"status": result.reason, "task": task}

    with get_conn_dict() as conn:
        conn.execute(
            """
            UPDATE schedule_proposals
            SET status = 'superseded'
            WHERE task_id = %s AND status = 'pending'
            """,
            (task_id,),
        )
        session = conn.execute(
            """
            INSERT INTO task_sessions
                (task_id, planned_start_at, planned_end_at, planned_duration_minutes)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (task_id, result.start, result.end, duration),
        ).fetchone()
        proposal = conn.execute(
            """
            INSERT INTO schedule_proposals
                (task_id, session_id, proposed_start_at, proposed_end_at, expires_at)
            VALUES (%s, %s, %s, %s, now() + %s::interval)
            RETURNING *
            """,
            (
                task_id,
                session["id"],
                result.start,
                result.end,
                f"{PROPOSAL_TTL_MINUTES} minutes",
            ),
        ).fetchone()
        conn.execute(
            "UPDATE tasks SET status = 'pending' WHERE id = %s AND status = 'captured'",
            (task_id,),
        )
    history.record(
        "proposal_created",
        task_id=task_id,
        session_id=str(session["id"]),
        details={"proposal_id": str(proposal["id"])},
    )
    return {"status": "proposed", "proposal": proposal, "task": task}


def get_proposal(proposal_id: str) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT p.*, t.title AS task_title,
                   t.estimated_duration_minutes, t.deadline_at, t.status AS task_status
            FROM schedule_proposals p
            JOIN tasks t ON t.id = p.task_id
            WHERE p.id = %s
            """,
            (proposal_id,),
        ).fetchone()


def reject_proposal(proposal_id: str) -> dict:
    with get_conn_dict() as conn:
        proposal = conn.execute(
            """
            UPDATE schedule_proposals
            SET status = 'rejected', rejected_at = now()
            WHERE id = %s AND status = 'pending'
            RETURNING *
            """,
            (proposal_id,),
        ).fetchone()
        if proposal is None:
            return {"status": "not_pending"}
        if proposal["session_id"] is not None:
            conn.execute(
                "UPDATE task_sessions SET status = 'cancelled' WHERE id = %s",
                (proposal["session_id"],),
            )
    history.record(
        "proposal_rejected",
        task_id=str(proposal["task_id"]),
        details={"proposal_id": proposal_id},
    )
    return {"status": "rejected", "proposal": proposal}


def _enqueue_session_jobs(session: dict) -> None:
    session_id = str(session["id"])
    start = session["planned_start_at"]
    end = session["planned_end_at"]
    duration = session["planned_duration_minutes"]
    jobs.enqueue(
        "session_start",
        {"session_id": session_id},
        run_at=start,
        idempotency_key=f"session:{session_id}:start",
    )
    jobs.enqueue(
        "session_end",
        {"session_id": session_id},
        run_at=end,
        idempotency_key=f"session:{session_id}:end",
    )
    jobs.enqueue(
        "missed_start",
        {"session_id": session_id},
        run_at=start + timedelta(minutes=MISSED_FOLLOWUP_MINUTES),
        idempotency_key=f"session:{session_id}:missed_start",
    )
    jobs.enqueue(
        "missed_followup",
        {"session_id": session_id},
        run_at=start + timedelta(minutes=MISSED_FOLLOWUP_MINUTES * 3),
        idempotency_key=f"session:{session_id}:missed_followup",
    )
    if duration > PRE_END_REMINDER_MINUTES:
        jobs.enqueue(
            "session_pre_end",
            {"session_id": session_id},
            run_at=end - timedelta(minutes=PRE_END_REMINDER_MINUTES),
            idempotency_key=f"session:{session_id}:pre_end",
        )


def approve_proposal(proposal_id: str, telegram_user_id: int) -> dict:
    creds = load_user_credentials(telegram_user_id)
    user_settings = settings_service.get_or_create_user_settings(telegram_user_id)
    calendar_id = _calendar_id(user_settings)
    now = datetime.now(UTC)

    with get_conn_dict() as conn:
        proposal = conn.execute(
            "SELECT * FROM schedule_proposals WHERE id = %s FOR UPDATE",
            (proposal_id,),
        ).fetchone()
        if proposal is None:
            return {"status": "not_found"}
        if proposal["status"] == "approved":
            return {"status": "already_approved", "proposal": proposal}
        if proposal["status"] != "pending":
            return {"status": "not_pending", "proposal_status": proposal["status"]}
        if proposal["expires_at"] <= now:
            conn.execute(
                "UPDATE schedule_proposals SET status = 'expired' WHERE id = %s",
                (proposal_id,),
            )
            return {"status": "expired"}
        if creds is None:
            return {"status": "needs_auth"}

        session = conn.execute(
            "SELECT * FROM task_sessions WHERE id = %s",
            (proposal["session_id"],),
        ).fetchone()
        task = conn.execute(
            "SELECT * FROM tasks WHERE id = %s", (proposal["task_id"],)
        ).fetchone()
        if session is None or task is None:
            return {"status": "corrupt"}

        start = proposal["proposed_start_at"]
        end = proposal["proposed_end_at"]
        event_id = str(session["id"]).replace("-", "")
        try:
            busy = get_busy_intervals(creds, start, end, calendar_id)
        except GoogleAuthRequired:
            return {"status": "needs_auth"}
        booked = list(busy)
        other_tasks = _own_planned_intervals(
            start, end, exclude_session_id=str(session["id"])
        )
        created_id: str | None = None
        if (
            any(start < b_end and end > b_start for b_start, b_end in booked)
            or _breaks_gap(start, end, other_tasks, 30)
        ):
            existing = get_event(creds, calendar_id, event_id)
            if existing is None:
                conn.execute(
                    "UPDATE schedule_proposals SET status = 'superseded' WHERE id = %s",
                    (proposal_id,),
                )
                conn.execute(
                    "UPDATE task_sessions SET status = 'superseded' WHERE id = %s",
                    (session["id"],),
                )
                history.record(
                    "proposal_superseded_conflict",
                    task_id=str(task["id"]),
                    session_id=str(session["id"]),
                    details={"proposal_id": proposal_id},
                )
                return {"status": "slot_taken", "task_id": str(task["id"])}
            created_id = event_id

        summary = f"[Task] {task['title']}"
        description = (
            f"Task ID: {str(task['id'])[:8]}\n"
            f"Estimated duration: {session['planned_duration_minutes']} minutes\n"
            f"First step: {(task.get('first_step') or '').strip() or '-'}"
        )
        if created_id is None:
            try:
                created_id = _create_event_idempotent(
                    creds,
                    calendar_id,
                    event_id=event_id,
                    summary=summary,
                    description=description,
                    start=start,
                    end=end,
                )
            except Exception as exc:  # noqa: BLE001
                error_code, _ = classify_error(exc)
                if is_uncertain_calendar_error(exc):
                    history.record(
                        "calendar_event_uncertain",
                        task_id=str(task["id"]),
                        session_id=str(session["id"]),
                        details={"error_code": error_code},
                    )
                    return {
                        "status": "event_uncertain",
                        "error_code": error_code,
                    }
                conn.execute(
                    """
                    UPDATE schedule_proposals
                    SET status = 'failed'
                    WHERE id = %s AND status = 'pending'
                    """,
                    (proposal_id,),
                )
                history.record(
                    "calendar_event_failed",
                    task_id=str(task["id"]),
                    session_id=str(session["id"]),
                    details={"error_code": error_code},
                )
                return {"status": "event_failed", "error_code": error_code}

        conn.execute(
            """
            INSERT INTO calendar_events (task_session_id, google_calendar_id, google_event_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (google_event_id) DO NOTHING
            """,
            (session["id"], calendar_id, created_id),
        )
        conn.execute(
            """
            UPDATE task_sessions
            SET calendar_event_id = %s, status = 'planned'
            WHERE id = %s
            """,
            (created_id, session["id"]),
        )
        conn.execute(
            """
            UPDATE schedule_proposals
            SET status = 'approved', approved_at = now()
            WHERE id = %s
            """,
            (proposal_id,),
        )
        conn.execute(
            "UPDATE tasks SET status = 'scheduled' WHERE id = %s",
            (task["id"],),
        )
    _enqueue_session_jobs(session)
    history.record(
        "proposal_approved",
        task_id=str(task["id"]),
        session_id=str(session["id"]),
        details={"proposal_id": proposal_id, "event_id": created_id},
    )
    return {
        "status": "approved",
        "proposal_id": proposal_id,
        "session_id": str(session["id"]),
        "task_title": task["title"],
        "start": start,
        "end": end,
        "event_id": created_id,
    }


def _create_event_idempotent(
    creds,
    calendar_id: str,
    *,
    event_id: str,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
) -> str:
    try:
        return create_event(
            creds,
            calendar_id,
            summary=summary,
            description=description,
            start=start,
            end=end,
            event_id=event_id,
        )
    except EventConflict:
        existing = get_event(creds, calendar_id, event_id)
        if existing is not None:
            return event_id
        raise


def sync_external_deletions(telegram_user_id: int) -> list[dict]:
    creds = load_user_credentials(telegram_user_id)
    if creds is None:
        return []
    user_settings = settings_service.get_or_create_user_settings(telegram_user_id)
    calendar_id = _calendar_id(user_settings)
    missing: list[dict] = []
    with get_conn_dict() as conn:
        rows = conn.execute(
            """
            SELECT ce.*, s.task_id, t.title AS task_title, s.planned_start_at
            FROM calendar_events ce
            JOIN task_sessions s ON s.id = ce.task_session_id
            JOIN tasks t ON t.id = s.task_id
            WHERE ce.event_status = 'confirmed'
              AND s.status IN ('planned', 'in_progress')
            """
        ).fetchall()
    for row in rows:
        if get_event(creds, calendar_id, row["google_event_id"]) is None:
            with get_conn_dict() as conn:
                conn.execute(
                    """
                    UPDATE calendar_events
                    SET event_status = 'deleted_externally', last_synced_at = now()
                    WHERE id = %s
                    """,
                    (row["id"],),
                )
            history.record(
                "event_deleted_externally",
                task_id=str(row["task_id"]),
                session_id=str(row["task_session_id"]),
                details={"event_id": row["google_event_id"]},
            )
            missing.append(row)
    return missing
