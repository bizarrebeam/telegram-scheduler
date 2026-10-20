import asyncio
import logging
from datetime import UTC, datetime, timedelta

from telegram.error import RetryAfter, TimedOut

from app.config import get_settings
from app.services import deadlines, jobs, reminders, sessions as sessions_service
from app.services import proposals, settings as settings_service
from app.telegram_bot.ui import (
    fmt_datetime,
    fmt_time,
    missed_keyboard,
    session_keyboard,
)

logger = logging.getLogger(__name__)

TICK_SECONDS = 20
GRACE_MINUTES = 10


def _user_ids() -> list[int]:
    return sorted(get_settings().allowed_telegram_user_ids)


def _timezone_for(user_id: int) -> str:
    row = settings_service.get_or_create_user_settings(user_id)
    return row["timezone"] if row else get_settings().timezone


async def _send(bot, user_id: int, text: str, reply_markup=None) -> None:
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=user_id, text=text, reply_markup=reply_markup
            )
            return
        except RetryAfter as exc:
            await asyncio.sleep(min(exc.retry_after, 10) + 1)
        except TimedOut:
            await asyncio.sleep(2)
    raise RuntimeError(f"send_message failed for user {user_id}")


async def _notify(bot, text: str, reply_markup=None) -> None:
    for user_id in _user_ids():
        await _send(bot, user_id, text, reply_markup)


async def _handle_session_start(bot, session_id: str) -> None:
    session = await asyncio.to_thread(
        sessions_service.get_session_with_task, session_id
    )
    if session is None or session["status"] != "planned":
        return
    if not await asyncio.to_thread(
        reminders.claim, session_id, "session_start", session["planned_start_at"]
    ):
        return
    await asyncio.to_thread(sessions_service.mark_started, session_id, datetime.now(UTC))
    tz = _timezone_for(_user_ids()[0]) if _user_ids() else "Asia/Jakarta"
    text = (
        f"Time to start: {session['task_title']}\n\n"
        f"Your session is scheduled until "
        f"{fmt_time(session['planned_end_at'], tz)} "
        f"({session['planned_duration_minutes']} minutes)."
    )
    first_step = (session.get("first_step") or "").strip()
    if first_step:
        text += f"\n\nStart here: {first_step}"
    await _notify(bot, text, session_keyboard(session_id))
    await asyncio.to_thread(reminders.mark_sent, session_id, "session_start")


async def _handle_pre_end(bot, session_id: str) -> None:
    session = await asyncio.to_thread(
        sessions_service.get_session_with_task, session_id
    )
    if session is None or session["status"] not in ("planned", "in_progress"):
        return
    if not await asyncio.to_thread(
        reminders.claim, session_id, "pre_end", session["planned_end_at"]
    ):
        return
    tz = _timezone_for(_user_ids()[0]) if _user_ids() else "Asia/Jakarta"
    text = (
        f"You have 15 minutes left for {session['task_title']}. "
        f"Your session ends at {fmt_time(session['planned_end_at'], tz)}.\n\n"
        "Do you need more time, or are you finished?"
    )
    await _notify(bot, text, session_keyboard(session_id))
    await asyncio.to_thread(reminders.mark_sent, session_id, "pre_end")


async def _handle_session_end(bot, session_id: str) -> None:
    session = await asyncio.to_thread(
        sessions_service.get_session_with_task, session_id
    )
    if session is None or session["status"] not in ("planned", "in_progress"):
        return
    if not await asyncio.to_thread(
        reminders.claim, session_id, "session_end", session["planned_end_at"]
    ):
        return
    text = (
        f"Your session for {session['task_title']} has ended.\n\n"
        "Did you finish, do you need more time, or would you like to schedule "
        "another session?"
    )
    await _notify(bot, text, session_keyboard(session_id))
    await asyncio.to_thread(reminders.mark_sent, session_id, "session_end")


