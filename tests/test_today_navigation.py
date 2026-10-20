import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.config import get_settings
from app.telegram_bot import bot
from app.telegram_bot.bot import callback_handler, today_command


def make_callback(data: str):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        message=SimpleNamespace(chat_id=555),
    )
    return SimpleNamespace(
        update_id=31,
        callback_query=query,
        effective_user=SimpleNamespace(id=111),
        effective_chat=SimpleNamespace(id=555),
    )


def make_context():
    return SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock()),
        user_data={},
    )


def run(handler, update, context=None):
    get_settings.cache_clear()
    asyncio.run(handler(update, context or make_context()))


def details(short="abcd1234", status="captured", first_step="Open the job posting."):
    return {
        "id": f"{short}-full",
        "short_id": short,
        "title": "Apply MT",
        "description": "https://example.test/job",
        "category": "work",
        "deadline_at": None,
        "estimated_duration_minutes": 120,
        "first_step": first_step,
        "status": status,
        "attachment_count": 0,
        "subtask_count": 0,
    }


def planned_session():
    start = datetime.now(UTC) + timedelta(hours=1)
    return {
        "id": "sess-1",
        "status": "planned",
        "task_title": "Apply MT",
        "planned_start_at": start,
        "planned_end_at": start + timedelta(minutes=120),
    }


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def patch_base(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.overwhelm.open_tasks",
        lambda: [
            {
                "id": f"{i}234567890abcdef",
                "title": f"Task {i}",
                "estimated_duration_minutes": 30,
            }
            for i in range(1, 6)
        ],
    )


def test_choose_another_lists_tasks_with_back_and_pagination(monkeypatch) -> None:
    patch_base(monkeypatch)
    update = make_callback("picktask:0")
    run(callback_handler, update)
    args = update.callback_query.edit_message_text.call_args
    text = args.args[0]
    assert "Choose a task" in text
    assert "1. Task 1" in text
    assert "Estimated time: 30 min" in text
    assert "Task 4" not in text
    assert "Page 1 of 2" in text
    codes = datas(args.kwargs["reply_markup"])
    assert "selecttask:12345678" in codes
    assert "picktask:1" in codes
    assert "today" in codes


def test_choose_page_two_shows_previous(monkeypatch) -> None:
    patch_base(monkeypatch)
    update = make_callback("picktask:1")
    run(callback_handler, update)
    codes = datas(update.callback_query.edit_message_text.call_args.kwargs["reply_markup"])
    assert "picktask:0" in codes
    assert "selecttask:52345678" in codes


def test_selecting_task_updates_recommendation_without_touching_sessions(
    monkeypatch,
) -> None:
    patch_base(monkeypatch)
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task_details", lambda short: details(short)
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    reschedule = AsyncMock()
    complete = AsyncMock()
    monkeypatch.setattr(bot.session_actions, "reschedule_session", reschedule)
    monkeypatch.setattr(bot, "complete_task", complete)
    update = make_callback("selecttask:abcd1234")
    run(callback_handler, update)
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Your selected task" in text
    assert "Apply MT" in text
    assert "Open the job posting." in text
    codes = datas(
        update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    )
    assert "schedule:abcd1234" in codes
    assert "picktask:0" in codes
    assert "today" in codes
    assert "startnow:" not in " ".join(codes)
    reschedule.assert_not_called()
    complete.assert_not_called()


def test_back_to_today_returns_agenda(monkeypatch) -> None:
    patch_base(monkeypatch)

    async def payload(uid):
        return "Your next step", None

    monkeypatch.setattr(bot, "_today_payload", payload)
    update = make_callback("today")
    run(callback_handler, update)
    assert update.callback_query.edit_message_text.call_args.args[0] == "Your next step"


def test_view_details_has_back_and_choose_other(monkeypatch) -> None:
    patch_base(monkeypatch)
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task_details", lambda short: details(short)
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    update = make_callback("view:abcd1234")
    run(callback_handler, update)
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Source: https://example.test/job" in text
    codes = datas(
        update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    )
    assert "schedule:abcd1234" in codes
    assert "today" in codes
    assert "picktask:0" in codes


