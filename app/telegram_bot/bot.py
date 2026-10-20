import asyncio
import logging
import re
from datetime import UTC, date, datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.error import RetryAfter, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import get_settings
from app.google.oauth import create_authorization_state, has_credentials
from app.parsing.ai import parse_message
from app.parsing.basic import (
    is_overwhelm,
    parse_deadline_date,
    parse_extension,
)
from app.services import deadlines, overwhelm, routines, session_actions
from app.services import sessions as sessions_service
from app.services import settings as settings_service
from app.services.attachments import count_for_task, list_for_task, save_attachment
from app.services.proposals import approve_proposal, propose_for_task, reject_proposal
from app.services.tasks import (
    cancel_task,
    complete_task,
    create_subtasks,
    create_task,
    create_task_from_message,
    delete_task,
    find_task_by_source_message,
    get_task,
    get_task_details,
    list_open_tasks,
    resolve_task_id,
    set_duration,
    skip_task,
)
from app.telegram_bot.auth import is_authorized
from app.telegram_bot.ui import (
    after_action_keyboard,
    agenda_keyboard,
    choose_task_keyboard,
    deadline_keyboard,
    deadline_text,
    fmt_datetime,
    fmt_range,
    fmt_short_datetime,
    fmt_time,
    overwhelm_keyboard,
    proposal_keyboard,
    proposal_text,
    queue_keyboard,
    reschedule_offer_keyboard,
    selection_keyboard,
    session_keyboard,
    short_id,
    task_details_keyboard,
    task_keyboard,
    today_keyboard,
)

logger = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
PROGRESS_INTERVAL_SECONDS = 12
PROGRESS_TEXT = (
    "Still working on this. This is taking longer than expected, but your "
    "request is still being processed."
)
_INFLIGHT: set[tuple[int, str]] = set()
CALLBACK_TOASTS = {
    "approve": "Got it. Checking your calendar...",
    "reject": "Got it. Updating your task...",
    "another": "Got it. Finding another time...",
    "done": "Got it. Marking this session as complete...",
    "addtime": "Got it. Extending your session...",
    "reschedule": "Got it. Finding a new time...",
    "skip": "Got it. Skipping this session...",
    "startnow": "Got it. Starting your session...",
    "schedule": "Got it. Checking your calendar...",
    "view": "Got it. Opening your task...",
    "cancel": "Got it. Updating your task...",
    "delete": "Got it. Updating your task...",
    "files": "Got it. Loading attachments...",
    "page": "Got it. Loading tasks...",
    "nexttask": "Got it. Finding another task...",
    "dismiss": "Okay.",
    "today": "Got it. Loading today...",
    "agenda": "Got it. Loading your agenda...",
    "picktask": "Got it. Loading your tasks...",
    "selecttask": "Got it. Selecting your task...",
    "deadline_ok": "Got it. Creating the all-day reminder...",
    "deadline_no": "Got it. Updating the deadline...",
    "deadline_only": "Got it. Keeping this as a reminder only...",
    "deadline_edit": "Got it. Opening the deadline editor...",
    "prep": "Got it. Looking for preparation time...",
}

START_TEXT = (
    "Hi! I'm your scheduling assistant.\n"
    "Send me any task, link, or screenshot. I'll save it and help you find a "
    "time to work on it.\n\n"
    "Commands\n"
    "/today - Your agenda and next step\n"
    "/queue - Your unscheduled tasks\n"
    "/plan <id> - Find a time for a task\n"
    "/done <id> - Mark a task as complete\n"
    "/help - See all commands\n\n"
    "You can also send tasks in your own words. I'll take it from there."
)

HELP_TEXT = (
    "How to use me\n\n"
    "Add a task\n"
    "Send a regular message, for example: "
    "\"Apply for the MT program at Company X tomorrow. It'll probably take an hour.\"\n"
    "You can also send links, photos, screenshots, or documents.\n\n"
    "Attachments\n"
    "- Reply to a task message with a photo or file to attach it to that task.\n"
    "- /files <task_id> - Retrieve a task's attachments.\n\n"
    "During a session\n"
    "- Reply \"+30 minutes\" or \"add 20 minutes\" to request more time.\n"
    "- Type \"overwhelmed\" or \"I'm stuck\" when you need help choosing what to do next.\n\n"
    "Tasks and schedules\n"
    "- /today - See today's agenda and your next step\n"
    "- /queue - See tasks that haven't been scheduled\n"
    "- /plan <task_id> - Request a time proposal for a task\n"
    "- /plan - Request a proposal for your most recently added task\n"
    "- /done <task_id> - Mark a task as complete\n"
    "- /reschedule <task_id> - Find a new time for a task\n"
    "- /deadline <task_id> <date> - Add or change an all-day deadline\n"
    "- /duration <task_id> <minutes> - Update the preparation estimate\n"
    "- /skip <task_id> - Skip the current session\n"
    "- /cancel <task_id> - Cancel a task\n"
    "- /delete <task_id> - Delete a task (confirmation required)\n\n"
    "Break tasks into steps\n"
    "- /overwhelm - I'll suggest one small next step.\n"
    "- /breakdown <task_id> | step 1 ; step 2 - Split a task into smaller steps.\n\n"
    "Routines\n"
    "- /routines - Check your weekly routine progress\n"
    "- /routine <title> | <minutes> | <target/week> - Add a routine\n\n"
    "Settings\n"
    "- /auth - Connect or reconnect Google Calendar\n"
    "- /help - Show this message"
)


async def _authorized(update: Update) -> bool:
    settings = get_settings()
    user = update.effective_user
    user_id = user.id if user else None
    if not is_authorized(user_id, settings):
        logger.warning("rejected unauthorized telegram user_id=%s", user_id)
        return False
    return True


async def _reply(message, text: str, reply_markup=None):
    for attempt in range(2):
        try:
            return await message.reply_text(
                text,
                reply_markup=reply_markup,
                link_preview_options=NO_PREVIEW,
            )
        except RetryAfter as exc:
            if attempt == 0:
                await asyncio.sleep(min(exc.retry_after, 10) + 1)
                continue
            logger.warning("reply dropped after RetryAfter")
        except TimedOut:
            if attempt == 0:
                await asyncio.sleep(2)
                continue
            logger.warning("reply dropped after timeout")
    return None


