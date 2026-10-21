import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from app.config import get_settings
from app.telegram_bot import bot
from app.telegram_bot.bot import (
    callback_handler,
    error_handler,
    plan_command,
    today_command,
)


def make_callback_update(data: str, user_id: int = 111):
    return SimpleNamespace(
        update_id=9,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=555),
        callback_query=SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=555),
        ),
    )


def make_message_update(text: str | None = None, user_id: int = 111):
    return SimpleNamespace(
        update_id=11,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=555),
        message=SimpleNamespace(
            text=text,
            message_id=3,
            reply_text=AsyncMock(),
        ),
    )


def make_context():
    return SimpleNamespace(
        bot=SimpleNamespace(
            send_message=AsyncMock(),
            send_photo=AsyncMock(),
            send_document=AsyncMock(),
        ),
        args=[],
        user_data={},
    )


def run(handler, update, context) -> None:
    get_settings.cache_clear()
    asyncio.run(handler(update, context))


def proposal_result():
    start = datetime.now(UTC) + timedelta(hours=1)
    return {
        "status": "proposed",
        "proposal": {
            "id": "prop-1",
            "proposed_start_at": start,
            "proposed_end_at": start + timedelta(minutes=60),
        },
        "task": {
            "title": "Apply MT",
            "estimated_duration_minutes": 60,
            "deadline_at": None,
        },
    }


