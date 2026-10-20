import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.config import get_settings
from app.telegram_bot.bot import (
    COMMANDS,
    HELP_TEXT,
    START_TEXT,
    build_application,
    help_command,
    start_command,
)


def make_update(user_id: int | None = 111):
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(
        effective_user=user,
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


def run(handler, update) -> None:
    get_settings.cache_clear()
    asyncio.run(handler(update, SimpleNamespace()))


def test_start_replies_for_authorized_user(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update(111)
    run(start_command, update)
    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.call_args.args[0] == START_TEXT


def test_start_ignores_unauthorized_user(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update(999)
    run(start_command, update)
    update.message.reply_text.assert_not_awaited()


def test_help_replies_for_authorized_user(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update(111)
    run(help_command, update)
    update.message.reply_text.assert_awaited_once()
    assert update.message.reply_text.call_args.args[0] == HELP_TEXT


def test_build_application_registers_commands() -> None:
    app = build_application("123:TEST")
    handlers = [h for group in app.handlers.values() for h in group]
    assert len(handlers) == 4
    router = handlers[0]
    assert router.callback.__name__ == "command_router"
    assert set(COMMANDS) == {
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
    assert COMMANDS["routine"] is not COMMANDS["routines"]
    assert COMMANDS["overwhelm"] is not COMMANDS["files"]