async def _handle_missed_start(bot, session_id: str) -> None:
    session = await asyncio.to_thread(
        sessions_service.get_session_with_task, session_id
    )
    if session is None or session["status"] != "planned":
        return
    if not await asyncio.to_thread(
        reminders.claim, session_id, "missed_start", session["planned_start_at"]
    ):
        return
    await asyncio.to_thread(sessions_service.set_status, session_id, "missed")
    text = (
        f"Your session for {session['task_title']} hasn't started yet. "
        f"It was scheduled for {fmt_time(session['planned_start_at'], _timezone_for(_user_ids()[0]))}.\n\n"
        "Would you like to start now, reschedule, or skip this session?"
    )
    await _notify(bot, text, missed_keyboard(session_id))
    await asyncio.to_thread(reminders.mark_sent, session_id, "missed_start")


async def _handle_missed_followup(bot, session_id: str) -> None:
    session = await asyncio.to_thread(
        sessions_service.get_session_with_task, session_id
    )
    if session is None or session["status"] != "missed":
        return
    if not await asyncio.to_thread(
        reminders.claim, session_id, "missed_followup", session["planned_start_at"]
    ):
        return
    text = (
        f"Just checking in: your session for {session['task_title']} is still pending.\n\n"
        "I haven't rescheduled it. Choose an option if you'd like to continue."
    )
    await _notify(bot, text, missed_keyboard(session_id))
    await asyncio.to_thread(reminders.mark_sent, session_id, "missed_followup")


async def _handle_deadlines(bot) -> None:
    overdue = await asyncio.to_thread(deadlines.overdue_tasks, datetime.now(UTC))
    for task in overdue:
        user_ids = _user_ids()
        await _notify(
            bot,
            f"Deadline passed: {task['title']} "
            f"({task['deadline_date'].isoformat()}).\n\n"
            "The task is still open. Mark it done when you finish, or use "
            "/deadline <task_id> <date> to change the deadline.",
        )
        await asyncio.to_thread(deadlines.mark_overdue_notified, [task["task_id"]])
        logger.info("notified overdue deadline for %s to %s", task["task_id"], user_ids)


async def _handle_sync_calendar(bot) -> None:
    await _handle_deadlines(bot)
    for user_id in _user_ids():
        missing = await asyncio.to_thread(proposals.sync_external_deletions, user_id)
        for row in missing:
            await _send(
                bot,
                user_id,
                f"The Google Calendar event for \"{row['task_title']}\" "
                f"({fmt_datetime(row['planned_start_at'], _timezone_for(user_id))}) "
                "was deleted.\n\n"
                "I won't recreate it without your approval. Use /plan whenever "
                "you'd like a new time.",
            )


async def _handle(bot, job: dict) -> None:
    kind = job["kind"]
    payload = job["payload"] or {}
    session_id = payload.get("session_id")
    if kind == "session_start":
        await _handle_session_start(bot, session_id)
    elif kind == "session_pre_end":
        await _handle_pre_end(bot, session_id)
    elif kind == "session_end":
        await _handle_session_end(bot, session_id)
    elif kind == "missed_start":
        await _handle_missed_start(bot, session_id)
    elif kind == "missed_followup":
        await _handle_missed_followup(bot, session_id)
    elif kind == "sync_calendar":
        await _handle_sync_calendar(bot)
    else:
        logger.warning("unknown job kind %s", kind)


def _schedule_daily_sync() -> None:
    now = datetime.now(UTC)
    jobs.enqueue(
        "sync_calendar",
        {},
        run_at=now + timedelta(minutes=1),
        idempotency_key=f"sync:{now.date().isoformat()}",
    )


async def run_worker(tg_app) -> None:
    bot = tg_app.bot
    while True:
        try:
            await asyncio.to_thread(jobs.reset_stale_running)
            await asyncio.to_thread(jobs.reap_expired_proposals)
            await asyncio.to_thread(_schedule_daily_sync)
            claimed = await asyncio.to_thread(jobs.claim_due, 10)
            for job in claimed:
                try:
                    await _handle(bot, job)
                    await asyncio.to_thread(jobs.mark_done, job["id"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("job %s (%s) failed: %s", job["id"], job["kind"], exc)
                    await asyncio.to_thread(jobs.mark_failed, job["id"], str(exc))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("worker tick failed")
        await asyncio.sleep(TICK_SECONDS)