def test_callback_is_answered_before_slow_work(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    order: list[tuple[str, str | None]] = []

    async def answer(text=None) -> None:
        order.append(("answer", text))

    def approve(proposal_id, user_id):
        order.append(("approve", proposal_id))
        start = datetime.now(UTC) + timedelta(hours=1)
        return {
            "status": "approved",
            "start": start,
            "end": start + timedelta(minutes=60),
            "task_title": "Apply MT",
        }

    update = make_callback_update("approve:prop-1")
    update.callback_query.answer = answer
    monkeypatch.setattr("app.telegram_bot.bot.approve_proposal", approve)
    run(callback_handler, update, make_context())
    assert order[0][0] == "answer"
    assert order[0][1] and "checking your calendar" in order[0][1].lower()
    assert order[1] == ("approve", "prop-1")


def test_duplicate_callback_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    approve = AsyncMock()
    monkeypatch.setattr("app.telegram_bot.bot.approve_proposal", approve)
    assert bot._claim_request(111, "approve:prop-1") is True
    try:
        update = make_callback_update("approve:prop-1")
        run(callback_handler, update, make_context())
        update.callback_query.answer.assert_awaited_once_with(
            "I'm already working on this request."
        )
        approve.assert_not_called()
    finally:
        bot._release_request(111, "approve:prop-1")


def test_declined_proposal_never_creates_calendar_event(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.reject_proposal",
        lambda pid: {"status": "rejected"},
    )
    calendar_create = AsyncMock()
    monkeypatch.setattr("app.services.proposals.create_event", calendar_create)
    update = make_callback_update("reject:prop-1")
    run(callback_handler, update, make_context())
    assert "Declined" in update.callback_query.edit_message_text.call_args.args[0]
    calendar_create.assert_not_called()


def test_unauthorized_callback_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_callback_update("approve:prop-1", user_id=999)
    run(callback_handler, update, make_context())
    update.callback_query.answer.assert_not_awaited()


def test_plan_shows_progress_then_full_proposal(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr("app.telegram_bot.bot.resolve_task_id", lambda short: "tid")
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", lambda uid: True)
    monkeypatch.setattr(
        "app.telegram_bot.bot.propose_for_task", lambda tid, uid: proposal_result()
    )
    update = make_message_update()
    context = make_context()
    context.args = ["abcd1234"]
    run(plan_command, update, context)
    first = update.message.reply_text.call_args_list[0].args[0]
    assert "Finding a time for your task" in first
    status = update.message.reply_text.return_value
    final_text = status.edit_text.call_args_list[-1].args[0]
    final_markup = status.edit_text.call_args_list[-1].kwargs["reply_markup"]
    assert "Proposed session" in final_text
    assert "60 minutes" in final_text
    assert final_markup is not None


def test_plan_failure_reports_error(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr("app.telegram_bot.bot.resolve_task_id", lambda short: "tid")
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", lambda uid: True)

    def boom(tid, uid):
        raise RuntimeError("calendar down")

    monkeypatch.setattr("app.telegram_bot.bot.propose_for_task", boom)
    update = make_message_update()
    context = make_context()
    context.args = ["abcd1234"]
    run(plan_command, update, context)
    status = update.message.reply_text.return_value
    assert "couldn't check your calendar" in status.edit_text.call_args.args[0]


def test_view_callback_shows_task_details_and_buttons(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task_details",
        lambda short: {
            "short_id": short,
            "title": "Apply MT",
            "description": "daftar FE intern https://example.test/job",
            "category": "work",
            "deadline_at": None,
            "estimated_duration_minutes": 60,
            "first_step": "Open the job posting.",
            "status": "captured",
            "attachment_count": 0,
            "subtask_count": 0,
        },
    )
    update = make_callback_update("view:abcd1234")
    run(callback_handler, update, make_context())
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Apply MT" in text
    assert "Status: Waiting to be scheduled" in text
    assert "Task ID: abcd1234" in text
    markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "schedule:abcd1234" in datas
    assert "taskdone:abcd1234" in datas


def test_schedule_callback_returns_proposal_after_progress(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr("app.telegram_bot.bot.resolve_task_id", lambda short: "tid")
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.propose_for_task", lambda tid, uid: proposal_result()
    )
    update = make_callback_update("schedule:abcd1234")
    run(callback_handler, update, make_context())
    calls = update.callback_query.edit_message_text.call_args_list
    assert "Checking your Google Calendar" in calls[0].args[0]
    assert "Proposed session" in calls[-1].args[0]
    assert calls[-1].kwargs["reply_markup"] is not None


def test_today_shows_current_session_once(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "app.telegram_bot.bot.overwhelm.pick_next_action",
        lambda uid: {
            "status": "ok",
            "task": {
                "id": "abcdef1234567890",
                "title": "Apply MT",
            },
            "first_step": "Open the job posting.",
            "alternatives": [
                {"title": "Prepare CV"},
                {"title": "IELTS practice"},
                {"title": "Clean inbox"},
            ],
        },
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.day_bounds",
        lambda day, tz: (now - timedelta(hours=1), now + timedelta(hours=6)),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.sessions_between",
        lambda start, end, timezone: [
            {
                "id": "sess-1",
                "task_title": "Apply MT",
                "planned_start_at": now - timedelta(minutes=10),
                "planned_end_at": now + timedelta(minutes=50),
            }
        ],
    )
    update = make_message_update()
    run(today_command, update, make_context())
    text = update.message.reply_text.call_args.args[0]
    assert "Your next step" not in text
    assert "Current session" in text
    assert "Up next" not in text
    assert "Later today" not in text
    assert text.count("Apply MT") == 1
    assert "Prepare CV" not in text


def test_reply_disables_link_previews() -> None:
    message = SimpleNamespace(reply_text=AsyncMock())
    asyncio.run(bot._reply(message, "https://example.test/very/long/url"))
    options = message.reply_text.call_args.kwargs["link_preview_options"]
    assert options.is_disabled is True


def test_approve_failure_does_not_claim_event_created(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.approve_proposal",
        lambda proposal_id, user_id: {
            "status": "event_failed",
            "error": "HTTP 500",
        },
    )
    update = make_callback_update("approve:prop-1")
    run(callback_handler, update, make_context())
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "couldn't create the Google Calendar event" in text
    assert "Added to Google Calendar" not in text


def test_task_done_button_updates_without_task_id(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.complete_task", lambda short: "completed"
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task",
        lambda short: (short, "Apply MT", "captured"),
    )
    update = make_callback_update("taskdone:abcd1234")
    run(callback_handler, update, make_context())
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert text == "Done: Apply MT. Nice work."


def test_queue_page_two_shows_navigation(monkeypatch) -> None:
    rows = [(f"task{i:04d}", f"Task {i}", 30) for i in range(7)]
    text, markup = bot._queue_view(rows, 1)
    assert "6. Task 5" in text
    assert "Page 2 of 2" in text
    datas = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "page:0" in datas
    assert "schedule:task0005" in datas

def test_callback_timeout_reports_uncertain_failure(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")

    def boom(proposal_id, user_id):
        raise httpx.ReadTimeout("calendar timed out")

    monkeypatch.setattr("app.telegram_bot.bot.approve_proposal", boom)
    update = make_callback_update("approve:prop-1")
    run(callback_handler, update, make_context())
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "didn't respond in time" in text
    assert "couldn't confirm" in text
    assert "Added to Google Calendar" not in text
    assert "calendar timed out" not in text


def test_callback_http_error_is_mapped_without_raw_exception(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    request = httpx.Request("POST", "https://example.test/calendar")
    response = httpx.Response(500, request=request)

    def boom(proposal_id, user_id):
        raise httpx.HTTPStatusError(
            "server error", request=request, response=response
        )

    monkeypatch.setattr("app.telegram_bot.bot.approve_proposal", boom)
    update = make_callback_update("approve:prop-1")
    run(callback_handler, update, make_context())
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Google Calendar returned an error" in text
    assert "couldn't confirm" in text
    assert "server error" not in text


def test_callback_uncertain_event_does_not_claim_success(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.approve_proposal",
        lambda proposal_id, user_id: {
            "status": "event_uncertain",
            "error_code": "timeout",
        },
    )
    update = make_callback_update("approve:prop-1")
    run(callback_handler, update, make_context())
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "couldn't confirm whether the event was created" in text
    assert "Retrying is safe" in text
    assert "Added to Google Calendar" not in text


def test_reschedule_failure_does_not_claim_previous_session_cancelled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.session_actions.reschedule_session",
        lambda session_id, user_id: {
            "status": "calendar_failed",
            "uncertain": True,
        },
    )
    update = make_callback_update("reschedule:sess-1")
    run(callback_handler, update, make_context())
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "existing session is still scheduled" in text
    assert "previous session has been cancelled" not in text.lower()


def test_global_error_handler_sends_failure_message() -> None:
    update = make_message_update()
    update.effective_message = update.message
    context = SimpleNamespace(error=httpx.ReadTimeout("calendar timed out"))
    asyncio.run(error_handler(update, context))
    text = update.message.reply_text.call_args.args[0]
    assert "didn't respond in time" in text
    assert "calendar timed out" not in text


def test_callback_ack_is_logged_with_timing(monkeypatch, caplog) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.approve_proposal",
        lambda proposal_id, user_id: {
            "status": "approved",
            "start": datetime.now(UTC),
            "end": datetime.now(UTC) + timedelta(minutes=60),
            "task_title": "Apply MT",
        },
    )
    update = make_callback_update("approve:prop-1")
    with caplog.at_level("INFO", logger="app"):
        run(callback_handler, update, make_context())
    ack = [r for r in caplog.records if getattr(r, "event", "") == "callback_ack"]
    assert ack
    assert ack[0].action == "approve"
    assert ack[0].duration_ms >= 0
