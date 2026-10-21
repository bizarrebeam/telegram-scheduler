from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.db import get_conn_dict
from app.errors import classify_error, is_uncertain_calendar_error
from app.google.calendar import (
    EventConflict,
    GoogleAuthRequired,
    create_all_day_event,
    get_event,
    update_event_date,
)
from app.google.oauth import load_user_credentials
from app.parsing.basic import deadline_end
from app.services import history
from app.services import settings as settings_service

def _calendar_id(user_settings: dict) -> str:
    from app.config import get_settings

    return (
        (user_settings or {}).get("google_calendar_id")
        or get_settings().google_calendar_id
        or "primary"
    )


def get_deadline(reminder_id: str) -> dict | None:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT d.*, t.title AS task_title, t.id AS task_id,
                   t.status AS task_status
            FROM deadline_reminders d
            JOIN tasks t ON t.id = d.task_id
            WHERE d.id = %s
            """,
            (reminder_id,),
        ).fetchone()


def propose_deadline(task_id: str) -> dict:
    with get_conn_dict() as conn:
        task = conn.execute(
            "SELECT * FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if task is None:
            return {"status": "not_found"}
        event_date = task.get("deadline_date")
        if event_date is None and task.get("deadline_at") is not None:
            event_date = task["deadline_at"].astimezone(
                ZoneInfo("Asia/Jakarta")
            ).date()
        if event_date is None:
            return {"status": "missing_deadline"}
        existing = conn.execute(
            "SELECT * FROM deadline_reminders WHERE task_id = %s FOR UPDATE",
            (task_id,),
        ).fetchone()
        if existing is None:
            reminder = conn.execute(
                """
                INSERT INTO deadline_reminders (task_id, event_date)
                VALUES (%s, %s)
                RETURNING *
                """,
                (task_id, event_date),
            ).fetchone()
        elif existing["status"] == "approved":
            reminder = conn.execute(
                """
                UPDATE deadline_reminders
                SET pending_date = %s, status = 'pending_update'
                WHERE id = %s
                RETURNING *
                """,
                (event_date, existing["id"]),
            ).fetchone()
        elif existing["status"] in ("proposed", "pending_update"):
            reminder = conn.execute(
                """
                UPDATE deadline_reminders
                SET event_date = %s, pending_date = NULL, status = 'proposed'
                WHERE id = %s
                RETURNING *
                """,
                (event_date, existing["id"]),
            ).fetchone()
        else:
            reminder = conn.execute(
                """
                UPDATE deadline_reminders
                SET event_date = %s, pending_date = NULL, status = 'proposed',
                    google_event_id = NULL
                WHERE id = %s
                RETURNING *
                """,
                (event_date, existing["id"]),
            ).fetchone()
        conn.execute(
            """
            UPDATE tasks
            SET deadline_date = %s, deadline_at = %s
            WHERE id = %s
            """,
            (event_date, deadline_end(event_date), task_id),
        )
    history.record(
        "deadline_proposed",
        task_id=task_id,
        details={"reminder_id": str(reminder["id"]), "date": event_date.isoformat()},
    )
    return {"status": "proposed", "reminder": reminder, "task": task}


def reject_deadline(reminder_id: str) -> dict:
    with get_conn_dict() as conn:
        reminder = conn.execute(
            "SELECT * FROM deadline_reminders WHERE id = %s FOR UPDATE",
            (reminder_id,),
        ).fetchone()
        if reminder is None:
            return {"status": "not_found"}
        if reminder["status"] == "pending_update":
            reminder = conn.execute(
                """
                UPDATE deadline_reminders
                SET status = 'approved', pending_date = NULL
                WHERE id = %s
                RETURNING *
                """,
                (reminder_id,),
            ).fetchone()
            return {"status": "change_rejected", "reminder": reminder}
        if reminder["status"] != "proposed":
            return {"status": "not_pending", "reminder": reminder}
        reminder = conn.execute(
            """
            UPDATE deadline_reminders SET status = 'rejected'
            WHERE id = %s RETURNING *
            """,
            (reminder_id,),
        ).fetchone()
    history.record(
        "deadline_rejected",
        task_id=str(reminder["task_id"]),
        details={"reminder_id": reminder_id},
    )
    return {"status": "rejected", "reminder": reminder}


def _create_all_day_idempotent(creds, calendar_id, *, reminder_id, summary,
                               description, event_date):
    event_id = "dl" + reminder_id.replace("-", "")
    try:
        return create_all_day_event(
            creds,
            calendar_id,
            summary=summary,
            description=description,
            event_date=event_date,
            event_id=event_id,
        )
    except EventConflict:
        existing = get_event(creds, calendar_id, event_id)
        if existing is not None and existing.get("status") != "cancelled":
            return event_id
        raise


def approve_deadline(reminder_id: str, telegram_user_id: int) -> dict:
    creds = load_user_credentials(telegram_user_id)
    user_settings = settings_service.get_or_create_user_settings(telegram_user_id)
    calendar_id = _calendar_id(user_settings)
    with get_conn_dict() as conn:
        reminder = conn.execute(
            "SELECT * FROM deadline_reminders WHERE id = %s FOR UPDATE",
            (reminder_id,),
        ).fetchone()
        if reminder is None:
            return {"status": "not_found"}
        if reminder["status"] == "approved" and reminder["pending_date"] is None:
            return {"status": "already_approved", "reminder": reminder}
        if reminder["status"] not in ("proposed", "pending_update", "approved"):
            return {"status": "not_pending", "reminder": reminder}
        task = conn.execute(
            "SELECT * FROM tasks WHERE id = %s", (reminder["task_id"],)
        ).fetchone()
        if task is None:
            return {"status": "not_found"}
        if creds is None:
            return {"status": "needs_auth"}
        event_date = reminder["pending_date"] or reminder["event_date"]
        if reminder["status"] == "pending_update" and reminder["google_event_id"]:
            try:
                ok = update_event_date(
                    creds,
                    calendar_id,
                    reminder["google_event_id"],
                    event_date=event_date,
                )
            except Exception as exc:  # noqa: BLE001
                error_code, _ = classify_error(exc)
                if is_uncertain_calendar_error(exc):
                    return {
                        "status": "event_uncertain",
                        "error_code": error_code,
                    }
                return {"status": "event_failed", "error_code": error_code}
            if not ok:
                return {"status": "event_missing"}
            event_id = reminder["google_event_id"]
        else:
            try:
                event_id = _create_all_day_idempotent(
                    creds,
                    calendar_id,
                    reminder_id=reminder_id,
                    summary=f"DEADLINE: {task['title']}",
                    description=(
                        f"Task ID: {str(task['id'])[:8]}\n"
                        f"Deadline reminder created from Telegram."
                    ),
                    event_date=event_date,
                )
            except Exception as exc:  # noqa: BLE001
                error_code, _ = classify_error(exc)
                if is_uncertain_calendar_error(exc):
                    return {
                        "status": "event_uncertain",
                        "error_code": error_code,
                    }
                return {"status": "event_failed", "error_code": error_code}
        conn.execute(
            """
            UPDATE deadline_reminders
            SET status = 'approved', event_date = %s, pending_date = NULL,
                google_event_id = %s, approved_at = now()
            WHERE id = %s
            """,
            (event_date, event_id, reminder_id),
        )
        conn.execute(
            """
            UPDATE tasks SET deadline_date = %s, deadline_at = %s
            WHERE id = %s
            """,
            (event_date, deadline_end(event_date), task["id"]),
        )
    history.record(
        "deadline_approved",
        task_id=str(task["id"]),
        details={"reminder_id": reminder_id, "event_id": event_id},
    )
    return {
        "status": "approved",
        "reminder_id": reminder_id,
        "task_id": str(task["id"]),
        "task_title": task["title"],
        "event_date": event_date,
        "event_id": event_id,
    }


def set_reminder_only(task_id: str, value: bool = True) -> dict:
    with get_conn_dict() as conn:
        task = conn.execute(
            "SELECT id, title FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if task is None:
            return {"status": "not_found"}
        conn.execute(
            "UPDATE tasks SET reminder_only = %s WHERE id = %s",
            (value, task_id),
        )
        if value:
            conn.execute(
                """
                UPDATE schedule_proposals
                SET status = 'rejected', rejected_at = now()
                WHERE task_id = %s AND status = 'pending'
                """,
                (task_id,),
            )
            conn.execute(
                """
                UPDATE task_sessions
                SET status = 'cancelled'
                WHERE task_id = %s AND status = 'planned'
                  AND calendar_event_id IS NULL
                """,
                (task_id,),
            )
    history.record(
        "reminder_only" if value else "preparation_enabled",
        task_id=task_id,
    )
    return {"status": "ok", "task_id": task_id, "reminder_only": value}


def change_deadline(task_id: str, new_date: date) -> dict:
    with get_conn_dict() as conn:
        task = conn.execute(
            "SELECT * FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if task is None:
            return {"status": "not_found"}
        reminder = conn.execute(
            "SELECT * FROM deadline_reminders WHERE task_id = %s FOR UPDATE",
            (task_id,),
        ).fetchone()
        if reminder is None:
            reminder = conn.execute(
                """
                INSERT INTO deadline_reminders (task_id, event_date)
                VALUES (%s, %s) RETURNING *
                """,
                (task_id, new_date),
            ).fetchone()
            conn.execute(
                "UPDATE tasks SET deadline_date = %s, deadline_at = %s WHERE id = %s",
                (new_date, deadline_end(new_date), task_id),
            )
            return {"status": "proposed", "reminder": reminder}
        if reminder["status"] == "approved":
            reminder = conn.execute(
                """
                UPDATE deadline_reminders
                SET pending_date = %s, status = 'pending_update'
                WHERE id = %s RETURNING *
                """,
                (new_date, reminder["id"]),
            ).fetchone()
            return {"status": "pending_update", "reminder": reminder}
        reminder = conn.execute(
            """
            UPDATE deadline_reminders
            SET event_date = %s, pending_date = NULL, status = 'proposed'
            WHERE id = %s RETURNING *
            """,
            (new_date, reminder["id"]),
        ).fetchone()
        conn.execute(
            "UPDATE tasks SET deadline_date = %s, deadline_at = %s WHERE id = %s",
            (new_date, deadline_end(new_date), task_id),
        )
    return {"status": "proposed", "reminder": reminder}


def overdue_tasks(now: datetime | None = None) -> list[dict]:
    today = (now or datetime.now(UTC)).date()
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT t.id::text AS task_id, t.title, t.deadline_date
            FROM tasks t
            WHERE t.deadline_date IS NOT NULL
              AND t.deadline_date < %s
              AND t.status NOT IN ('completed', 'cancelled')
              AND t.overdue_notified_at IS NULL
            """,
            (today,),
        ).fetchall()


def mark_overdue_notified(task_ids: list[str]) -> None:
    if not task_ids:
        return
    with get_conn_dict() as conn:
        conn.execute(
            """
            UPDATE tasks SET overdue_notified_at = now()
            WHERE id = ANY(%s::uuid[])
            """,
            (task_ids,),
        )
