import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock


from app.config import get_settings
from app.telegram_bot import bot
from app.telegram_bot.bot import (
    COMMANDS,
    command_router,
    files_command,
    plan_command,
    today_command,
)


def make_update(text: str | None = None, user_id: int = 111):
    return SimpleNamespace(
        update_id=21,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=555),
        message=SimpleNamespace(
            text=text,
            message_id=7,
            caption=None,
            photo=None,
            document=None,
            reply_to_message=None,
            reply_text=AsyncMock(),
        ),
    )


def make_context(args=None):
    return SimpleNamespace(
        args=args or [],
        user_data={},
        bot=SimpleNamespace(
            send_message=AsyncMock(),
            send_photo=AsyncMock(),
            send_document=AsyncMock(),
        ),
    )


def run(handler, update, context=None):
    get_settings.cache_clear()
    asyncio.run(handler(update, context or make_context()))


def test_overwhelm_routes_to_handler_even_without_attachments(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    calls = []

    async def spy(update, context):
        calls.append(update.message.text)

    monkeypatch.setitem(COMMANDS, "overwhelm", spy)
    update = make_update("/overwhelm")
    run(command_router, update)
    assert calls == ["/overwhelm"]


def test_unknown_command_is_not_saved_as_task(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update("/notacommand")
    capture = AsyncMock()
    monkeypatch.setattr(bot, "capture_message", capture)
    run(command_router, update)
    capture.assert_not_called()
    assert "don't recognize that command" in update.message.reply_text.call_args.args[0]
    assert "/help" in update.message.reply_text.call_args.args[0]


def test_routine_and_routines_route_separately(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    seen = []

    async def routines_spy(update, context):
        seen.append("routines")

    async def routine_spy(update, context):
        seen.append("routine")

    monkeypatch.setitem(COMMANDS, "routines", routines_spy)
    monkeypatch.setitem(COMMANDS, "routine", routine_spy)
    run(command_router, make_update("/routines"))
    run(command_router, make_update("/routine Exercise | 45 | 3"))
    assert seen == ["routines", "routine"]


def test_overwhelm_works_without_session_or_calendar(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.overwhelm.pick_next_action",
        lambda uid: {
            "status": "ok",
            "task": {"id": "abcdef1234567890", "title": "Apply MT"},
            "first_step": "Open the job posting.",
            "alternatives": [],
        },
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )

    def no_credentials(uid):
        raise AssertionError("overwhelm must not check Google credentials")

    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", no_credentials)
    update = make_update("/overwhelm")
    run(bot.overwhelm_command, update)
    text = update.message.reply_text.call_args.args[0]
    assert "Let's start small." in text
    assert "Apply MT" in text
    assert "Open the job posting." in text
    assert "attachment" not in text.lower()
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "schedule:abcdef12" in datas
    assert "nexttask:abcdef12" in datas
    assert not any(d and d.startswith("startnow:") for d in datas)


def test_overwhelm_without_tasks_invites_a_task(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.overwhelm.pick_next_action",
        lambda uid: {"status": "empty"},
    )
    update = make_update("/overwhelm")
    run(bot.overwhelm_command, update)
    assert "don't have any open tasks yet" in update.message.reply_text.call_args.args[0]


def test_today_without_events_still_recommends_a_task(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.overwhelm.pick_next_action",
        lambda uid: {
            "status": "ok",
            "task": {"id": "abcdef1234567890", "title": "Apply MT"},
            "first_step": "Open the job posting.",
            "alternatives": [],
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
    update = make_update("/today")
    run(today_command, update)
    text = update.message.reply_text.call_args.args[0]
    assert "Nothing is scheduled for today yet." in text
    assert "Start here" in text
    assert "Apply MT" in text
    assert "Estimated time: 1 hour" in text
    assert "Open the job posting." in text
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "schedule:abcdef12" in datas
    assert "picktask:0" in datas


def test_today_without_events_or_tasks_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.overwhelm.pick_next_action",
        lambda uid: {"status": "empty"},
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.day_bounds",
        lambda day, tz: (datetime.now(UTC), datetime.now(UTC)),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.sessions_between",
        lambda start, end, timezone: [],
    )
    update = make_update("/today")
    run(today_command, update)
    assert update.message.reply_text.call_args.args[0] == (
        "Nothing is scheduled for today, and your task queue is empty. "
        "Send me a task whenever you're ready."
    )


def test_plan_without_credentials_does_not_start_calendar_search(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot.resolve_task_id", lambda short: "tid")
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", lambda uid: False)
    propose = AsyncMock()
    monkeypatch.setattr(bot, "propose_for_task", propose)
    update = make_update("/plan abcd1234")
    run(plan_command, update, make_context(["abcd1234"]))
    assert update.message.reply_text.call_args.args[0] == (
        "Your task is saved, but I can't check your calendar yet. Connect "
        "Google Calendar with /auth, then try scheduling again."
    )
    propose.assert_not_called()


def test_plan_does_not_duplicate_an_existing_session(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr("app.telegram_bot.bot.resolve_task_id", lambda short: "tid")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task",
        lambda short: ("abcd1234", "Apply MT", "scheduled"),
    )
    start = datetime(2026, 10, 21, 13, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task",
        lambda tid: {"id": "sess-1", "planned_start_at": start},
    )
    propose = AsyncMock()
    monkeypatch.setattr(bot, "propose_for_task", propose)
    update = make_update("/plan abcd1234")
    run(plan_command, update, make_context(["abcd1234"]))
    assert "already scheduled" in update.message.reply_text.call_args.args[0]
    propose.assert_not_called()


def test_files_empty_does_not_change_task_state(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task",
        lambda short: ("abcd1234", "Apply MT", "pending"),
    )
    monkeypatch.setattr("app.telegram_bot.bot.resolve_task_id", lambda short: "full-id")
    monkeypatch.setattr("app.telegram_bot.bot.list_for_task", lambda tid: [])
    complete = AsyncMock()
    cancel = AsyncMock()
    delete = AsyncMock()
    monkeypatch.setattr(bot, "complete_task", complete)
    monkeypatch.setattr(bot, "cancel_task", cancel)
    monkeypatch.setattr(bot, "delete_task", delete)
    update = make_update("/files abcd1234")
    run(files_command, update, make_context(["abcd1234"]))
    assert update.message.reply_text.call_args.args[0] == (
        "There are no attachments saved for this task."
    )
    complete.assert_not_called()
    cancel.assert_not_called()
    delete.assert_not_called()


def test_extension_without_active_session_explains_options(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.session_actions.extend_active_session",
        lambda uid, minutes: {"status": "no_active_session"},
    )
    update = make_update("+30 minutes")
    run(bot.capture_message, update)
    assert update.message.reply_text.call_args.args[0] == (
        "You don't have an active session to extend. You can choose a task "
        "from /queue or check /today."
    )


def test_attachment_reply_without_task_identity_asks_for_clarification(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.find_task_by_source_message", lambda mid: None
    )
    create = AsyncMock()
    monkeypatch.setattr(bot, "create_task", create)
    update = make_update()
    update.message.photo = [SimpleNamespace(file_id="photo-1")]
    update.message.reply_to_message = SimpleNamespace(message_id=99)
    run(bot.attachment_message, update)
    assert "couldn't tell which task this belongs to" in (
        update.message.reply_text.call_args.args[0]
    )
    create.assert_not_called()


def test_command_map_covers_every_documented_command() -> None:
    expected = {
        "start",
        "help",
        "queue",
        "done",
        "cancel",
        "skip",
        "reschedule",
        "delete",
        "auth",
        "today",
        "overwhelm",
        "plan",
        "routines",
        "routine",
        "breakdown",
        "files",
        "deadline",
        "duration",
    }
    assert set(COMMANDS) == expected
    for name, handler in COMMANDS.items():
        assert callable(handler), name

