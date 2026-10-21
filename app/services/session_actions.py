import logging
from datetime import UTC, datetime

from app.errors import classify_error, is_uncertain_calendar_error
from app.google.calendar import GoogleAuthRequired, delete_event, update_event_time
from app.google.oauth import load_user_credentials
from app.observability import log_event
from app.services import history, jobs, proposals, reminders
from app.services import sessions as sessions_service
from app.services import settings as settings_service

MAX_EXTENSION_MINUTES = 240


def _calendar_context(telegram_user_id: int) -> tuple[object | None, str]:
    from app.config import get_settings

    creds = load_user_credentials(telegram_user_id)
    user_settings = settings_service.get_or_create_user_settings(telegram_user_id)
    calendar_id = (
        (user_settings or {}).get("google_calendar_id")
        or get_settings().google_calendar_id
        or "primary"
    )
    return creds, calendar_id


def extend_active_session(telegram_user_id: int, minutes: int) -> dict:
    session = sessions_service.latest_active_session()
    if session is None:
        return {"status": "no_active_session"}
    updated = sessions_service.extend(
        str(session["id"]), minutes, max_extra_minutes=MAX_EXTENSION_MINUTES
    )
    if updated is None:
        return {"status": "limit_reached"}
    note = ""
    if session.get("calendar_event_id"):
        creds, calendar_id = _calendar_context(telegram_user_id)
        if creds is not None:
            try:
                ok = update_event_time(
                    creds,
                    calendar_id,
                    session["calendar_event_id"],
                    start=updated["planned_start_at"],
                    end=updated["planned_end_at"],
                )
            except GoogleAuthRequired:
                return {"status": "needs_auth", "session": updated}
            except Exception as exc:  # noqa: BLE001
                error_code, _ = classify_error(exc)
                uncertain = is_uncertain_calendar_error(exc)
                history.record(
                    "calendar_extend_failed",
                    task_id=str(session["task_id"]),
                    session_id=str(session["id"]),
                    details={"error_code": error_code},
                )
                return {
                    "status": "calendar_failed",
                    "session": updated,
                    "error_code": error_code,
                    "uncertain": uncertain,
                }
            if not ok:
                note = " The calendar event wasn't found. It may have been deleted manually."
    history.record(
        "session_extended",
        task_id=str(session["task_id"]),
        session_id=str(session["id"]),
        details={"minutes": minutes},
    )
    return {"status": "extended", "session": updated, "note": note}


def complete_session(session_id: str, *, session: dict | None = None) -> dict:
    session = session or sessions_service.get_session_with_task(session_id)
    if session is None:
        return {"status": "not_found"}
    if session["status"] == "completed":
        return {"status": "already_completed", "session": session}
    updated = sessions_service.mark_completed(session_id, datetime.now(UTC))
    if updated is None:
        return {"status": "not_actionable", "session": session}
    from app.services.tasks import complete_task

    complete_task(str(session["task_id"])[:8])
    jobs.cancel_session_jobs(session_id)
    reminders.mark_response(session_id)
    history.record(
        "session_completed",
        task_id=str(session["task_id"]),
        session_id=session_id,
    )
    return {"status": "completed", "session": updated, "task_title": session["task_title"]}


def start_session_now(session_id: str) -> dict:
    session = sessions_service.get_session_with_task(session_id)
    if session is None:
        return {"status": "not_found"}
    if session["status"] not in ("planned", "missed"):
        return {"status": "not_actionable", "session": session}
    with_updated = sessions_service.mark_started(session_id, datetime.now(UTC))
    if with_updated is None:
        sessions_service.set_status(session_id, "in_progress")
    history.record(
        "session_started_manually",
        task_id=str(session["task_id"]),
        session_id=session_id,
    )
    return {"status": "started", "session": session}


def skip_session(session_id: str) -> dict:
    session = sessions_service.get_session_with_task(session_id)
    if session is None:
        return {"status": "not_found"}
    if session["status"] in ("completed", "cancelled"):
        return {"status": "already_closed", "session": session}
    sessions_service.set_status(session_id, "cancelled")
    jobs.cancel_session_jobs(session_id)
    history.record(
        "session_skipped",
        task_id=str(session["task_id"]),
        session_id=session_id,
    )
    return {"status": "skipped", "session": session}


def reschedule_session(session_id: str, telegram_user_id: int) -> dict:
    session = sessions_service.get_session_with_task(session_id)
    if session is None:
        return {"status": "not_found"}
    if session["status"] in ("completed", "cancelled"):
        return {"status": "already_closed", "session": session}
    if session.get("calendar_event_id"):
        creds, calendar_id = _calendar_context(telegram_user_id)
        if creds is not None:
            try:
                delete_event(creds, calendar_id, session["calendar_event_id"])
            except GoogleAuthRequired:
                return {"status": "needs_auth", "session": session}
            except Exception as exc:  # noqa: BLE001
                error_code, _ = classify_error(exc)
                log_event(
                    logging.WARNING,
                    "calendar_delete_failed",
                    operation="reschedule",
                    outcome="error",
                    error_type=type(exc).__name__,
                    error_code=error_code,
                )
                return {
                    "status": "calendar_failed",
                    "session": session,
                    "error_code": error_code,
                    "uncertain": is_uncertain_calendar_error(exc),
                }
    sessions_service.set_status(session_id, "cancelled")
    jobs.cancel_session_jobs(session_id)
    history.record(
        "session_rescheduled",
        task_id=str(session["task_id"]),
        session_id=session_id,
    )
    try:
        result = proposals.propose_for_task(
            str(session["task_id"]), telegram_user_id
        )
    except Exception as exc:  # noqa: BLE001
        error_code, _ = classify_error(exc)
        log_event(
            logging.WARNING,
            "reschedule_proposal_failed",
            operation="reschedule",
            outcome="error",
            error_type=type(exc).__name__,
            error_code=error_code,
        )
        return {
            "status": "proposal_failed",
            "error_code": error_code,
            "previous_session_id": session_id,
        }
    result["previous_session_id"] = session_id
    return result