def test_scheduled_task_shows_session_and_reschedule_not_schedule(
    monkeypatch,
) -> None:
    patch_base(monkeypatch)
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task_details",
        lambda short: details(short, status="scheduled"),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task",
        lambda tid: planned_session(),
    )
    update = make_callback("view:abcd1234")
    run(callback_handler, update)
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Scheduled session:" in text
    codes = datas(
        update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    )
    assert "startnow:sess-1" in codes
    assert "reschedule:sess-1" in codes
    assert "schedule:abcd1234" not in codes
    assert "today" in codes


def test_today_recommendation_has_start_and_switch_buttons(monkeypatch) -> None:
    patch_base(monkeypatch)
    monkeypatch.setattr(
        "app.telegram_bot.bot.overwhelm.pick_next_action",
        lambda uid: {
            "status": "ok",
            "task": {
                "id": "abcd1234-full",
                "title": "Apply MT",
                "estimated_duration_minutes": 120,
            },
            "first_step": "Open the job posting.",
        },
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.day_bounds",
        lambda day, tz: (datetime.now(UTC), datetime.now(UTC)),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.sessions_between",
        lambda start, end, timezone: [],
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task",
        lambda tid: planned_session(),
    )
    update = make_callback("today")
    run(callback_handler, update)
    text = update.callback_query.edit_message_text.call_args.args[0]
    assert "Estimated time: 2 hours" in text
    codes = datas(
        update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    )
    assert "startnow:sess-1" in codes
    assert "reschedule:sess-1" in codes
    assert "picktask:0" in codes
    assert "view:abcd1234" in codes


def test_today_command_does_not_auto_open_details(monkeypatch) -> None:
    patch_base(monkeypatch)
    monkeypatch.setattr(
        "app.telegram_bot.bot.overwhelm.pick_next_action",
        lambda uid: {
            "status": "ok",
            "task": {"id": "abcd1234-full", "title": "Apply MT"},
            "first_step": "Open the job posting.",
        },
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.day_bounds",
        lambda day, tz: (datetime.now(UTC), datetime.now(UTC)),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.sessions_between",
        lambda start, end, timezone: [],
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    update = SimpleNamespace(
        update_id=41,
        effective_user=SimpleNamespace(id=111),
        effective_chat=SimpleNamespace(id=555),
        message=SimpleNamespace(text="/today", reply_text=AsyncMock()),
    )
    context = make_context()
    run(today_command, update, context)
    assert update.message.reply_text.await_count == 1
    context.bot.send_message.assert_not_called()
    assert "Apply MT" in update.message.reply_text.call_args.args[0]


def test_stale_task_button_cannot_act_on_another_task(monkeypatch) -> None:
    patch_base(monkeypatch)
    monkeypatch.setattr("app.telegram_bot.bot.get_task_details", lambda short: None)
    complete = AsyncMock()
    cancel = AsyncMock()
    monkeypatch.setattr(bot, "complete_task", complete)
    monkeypatch.setattr(bot, "cancel_task", cancel)
    update = make_callback("view:deadbeef")
    run(callback_handler, update)
    assert "couldn't find that task" in (
        update.callback_query.edit_message_text.call_args.args[0]
    )
    complete.assert_not_called()
    cancel.assert_not_called()


def test_done_keeps_navigation_and_does_not_open_another_task(monkeypatch) -> None:
    patch_base(monkeypatch)
    monkeypatch.setattr(bot, "complete_task", lambda short: "completed")
    monkeypatch.setattr(
        bot, "get_task", lambda short: (short, "Apply MT", "captured")
    )
    update = make_callback("taskdone:abcd1234")
    run(callback_handler, update)
    args = update.callback_query.edit_message_text.call_args
    assert args.args[0] == "Done: Apply MT. Nice work."
    codes = datas(args.kwargs["reply_markup"])
    assert "today" in codes
    assert "picktask:0" in codes