async def _update_status(message, status, text: str, reply_markup=None) -> None:
    if status is not None:
        try:
            await status.edit_text(
                text,
                reply_markup=reply_markup,
                link_preview_options=NO_PREVIEW,
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.info("status edit failed, sending a new message: %s", exc)
    await _reply(message, text, reply_markup=reply_markup)


async def _edit_message(message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            link_preview_options=NO_PREVIEW,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("message edit failed, sending a new message: %s", exc)
        await _reply(message, text, reply_markup=reply_markup)


def _claim_request(user_id: int, data: str) -> bool:
    key = (user_id, data)
    if key in _INFLIGHT:
        return False
    _INFLIGHT.add(key)
    return True


def _release_request(user_id: int, data: str) -> None:
    _INFLIGHT.discard((user_id, data))


async def _progress_tick(set_status, interval: float) -> None:
    try:
        await asyncio.sleep(interval)
        await set_status(PROGRESS_TEXT)
    except asyncio.CancelledError:
        pass


async def _with_progress(set_status, status_text: str, work, interval: float | None = None):
    await set_status(status_text)
    ticker = asyncio.create_task(
        _progress_tick(
            set_status,
            PROGRESS_INTERVAL_SECONDS if interval is None else interval,
        )
    )
    try:
        return await work()
    finally:
        ticker.cancel()


def _timezone(user_id: int) -> str:
    row = settings_service.get_or_create_user_settings(user_id)
    return row["timezone"] if row else get_settings().timezone


def _proposal_message(result: dict, tz: str) -> tuple[str, InlineKeyboardMarkup | None]:
    status = result.get("status")
    if status == "proposed":
        proposal = result["proposal"]
        task = result["task"]
        text = proposal_text(
            task_title=task["title"],
            start=proposal["proposed_start_at"],
            end=proposal["proposed_end_at"],
            duration_minutes=task["estimated_duration_minutes"],
            deadline_at=task.get("deadline_at"),
            timezone=tz,
        )
        return text, proposal_keyboard(str(proposal["id"]))
    if status == "needs_auth":
        return (
            "Your task is saved, but I can't check your calendar yet. Connect "
            "Google Calendar with /auth, then try scheduling again.",
            None,
        )
    if status == "no_slot":
        return (
            "I couldn't find an available time in the next 14 days during your "
            "usual hours. Your task is still saved.",
            None,
        )
    if status == "deadline_unmet":
        task = result.get("task") or {}
        deadline = task.get("deadline_at")
        when = f" ({fmt_datetime(deadline, tz)})" if deadline else ""
        return (
            f"I couldn't find a slot that ends before your deadline{when} during "
            "your available hours.\n\n"
            "You may need to move the deadline or add more available time.",
            None,
        )
    if status == "not_found":
        return ("I couldn't find that task. Check /queue for its ID.", None)
    if status == "not_actionable":
        return (
            "This task is already completed or cancelled, so it can't be scheduled.",
            None,
        )
    if status == "calendar_failed":
        return (
            "I couldn't update the Google Calendar event. Your existing session "
            "is still scheduled. Please try again shortly.",
            None,
        )
    if status == "already_closed":
        return ("This session is already closed.", None)
    return ("I couldn't find a time for this task. Please try again. Your task is still saved.", None)


async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    user_id = update.effective_user.id
    _, url = create_authorization_state(user_id)
    connected = await asyncio.to_thread(has_credentials, user_id)
    intro = (
        "Google Calendar is connected.\n\n"
        "Use this link to refresh your connection or switch accounts:\n"
        if connected
        else "Connect your Google Calendar using this link:\n"
    )
    await _reply(
        update.message,
        f"{intro}{url}\n\nFinish signing in in your browser, then return to Telegram.",
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    await _reply(update.message, START_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    await _reply(update.message, HELP_TEXT)


def _duration_label(minutes: int) -> str:
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if minutes > 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} h {rest} min"
    return f"{minutes} min"


async def _today_payload(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    tz = await asyncio.to_thread(_timezone, user_id)
    result = await asyncio.to_thread(overwhelm.pick_next_action, user_id)
    now = datetime.now(UTC)
    start, end = sessions_service.day_bounds(date.today(), tz)
    plan = await asyncio.to_thread(
        sessions_service.sessions_between, start, end, timezone=tz
    )
    active = next(
        (s for s in plan if s["planned_start_at"] <= now < s["planned_end_at"]),
        None,
    )
    upcoming = next((s for s in plan if s["planned_start_at"] > now), None)
    task = result.get("task")

    if active is not None:
        lines = [
            "Current session",
            "",
            active["task_title"],
            fmt_range(active["planned_start_at"], active["planned_end_at"], tz),
            "",
            "Finish this first. Then we can look at what's next.",
        ]
        return "\n".join(lines), session_keyboard(str(active["id"]))

    if task is not None:
        short = short_id(str(task["id"]))
        session = await asyncio.to_thread(
            sessions_service.session_for_task, str(task["id"])
        )
        startable = bool(session and session["status"] == "planned")
        if not plan:
            lines = [
                "Nothing is scheduled for today yet.",
                "",
                "Start here",
                "",
                task["title"],
                f"Estimated time: {_duration_label(task.get('estimated_duration_minutes') or 60)}",
                "",
                result["first_step"],
            ]
        else:
            lines = [
                "Your next step",
                "",
                task["title"],
                f"Estimated time: {_duration_label(task.get('estimated_duration_minutes') or 60)}",
                "",
                "Start here",
                "",
                result["first_step"],
            ]
            if upcoming is not None:
                lines += [
                    "",
                    "Up next",
                    "",
                    f"{fmt_time(upcoming['planned_start_at'], tz)}\u2013"
                    f"{fmt_time(upcoming['planned_end_at'], tz)} \u00b7 "
                    f"{upcoming['task_title']}",
                ]
        if session and session["status"] in ("planned", "in_progress"):
            for index, line in enumerate(lines):
                if line.startswith("Estimated time:"):
                    lines.insert(
                        index + 1,
                        "Scheduled: "
                        + fmt_range(
                            session["planned_start_at"],
                            session["planned_end_at"],
                            tz,
                        ),
                    )
                    break
        markup = today_keyboard(
            short,
            str(session["id"]) if session else None,
            startable=startable,
            has_agenda=bool(plan),
        )
        return "\n".join(lines), markup

    if not plan:
        return (
            "Nothing is scheduled for today, and your task queue is empty. "
            "Send me a task whenever you're ready.",
            None,
        )

    lines = ["Today's plan", ""]
    remaining = [s for s in plan if s is not active and s is not upcoming]
    if remaining:
        agenda = [
            f"- {fmt_time(s['planned_start_at'], tz)}\u2013"
            f"{fmt_time(s['planned_end_at'], tz)} \u00b7 {s['task_title']}"
            for s in remaining[:3]
        ]
        lines += ["", "Later today", ""] + agenda

    return "\n".join(lines), agenda_keyboard()


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    text, markup = await _today_payload(update.effective_user.id)
    await _reply(update.message, text, reply_markup=markup)


def _overwhelm_text(task: dict, first_step: str) -> str:
    return (
        "Let's start small.\n\n"
        "Your next step\n\n"
        f"{task['title']}\n\n"
        f"{first_step}"
    )


async def _send_overwhelm(update: Update, user_id: int) -> None:
    result = await asyncio.to_thread(overwhelm.pick_next_action, user_id)
    task = result.get("task")
    if not task:
        await _reply(
            update.message,
            "You don't have any open tasks yet. Send me a task, and I'll help you "
            "choose a first step.",
        )
        return
    short = short_id(str(task["id"]))
    session = await asyncio.to_thread(
        sessions_service.session_for_task, str(task["id"])
    )
    await _reply(
        update.message,
        _overwhelm_text(task, result["first_step"]),
        reply_markup=overwhelm_keyboard(
            short, str(session["id"]) if session else None
        ),
    )


async def overwhelm_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    await _send_overwhelm(update, update.effective_user.id)


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    user_id = update.effective_user.id
    short = context.args[0].strip() if context.args else None
    if short:
        task_id = await asyncio.to_thread(resolve_task_id, short)
        if task_id is None:
            await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
            return
        task = await asyncio.to_thread(get_task, short)
        if task is not None and task[2] in ("completed", "cancelled", "skipped"):
            await _reply(
                update.message,
                "This task can't be scheduled in its current state.",
            )
            return
    else:
        from app.services.tasks import latest_open_task_id

        task_id = await asyncio.to_thread(latest_open_task_id)
        if task_id is None:
            await _reply(
                update.message,
                "You don't have a task to schedule yet. Send me a task first.",
            )
            return
    session = await asyncio.to_thread(sessions_service.session_for_task, task_id)
    if session is not None:
        await _reply(
            update.message,
            "This task is already scheduled for "
            f"{fmt_datetime(session['planned_start_at'], await asyncio.to_thread(_timezone, user_id))}.\n\n"
            "Use the buttons below to manage it.",
            reply_markup=session_keyboard(str(session["id"])),
        )
        return
    if not await asyncio.to_thread(has_credentials, user_id):
        await _reply(
            update.message,
            "Your task is saved, but I can't check your calendar yet. Connect "
            "Google Calendar with /auth, then try scheduling again.",
        )
        return
    tz = await asyncio.to_thread(_timezone, user_id)
    progress = await _reply(
        update.message,
        "Finding a time for your task\n\n"
        "I'm checking your calendar for an available slot.",
    )
    try:
        result = await _with_progress(
            lambda text: _update_status(update.message, progress, text),
            "Finding a time for your task\n\n"
            "I'm checking your calendar for an available slot.",
            lambda: asyncio.to_thread(propose_for_task, task_id, user_id),
        )
    except Exception:  # noqa: BLE001
        logger.exception("plan failed for task %s", task_id)
        await _update_status(
            update.message,
            progress,
            "I couldn't check your calendar just now. Please try again shortly.",
        )
        return
    text, markup = _proposal_message(result, tz)
    if result.get("status") == "proposed":
        text = "Proposed session\n\n" + text
    await _update_status(update.message, progress, text, reply_markup=markup)


async def routines_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    user_id = update.effective_user.id
    tz = await asyncio.to_thread(_timezone, user_id)
    items = await asyncio.to_thread(routines.progress, datetime.now(UTC), tz)
    if not items:
        await _reply(
            update.message,
            "You haven't added any routines yet.\n\n"
            "Add one with /routine Exercise | 45 | 3.",
        )
        return
    lines = [
        f"- {i['routine']['title']}: {i['completed_this_week']}/{i['target']} "
        f"sessions completed ({i['remaining']} remaining)"
        for i in items
    ]
    await _reply(update.message, "Your routine progress this week:\n" + "\n".join(lines))
    pending = [i for i in items if i["remaining"] > 0]
    if not pending:
        await _reply(update.message, "You've completed all your routine targets for this week.")
        return
    if not await asyncio.to_thread(has_credentials, user_id):
        await _reply(
            update.message,
            "Connect Google Calendar with /auth so I can suggest times for your "
            "routine sessions.",
        )
        return
    target = max(pending, key=lambda i: i["remaining"])
    instance = await asyncio.to_thread(routines.create_routine_instance, target["routine"])
    instance_id = str(instance["id"])
    result = await asyncio.to_thread(propose_for_task, instance_id, user_id)
    text, markup = _proposal_message(result, tz)
    if result.get("status") == "proposed":
        await _reply(
            update.message,
            f"Proposed schedule for \"{target['routine']['title']}\":\n\n{text}",
            reply_markup=markup,
        )
    else:
        await _reply(update.message, text)


async def routine_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    raw = " ".join(context.args or []).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) != 3:
        await _reply(
            update.message,
            "Use /routine <title> | <minutes per session> | <target per week>\n"
            "Example: /routine Exercise | 45 | 3",
        )
        return
    title, minutes_raw, target_raw = parts
    try:
        minutes = int(minutes_raw)
        target = int(target_raw)
    except ValueError:
        await _reply(update.message, "Duration and weekly target must be numbers.")
        return
    if not title or minutes <= 0 or target <= 0:
        await _reply(
            update.message,
            "Please provide a title, a duration greater than zero, and a weekly "
            "target greater than zero.",
        )
        return
    routine = await asyncio.to_thread(
        routines.add_routine,
        title=title,
        session_duration_minutes=minutes,
        target_sessions_per_week=target,
    )
    await _reply(
        update.message,
        f"Routine saved: {routine['title']} "
        f"({routine['session_duration_minutes']} minutes per session, "
        f"{routine['target_sessions_per_week']} times per week).\n\n"
        "I'll help you schedule sessions when you still have weekly targets to complete.",
    )


async def deadline_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    args = context.args or []
    if len(args) < 2:
        await _reply(
            update.message,
            "Use /deadline <task_id> <date>\n"
            "Example: /deadline abcd1234 2026-10-25",
        )
        return
    short = args[0].strip()
    new_date = parse_deadline_date(" ".join(args[1:]))
    if new_date is None:
        await _reply(
            update.message,
            "I couldn't read that date. Use YYYY-MM-DD or a date like "
            "25 October 2026.",
        )
        return
    details = await asyncio.to_thread(get_task_details, short)
    task_id = await asyncio.to_thread(resolve_task_id, short)
    if details is None or task_id is None:
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
        return
    result = await asyncio.to_thread(deadlines.change_deadline, task_id, new_date)
    status = result.get("status")
    if status not in ("proposed", "pending_update"):
        await _reply(update.message, "I couldn't update that deadline just now.")
        return
    reminder = result["reminder"]
    prefix = (
        "Deadline change proposed. Approve it before I update the calendar.\n\n"
        if status == "pending_update"
        else ""
    )
    await _reply(
        update.message,
        prefix
        + deadline_text(
            details["title"],
            new_date,
            details["estimated_duration_minutes"],
            details.get("duration_source") or "estimate",
            preparation_scheduled=False,
        ),
        reply_markup=deadline_keyboard(str(reminder["id"]), short),
    )


async def duration_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    args = context.args or []
    if len(args) < 2:
        await _reply(update.message, "Use /duration <task_id> <minutes>.")
        return
    short = args[0].strip()
    try:
        minutes = int(args[1])
    except ValueError:
        await _reply(update.message, "Duration must be a number of minutes.")
        return
    if not 5 <= minutes <= 600:
        await _reply(update.message, "Duration must be between 5 and 600 minutes.")
        return
    result = await asyncio.to_thread(set_duration, short, minutes)
    if result == "updated":
        await _reply(
            update.message,
            f"Estimated time updated to {minutes} minutes.\n\n"
            "Existing approved sessions were not moved. Use /reschedule <task_id> "
            "to recalculate a slot for the new duration.",
        )
    elif result == "not_found":
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
    else:
        await _reply(update.message, "This task can't be edited in its current state.")


async def breakdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    raw = " ".join(context.args or []).strip()
    if "|" not in raw:
        await _reply(
            update.message,
            "Use /breakdown <task_id> | step 1 ; step 2 ; step 3",
        )
        return
    short, _, steps_raw = raw.partition("|")
    task_id = await asyncio.to_thread(resolve_task_id, short.strip())
    task = await asyncio.to_thread(get_task, short.strip())
    if task_id is None or task is None:
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
        return
    steps = [s.strip() for s in steps_raw.split(";") if s.strip()]
    if not steps:
        await _reply(update.message, "Add at least one step after the | symbol.")
        return
    created = await asyncio.to_thread(create_subtasks, task_id, steps)
    if not created:
        await _reply(
            update.message,
            "I couldn't create the steps. Please check the format and try again.",
        )
        return
    lines = [f"- {short_id} \u00b7 {title}" for short_id, title in created]
    await _reply(
        update.message,
        f"Split \"{task[1]}\" into {len(created)} steps:\n" + "\n".join(lines),
    )


async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    rows = list_open_tasks()
    if not rows:
        await _reply(
            update.message,
            "Your queue is empty. Send me a task whenever you're ready.",
        )
        return
    text, markup = _queue_view(rows, 0)
    await _reply(update.message, text, reply_markup=markup)


QUEUE_PAGE_SIZE = 5


def _queue_view(rows: list[tuple], page: int) -> tuple[str, InlineKeyboardMarkup | None]:
    pages = max(1, -(-len(rows) // QUEUE_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = rows[page * QUEUE_PAGE_SIZE : (page + 1) * QUEUE_PAGE_SIZE]
    lines = ["Waiting to be scheduled", ""]
    for index, (_short, title, minutes) in enumerate(
        chunk, start=page * QUEUE_PAGE_SIZE + 1
    ):
        lines.append(f"{index}. {title}")
        lines.append(f"Estimated time: {minutes} min")
        lines.append("")
    text = "\n".join(lines).rstrip()
    if pages > 1:
        text += f"\n\nPage {page + 1} of {pages}"
    return text, queue_keyboard(chunk, page, QUEUE_PAGE_SIZE, total=len(rows))


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    if not context.args:
        await _reply(update.message, "Use /done <task_id>. You can find the ID in /queue.")
        return
    short = context.args[0].strip()
    result = complete_task(short)
    if result == "completed":
        task = get_task(short)
        title = task[1] if task else "Task"
        await _reply(update.message, f"Done: {title}. Nice work.")
    elif result == "not_found":
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
    else:
        await _reply(
            update.message,
            "This task can't be marked as complete in its current state.",
        )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    if not context.args:
        await _reply(update.message, "Use /cancel <task_id>. You can find the ID in /queue.")
        return
    short = context.args[0].strip()
    result = cancel_task(short)
    if result == "cancelled":
        task = get_task(short)
        title = task[1] if task else "Task"
        await _reply(update.message, f"Cancelled: {title}.")
    elif result == "not_found":
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
    else:
        await _reply(update.message, "This task can't be cancelled in its current state.")


async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    if not context.args:
        await _reply(update.message, "Use /skip <task_id>. You can find the task in /queue.")
        return
    short = context.args[0].strip()
    task = await asyncio.to_thread(get_task, short)
    if task is None:
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
        return
    task_id = await asyncio.to_thread(resolve_task_id, short)
    session = (
        await asyncio.to_thread(sessions_service.session_for_task, task_id)
        if task_id
        else None
    )
    if session is None:
        await _reply(
            update.message,
            "There isn't an active session to skip. Your task is still in your queue.",
        )
        return
    result = await asyncio.to_thread(skip_task, short)
    if result == "skipped":
        await _reply(
            update.message,
            "Skipped this session. The task remains in your queue unless it was "
            "explicitly cancelled.",
        )
    else:
        await _reply(
            update.message,
            "This session can't be skipped in its current state.",
        )


async def reschedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    if not context.args:
        await _reply(
            update.message,
            "Use /reschedule <task_id>. You can find the task in /queue.",
        )
        return
    short = context.args[0].strip()
    task = await asyncio.to_thread(get_task, short)
    if task is None:
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
        return
    task_id = await asyncio.to_thread(resolve_task_id, short)
    if task_id is None:
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
        return
    session = await asyncio.to_thread(sessions_service.session_for_task, task_id)
    if session is None:
        await _reply(
            update.message,
            "This task doesn't have an active scheduled session. Would you like "
            "me to find a time for it?",
            reply_markup=reschedule_offer_keyboard(short),
        )
        return
    user_id = update.effective_user.id
    tz = await asyncio.to_thread(_timezone, user_id)
    progress = await _reply(
        update.message,
        "Finding a new time\n\nI'm checking your calendar and moving the session.",
    )
    try:
        result = await _with_progress(
            lambda text: _update_status(update.message, progress, text),
            "Finding a new time\n\nI'm checking your calendar and moving the session.",
            lambda: asyncio.to_thread(
                session_actions.reschedule_session, str(session["id"]), user_id
            ),
        )
    except Exception:  # noqa: BLE001
        logger.exception("reschedule failed for task %s", short)
        await _update_status(
            update.message,
            progress,
            "I couldn't reschedule just now. Your task is unchanged. Please try again shortly.",
        )
        return
    text, markup = _proposal_message(result, tz)
    if result.get("status") == "proposed":
        text = "Previous session cancelled.\n\nProposed session\n\n" + text
    await _update_status(update.message, progress, text, reply_markup=markup)


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    if not context.args:
        await _reply(update.message, "Use /delete <task_id>. You can find the ID in /queue.")
        return
    short_id = context.args[0].strip()
    pending = context.user_data.get("pending_delete")
    if isinstance(pending, dict) and pending.get("id") == short_id:
        context.user_data.pop("pending_delete", None)
        result = delete_task(short_id)
        if result == "deleted":
            await _reply(update.message, f"Deleted: {pending.get('title', 'task')}.")
        else:
            await _reply(update.message, "I couldn't delete this task. Please try again.")
        return
    task = get_task(short_id)
    if task is None:
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
        return
    context.user_data["pending_delete"] = {"id": short_id, "title": task[1]}
    await _reply(
        update.message,
        f"Are you sure you want to delete \"{task[1]}\"?\n\n"
        f"Send /delete {short_id} again to confirm.",
    )


async def capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    text = update.message.text
    if not text or not text.strip():
        return
    user_id = update.effective_user.id

    extension = parse_extension(text)
    if extension is not None:
        result = await asyncio.to_thread(session_actions.extend_active_session, user_id, extension)
        status = result["status"]
        if status == "extended":
            session = result["session"]
            tz = await asyncio.to_thread(_timezone, user_id)
            await _reply(
                update.message,
                f"Added {extension} minutes. Your session now ends at "
                f"{fmt_time(session['planned_end_at'], tz)}.{result.get('note', '')}",
            )
            return
        if status == "no_active_session":
            await _reply(
                update.message,
                "You don't have an active session to extend. You can choose a "
                "task from /queue or check /today.",
            )
            return
        if status == "limit_reached":
            await _reply(
                update.message,
                "You've reached the extension limit for this session. You can "
                "schedule another session if you need more time.",
            )
            return
        if status == "needs_auth":
            await _reply(
                update.message,
                "Google Calendar needs to be reconnected. Use /auth.",
            )
            return
        if status == "calendar_failed":
            await _reply(
                update.message,
                "I couldn't update the Google Calendar event. Your local session "
                "time was extended, but the calendar may still show the previous "
                "end time.",
            )
            return

    if is_overwhelm(text):
        await _send_overwhelm(update, user_id)
        return

    status_message = await _reply(update.message, "Saving this task\u2026")
    parsed = await asyncio.to_thread(parse_message, text)
    row = await asyncio.to_thread(
        create_task_from_message,
        title=parsed.title,
        description=parsed.description,
        category=parsed.category,
        estimated_duration_minutes=parsed.estimated_duration_minutes,
        duration_source=parsed.duration_source,
        first_step=parsed.first_step,
        deadline_at=parsed.deadline_at,
        deadline_date=parsed.deadline_date,
        reminder_only=parsed.reminder_only,
        telegram_message_id=update.message.message_id,
        telegram_update_id=update.update_id,
    )
    if row is None:
        await _update_status(
            update.message,
            status_message,
            "This message was already saved. Nothing new was added.",
        )
        return
    short, title = row
    saved = (
        f"Saved: {title}\n"
        f"Task ID: {short} \u00b7 Estimated time: "
        f"{parsed.estimated_duration_minutes} minutes"
    )
    if parsed.deadline_date:
        saved += f"\nDeadline: {parsed.deadline_date.isoformat()} (all-day)"
    task_id = await asyncio.to_thread(resolve_task_id, short)

    if parsed.deadline_date and task_id:
        deadline_result = await asyncio.to_thread(deadlines.propose_deadline, task_id)
        if deadline_result.get("status") == "proposed":
            reminder = deadline_result["reminder"]
            tz = await asyncio.to_thread(_timezone, user_id)
            await _update_status(
                update.message, status_message, saved
            )
            await _reply(
                update.message,
                deadline_text(
                    title,
                    parsed.deadline_date,
                    parsed.estimated_duration_minutes,
                    parsed.duration_source,
                    preparation_scheduled=not parsed.reminder_only,
                ),
                reply_markup=deadline_keyboard(
                    str(reminder["id"]), short
                ),
            )

    if parsed.reminder_only:
        if task_id:
            await asyncio.to_thread(deadlines.set_reminder_only, task_id, True)
        await _update_status(
            update.message,
            status_message,
            saved
            + "\n\nReminder only. I won't schedule preparation unless you ask.",
            reply_markup=task_keyboard(short),
        )
        return

    if parsed.deadline_unclear:
        await _update_status(
            update.message,
            status_message,
            saved
            + "\n\nWhat date is the deadline? Reply with "
            f"/deadline {short} YYYY-MM-DD.",
            reply_markup=task_keyboard(short),
        )
        return

    if not await asyncio.to_thread(has_credentials, user_id):
        await _update_status(
            update.message,
            status_message,
            saved
            + "\n\nConnect Google Calendar with /auth, and I'll find a time for you.",
            reply_markup=task_keyboard(short),
        )
        return
    if task_id is None:
        await _update_status(
            update.message,
            status_message,
            saved + "\n\nI couldn't start the calendar search just now. Please try again shortly.",
            reply_markup=task_keyboard(short),
        )
        return
    tz = await asyncio.to_thread(_timezone, user_id)
    await _update_status(
        update.message,
        status_message,
        saved + "\n\nI'm checking your calendar for a time that fits.",
    )
    try:
        result = await asyncio.to_thread(propose_for_task, task_id, user_id)
    except Exception:  # noqa: BLE001
        logger.exception("proposal generation failed for task %s", task_id)
        await _update_status(
            update.message,
            status_message,
            saved
            + "\n\nI couldn't check your calendar just now. Your task is still "
            "saved. Please try again shortly.",
            reply_markup=task_keyboard(short),
        )
        return
    body, markup = _proposal_message(result, tz)
    if result.get("status") == "proposed":
        await _update_status(
            update.message,
            status_message,
            saved + "\n\nI found a time that fits. Take a look below.",
        )
        await _reply(update.message, body, reply_markup=markup)
    else:
        await _update_status(
            update.message,
            status_message,
            saved + "\n\n" + body,
            reply_markup=task_keyboard(short),
        )


async def attachment_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    message = update.message
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
        filename = None
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        filename = message.document.file_name
    else:
        return
    caption = (message.caption or "").strip()

    reply = message.reply_to_message
    if reply is not None:
        target = await asyncio.to_thread(find_task_by_source_message, reply.message_id)
        if target is None:
            await _reply(
                update.message,
                "I couldn't tell which task this belongs to. Reply to the "
                "message that created the task, or send the file as a new task.",
            )
            return
        try:
            await asyncio.to_thread(
                save_attachment,
                task_id=target["id"],
                telegram_file_id=file_id,
                file_type=file_type,
                original_filename=filename,
            )
        except Exception:  # noqa: BLE001
            logger.exception("attachment save failed for task %s", target["id"])
            await _reply(
                update.message,
                "I couldn't save the attachment. Your task is unchanged. "
                "Please try sending it again.",
            )
            return
        count = await asyncio.to_thread(count_for_task, target["id"])
        await _reply(
            update.message,
            f"Added {count} attachment(s) to \"{target['title']}\".\n\n"
            f"Retrieve them anytime with /files {target['short_id']}.",
        )
        return

    if caption:
        title = caption.splitlines()[0][:100]
    else:
        tz = await asyncio.to_thread(_timezone, update.effective_user.id)
        title = f"Attachment {fmt_short_datetime(datetime.now(UTC), tz)}"
    task_id = await asyncio.to_thread(
        create_task,
        title=title,
        description=caption or None,
        category="other",
        estimated_duration_minutes=60,
        duration_source="estimate",
        telegram_message_id=message.message_id,
    )
    try:
        await asyncio.to_thread(
            save_attachment,
            task_id=task_id,
            telegram_file_id=file_id,
            file_type=file_type,
            original_filename=filename,
        )
    except Exception:  # noqa: BLE001
        logger.exception("attachment save failed for new task %s", task_id)
        await _reply(
            update.message,
            f"Saved as a new task: {title}\n\n"
            f"Task ID: {short_id(task_id)}\n\n"
            "I couldn't save the attachment, though. Please try sending it again.",
        )
        return
    await _reply(
        update.message,
        f"Saved as a new task: {title}\n\n"
        f"Task ID: {short_id(task_id)}\n\n"
        f"Retrieve the attachment anytime with /files {short_id(task_id)}.\n\n"
        "Tip: Reply to a task message with a photo or file to attach it to that task.",
    )


async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    if not context.args:
        await _reply(
            update.message,
            "Usage: /files <task_id>. You can find the task in /queue.",
        )
        return
    short = context.args[0].strip()
    task = get_task(short)
    if task is None:
        await _reply(update.message, "I couldn't find that task. Check /queue for its ID.")
        return
    task_id = resolve_task_id(short)
    files = list_for_task(task_id) if task_id else []
    if not files:
        await _reply(update.message, "There are no attachments saved for this task.")
        return
    await _reply(update.message, f"Attachments for \"{task[1]}\" ({len(files)}):")
    chat_id = update.effective_chat.id
    for item in files:
        try:
            if item["file_type"] == "photo":
                await context.bot.send_photo(
                    chat_id=chat_id, photo=item["telegram_file_id"]
                )
            else:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=item["telegram_file_id"],
                    filename=item.get("original_filename"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("resend attachment failed: %s", exc)
            await _reply(
                update.message,
                "I couldn't resend one attachment. The file may be too large to "
                "send through Telegram.",
            )


STATUS_LABELS = {
    "captured": "Waiting to be scheduled",
    "pending": "Proposal waiting for approval",
    "scheduled": "Scheduled",
    "in_progress": "In progress",
    "completed": "Completed",
    "cancelled": "Cancelled",
    "skipped": "Skipped",
}


def _task_details_text(details: dict, tz: str, session: dict | None = None) -> str:
    lines = [
        details["title"],
        "",
        f"Status: {STATUS_LABELS.get(details['status'], details['status'])}",
        f"Estimated time: {_duration_label(details['estimated_duration_minutes'] or 60)}",
    ]
    if session is not None:
        lines.append(
            "Scheduled session: "
            + fmt_range(
                session["planned_start_at"], session["planned_end_at"], tz
            )
        )
    if details.get("deadline_date"):
        lines.append(
            f"Deadline: {details['deadline_date'].isoformat()} (all-day)"
        )
    elif details.get("deadline_at"):
        lines.append(f"Deadline: {fmt_datetime(details['deadline_at'], tz)}")
    if details.get("reminder_only"):
        lines.append("Mode: reminder only (no work sessions)")
    if details.get("attachment_count"):
        lines.append(f"Attachments: {details['attachment_count']}")
    if details.get("subtask_count"):
        lines.append(f"Subtasks: {details['subtask_count']}")
    first_step = (details.get("first_step") or "").strip()
    if first_step:
        lines += ["", "Start here", "", first_step]
    description = (details.get("description") or "").strip()
    link = re.search(r"https?://\S+", description)
    if link:
        lines += ["", f"Source: {link.group(0)}"]
    elif description:
        lines += ["", "Saved message", "", description[:300]]
    lines += ["", f"Task ID: {details['short_id']}"]
    return "\n".join(lines)


async def _resend_files(context, chat_id: int, short: str) -> str:
    task = await asyncio.to_thread(get_task, short)
    if task is None:
        return "I couldn't find that task. Check /queue for its ID."
    task_id = await asyncio.to_thread(resolve_task_id, short)
    files = await asyncio.to_thread(list_for_task, task_id) if task_id else []
    if not files:
        return "There are no attachments saved for this task."
    failed = False
    for item in files:
        try:
            if item["file_type"] == "photo":
                await context.bot.send_photo(
                    chat_id=chat_id, photo=item["telegram_file_id"]
                )
            else:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=item["telegram_file_id"],
                    filename=item.get("original_filename"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("resend attachment failed: %s", exc)
            failed = True
    text = f"Attachments for \"{task[1]}\" ({len(files)}):"
    if failed:
        text += (
            "\n\nI couldn't resend one attachment. The file may be too large to "
            "send through Telegram."
        )
    return text


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    query = update.callback_query
    if query is None or not query.data:
        return
    action, _, arg = query.data.partition(":")
    user_id = update.effective_user.id
    toast = CALLBACK_TOASTS.get(action, "Got it.")
    if not _claim_request(user_id, query.data):
        try:
            await query.answer("I'm already working on this request.")
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        try:
            await query.answer(toast)
        except Exception as exc:  # noqa: BLE001
            logger.info("callback answer failed: %s", exc)
        await _dispatch_callback(update, context, query, action, arg, user_id)
    finally:
        _release_request(user_id, query.data)


async def _dispatch_callback(update, context, query, action, arg, user_id) -> None:
    tz = await asyncio.to_thread(_timezone, user_id)

    async def set_status(text: str) -> None:
        await _edit(query, text)

    if action == "deadline_ok":
        result = await asyncio.to_thread(
            deadlines.approve_deadline, arg, user_id
        )
        status = result.get("status")
        if status == "approved":
            await _edit(
                query,
                f"Deadline reminder added for {result['event_date'].isoformat()} "
                f"(all-day): {result['task_title']}.",
            )
        elif status == "already_approved":
            await _edit(query, "This deadline reminder is already approved.")
        elif status == "needs_auth":
            await _edit(query, "Connect Google Calendar with /auth first.")
        elif status == "event_missing":
            await _edit(
                query,
                "The calendar event was deleted. Reconnect or create the "
                "reminder again.",
            )
        elif status == "not_found":
            await _edit(query, "I couldn't find that deadline reminder.")
        else:
            await _edit(
                query,
                "I couldn't create the all-day reminder just now. Please try "
                "again shortly.",
            )
        return

    if action == "deadline_no":
        result = await asyncio.to_thread(deadlines.reject_deadline, arg)
        if result.get("status") == "change_rejected":
            await _edit(query, "Okay, the deadline change was cancelled.")
        elif result.get("status") == "rejected":
            await _edit(query, "Deadline reminder cancelled.")
        else:
            await _edit(query, "This deadline reminder is no longer pending.")
        return

    if action == "deadline_only":
        reminder = await asyncio.to_thread(deadlines.get_deadline, arg)
        if reminder is None:
            await _edit(query, "I couldn't find that deadline reminder.")
            return
        await asyncio.to_thread(
            deadlines.set_reminder_only, str(reminder["task_id"]), True
        )
        await _edit(
            query,
            "Reminder only. I won't schedule preparation. Approved work "
            "sessions, if any, were left unchanged.",
        )
        return

    if action == "deadline_edit":
        await _edit(
            query,
            f"Send /deadline {arg} YYYY-MM-DD (or a date like 25 October 2026). "
            "I'll ask for approval before changing the calendar reminder.",
        )
        return

    if action == "prep":
        task_id = await asyncio.to_thread(resolve_task_id, arg)
        if task_id is None:
            await _edit(query, "I couldn't find that task.")
            return
        await asyncio.to_thread(deadlines.set_reminder_only, task_id, False)
        result = await asyncio.to_thread(propose_for_task, task_id, user_id)
        body, markup = _proposal_message(result, tz)
        if result.get("status") == "proposed":
            await _edit(query, body, reply_markup=markup)
        else:
            await _edit(query, body)
        return

    if action == "approve":

        async def approve() -> dict:
            return await asyncio.to_thread(approve_proposal, arg, user_id)

        result = await _with_progress(
            set_status,
            "Request received.\n\nChecking your Google Calendar...",
            approve,
        )
        status = result["status"]
        if status == "approved":
            line = await _session_line(result, tz)
            await _edit(query, f"Added to Google Calendar:\n\n{line}")
            return
        if status == "already_approved":
            await _edit(query, "This proposal has already been approved.")
            return
        if status == "slot_taken":
            new = await asyncio.to_thread(propose_for_task, result["task_id"], user_id)
            body, markup = _proposal_message(new, tz)
            if new.get("status") == "proposed":
                await _edit(
                    query,
                    "That time is no longer available. Here's another option.\n\n"
                    + body,
                    reply_markup=markup,
                )
            else:
                await _edit(query, "That time is no longer available.\n\n" + body)
            return
        if status == "expired":
            await _edit(
                query, "This proposal has expired. Use /plan to get a new time."
            )
            return
        if status == "needs_auth":
            await _edit(
                query, "Google Calendar needs to be reconnected. Use /auth."
            )
            return
        if status == "event_failed":
            await _edit(
                query,
                "I couldn't create the Google Calendar event: "
                f"{result.get('error')}\n\n"
                "Your task is still saved. Try /plan again.",
            )
            return
        await _edit(
            query,
            "This proposal can no longer be approved. Request a new time with /plan.",
        )
        return

    if action == "reject":
        result = await asyncio.to_thread(reject_proposal, arg)
        if result["status"] == "rejected":
            await _edit(
                query,
                "Declined. You can request another time with /plan whenever you're ready.",
            )
        else:
            await _edit(query, "This proposal is no longer active.")
        return

    if action == "another":

        def find_another():
            rejected = reject_proposal(arg)
            proposal = rejected.get("proposal")
            if proposal is None:
                return rejected, None
            avoided = [(proposal["proposed_start_at"], proposal["proposed_end_at"])]
            new = propose_for_task(str(proposal["task_id"]), user_id, avoided)
            return rejected, new

        _, new = await _with_progress(
            set_status,
            "Request received.\n\nFinding another time...",
            lambda: asyncio.to_thread(find_another),
        )
        if new is None:
            await _edit(
                query,
                "I couldn't find another time for this proposal. Try /plan to "
                "request a fresh search.",
            )
            return
        body, markup = _proposal_message(new, tz)
        await _edit(query, body, reply_markup=markup)
        return

    if action in ("page", "nexttask", "dismiss"):
        if action == "dismiss":
            await _edit(query, "Okay.")
            return
        if action == "nexttask":
            result = await asyncio.to_thread(
                overwhelm.pick_next_action, user_id, arg
            )
            task = result.get("task")
            if not task:
                await _edit(
                    query,
                    "There are no other open tasks right now. Send me a task "
                    "whenever you're ready.",
                )
                return
            short = short_id(str(task["id"]))
            session = await asyncio.to_thread(
                sessions_service.session_for_task, str(task["id"])
            )
            await _edit(
                query,
                _overwhelm_text(task, result["first_step"]),
                reply_markup=overwhelm_keyboard(
                    short, str(session["id"]) if session else None
                ),
            )
            return
        try:
            page = int(arg)
        except ValueError:
            page = 0
        rows = await asyncio.to_thread(list_open_tasks)
        if not rows:
            await _edit(
                query, "Your queue is empty. Send me a task whenever you're ready."
            )
            return
        text, markup = _queue_view(rows, page)
        await _edit(query, text, reply_markup=markup)
        return

    if action == "view":
        details = await asyncio.to_thread(get_task_details, arg)
        if details is None:
            await _edit(
                query, "I couldn't find that task. Check /queue for its ID."
            )
            return
        session = await asyncio.to_thread(
            sessions_service.session_for_task, str(details["id"])
        ) if details.get("id") else None
        await _edit(
            query,
            _task_details_text(details, tz, session),
            reply_markup=task_details_keyboard(
                arg,
                str(session["id"]) if session else None,
                startable=bool(session and session["status"] == "planned"),
            ),
        )
        return

    if action == "today":
        text, markup = await _today_payload(user_id)
        await _edit(query, text, reply_markup=markup)
        return

    if action == "agenda":
        start, end = sessions_service.day_bounds(date.today(), tz)
        plan = await asyncio.to_thread(
            sessions_service.sessions_between, start, end, timezone=tz
        )
        if not plan:
            await _edit(
                query,
                "Nothing is scheduled for today.\n\nUse Back to today to see "
                "your next step.",
                reply_markup=agenda_keyboard(),
            )
            return
        lines = ["Today's agenda", ""]
        for session in plan:
            lines.append(
                f"{fmt_time(session['planned_start_at'], tz)}\u2013"
                f"{fmt_time(session['planned_end_at'], tz)} \u00b7 "
                f"{session['task_title']}"
            )
        await _edit(query, "\n".join(lines), reply_markup=agenda_keyboard())
        return

    if action == "picktask":
        try:
            page = int(arg)
        except ValueError:
            page = 0
        tasks = await asyncio.to_thread(overwhelm.open_tasks)
        if not tasks:
            await _edit(
                query,
                "There are no open tasks to choose from right now.",
                reply_markup=agenda_keyboard(),
            )
            return
        page_size = 3
        pages = max(1, -(-len(tasks) // page_size))
        page = max(0, min(page, pages - 1))
        chunk = tasks[page * page_size : (page + 1) * page_size]
        lines = ["Choose a task", ""]
        rows = []
        for index, task in enumerate(
            chunk, start=page * page_size + 1
        ):
            lines.append(f"{index}. {task['title']}")
            lines.append(
                f"Estimated time: "
                f"{_duration_label(task.get('estimated_duration_minutes') or 60)}"
            )
            lines.append("")
            rows.append(
                (
                    short_id(str(task["id"])),
                    task["title"],
                    task.get("estimated_duration_minutes") or 60,
                )
            )
        if pages > 1:
            lines.append(f"Page {page + 1} of {pages}")
        await _edit(
            query,
            "\n".join(lines).rstrip(),
            reply_markup=choose_task_keyboard(
                rows, page, page_size, total=len(tasks)
            ),
        )
        return

    if action == "selecttask":
        details = await asyncio.to_thread(get_task_details, arg)
        if details is None:
            await _edit(
                query,
                "I couldn't find that task. Check /queue for its ID.",
                reply_markup=agenda_keyboard(),
            )
            return
        if details["status"] not in ("captured", "pending", "scheduled", "in_progress"):
            await _edit(
                query,
                "This task is no longer open.",
                reply_markup=agenda_keyboard(),
            )
            return
        session = await asyncio.to_thread(
            sessions_service.session_for_task, str(details["id"])
        )
        first_step = (details.get("first_step") or "").strip() or (
            overwhelm.DEFAULT_FIRST_STEP
        )
        text = (
            "Your selected task\n\n"
            f"{details['title']}\n\n"
            "Start here\n\n"
            f"{first_step}"
        )
        await _edit(
            query,
            text,
            reply_markup=selection_keyboard(
                arg,
                str(session["id"]) if session else None,
                startable=bool(session and session["status"] == "planned"),
            ),
        )
        return

    if action == "schedule":
        task_id = await asyncio.to_thread(resolve_task_id, arg)
        if task_id is None:
            await _edit(
                query, "I couldn't find that task. Check /queue for its ID."
            )
            return
        session = await asyncio.to_thread(
            sessions_service.session_for_task, task_id
        )
        if session is not None:
            await _edit(
                query,
                "This task is already scheduled for "
                f"{fmt_datetime(session['planned_start_at'], tz)}.\n\n"
                "Use the buttons below to manage it.",
                reply_markup=session_keyboard(str(session["id"])),
            )
            return
        result = await _with_progress(
            set_status,
            "Request received.\n\nChecking your Google Calendar...",
            lambda: asyncio.to_thread(propose_for_task, task_id, user_id),
        )
        body, markup = _proposal_message(result, tz)
        if result.get("status") == "proposed":
            body = "Proposed session\n\n" + body
        await _edit(query, body, reply_markup=markup)
        return

    if action == "taskdone":
        result = await asyncio.to_thread(complete_task, arg)
        if result == "completed":
            task = await asyncio.to_thread(get_task, arg)
            title = task[1] if task else "Task"
            await _edit(
                query,
                f"Done: {title}. Nice work.",
                reply_markup=after_action_keyboard(),
            )
        elif result == "not_found":
            await _edit(
                query, "I couldn't find that task. Check /queue for its ID."
            )
        else:
            await _edit(
                query,
                "This task can't be marked as complete in its current state.",
            )
        return

    if action == "cancel":
        result = await asyncio.to_thread(cancel_task, arg)
        if result == "cancelled":
            task = await asyncio.to_thread(get_task, arg)
            title = task[1] if task else "Task"
            await _edit(
                query,
                f"Cancelled: {title}.",
                reply_markup=after_action_keyboard(),
            )
        elif result == "not_found":
            await _edit(
                query, "I couldn't find that task. Check /queue for its ID."
            )
        else:
            await _edit(
                query, "This task can't be cancelled in its current state."
            )
        return

    if action == "files":
        text = await _resend_files(context, query.message.chat_id, arg)
        await _edit(query, text)
        return

    if action in ("done", "addtime", "reschedule", "skip", "startnow"):
        status_texts = {
            "done": "Request received.\n\nMarking this session as complete...",
            "addtime": "Request received.\n\nExtending your session...",
            "reschedule": "Request received.\n\nFinding a new time...",
            "skip": "Request received.\n\nSkipping this session...",
            "startnow": "Request received.\n\nStarting your session...",
        }
        await _with_progress(
            set_status,
            status_texts[action],
            lambda: _handle_session_action(
                query, context, action, arg, user_id, tz
            ),
        )
        return


async def _session_line(result: dict, tz: str) -> str:
    start = result.get("start")
    end = result.get("end")
    if start is None or end is None:
        return result.get("task_title", "")
    return f"{result.get('task_title', '')}\n{fmt_range(start, end, tz)}"


async def _handle_session_action(query, context, action, arg, user_id, tz) -> None:
    if action == "done":
        result = await asyncio.to_thread(session_actions.complete_session, arg)
        if result["status"] in ("completed", "already_completed"):
            await _edit(
                query,
                f"Done: {result.get('task_title', 'session')}. Nice work.",
                reply_markup=after_action_keyboard(),
            )
        else:
            await _edit(
                query,
                "This session can't be marked as complete in its current state.",
            )
        return
    if action == "addtime":
        result = await asyncio.to_thread(session_actions.extend_active_session, user_id, 30)
        if result["status"] == "extended":
            session = result["session"]
            await _edit(
                query,
                "Added 30 minutes. Your session now ends at "
                f"{fmt_time(session['planned_end_at'], tz)}.{result.get('note', '')}",
            )
        elif result["status"] == "limit_reached":
            await _edit(
                query,
                "You've reached the extension limit for this session. You can "
                "schedule another session if you need more time.",
            )
        elif result["status"] == "needs_auth":
            await _edit(
                query, "Google Calendar needs to be reconnected. Use /auth."
            )
        elif result["status"] == "calendar_failed":
            await _edit(
                query,
                "I couldn't update the Google Calendar event. Your local session "
                "time was extended, but the calendar may still show the previous "
                "end time.\n\nPlease reconnect or retry before relying on the "
                "updated calendar time.",
            )
        else:
            await _edit(
                query,
                "You don't have an active session to extend. You can choose a "
                "task from /queue or check /today.",
            )
        return
    if action == "reschedule":
        result = await asyncio.to_thread(session_actions.reschedule_session, arg, user_id)
        body, markup = _proposal_message(result, tz)
        await _edit(query, "The previous session has been cancelled.")
        if markup is not None:
            await context.bot.send_message(
                chat_id=query.message.chat_id, text=body, reply_markup=markup
            )
        else:
            await _reply(query.message, body)
        return
    if action == "skip":
        result = await asyncio.to_thread(session_actions.skip_session, arg)
        await _edit(
            query,
            "Session skipped." if result["status"] == "skipped" else "This session is already closed.",
        )
        return
    if action == "startnow":
        result = await asyncio.to_thread(session_actions.start_session_now, arg)
        if result["status"] == "started":
            session = result["session"]
            await _edit(
                query,
                f"Let's get started: {session['task_title']}.\n\n"
                f"Your session ends at {fmt_time(session['planned_end_at'], tz)}.",
                reply_markup=session_keyboard(arg),
            )
        else:
            await _edit(query, "This session can't be started right now.")
        return


async def _edit(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as exc:  # noqa: BLE001
        logger.info("edit message failed: %s", exc)


async def command_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _authorized(update):
        return
    message = getattr(update, "effective_message", None) or update.message
    raw = (message.text or "").strip()
    if not raw.startswith("/"):
        return
    parts = raw.split()
    name = parts[0][1:].split("@")[0].lower()
    handler = COMMANDS.get(name)
    if handler is None:
        logger.info("unknown command %r from %s", name, update.effective_user.id)
        await _reply(
            message,
            "I don't recognize that command. Use /help to see what I can do.",
        )
        return
    logger.info("command %r from %s", name, update.effective_user.id)
    context.args = parts[1:]
    await handler(update, context)


COMMANDS = {
    "start": start_command,
    "help": help_command,
    "auth": auth_command,
    "today": today_command,
    "overwhelm": overwhelm_command,
    "plan": plan_command,
    "routines": routines_command,
    "routine": routine_command,
    "breakdown": breakdown_command,
    "files": files_command,
    "queue": queue_command,
    "done": done_command,
    "cancel": cancel_command,
    "skip": skip_command,
    "reschedule": reschedule_command,
    "delete": delete_command,
    "deadline": deadline_command,
    "duration": duration_command,
}


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.COMMAND, command_router))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, attachment_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, capture_message))
    return app


def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set Ã¢â‚¬â€ fill it in .env first")
    if not settings.allowed_telegram_user_ids:
        raise SystemExit(
            "TELEGRAM_ALLOWED_USER_IDS is empty Ã¢â‚¬â€ refusing to start (fail closed)"
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_application(settings.telegram_bot_token).run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()


