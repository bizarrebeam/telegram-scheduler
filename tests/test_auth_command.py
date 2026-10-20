import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.config import get_settings
from app.telegram_bot.bot import auth_command


def make_update(user_id: int = 111):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


def test_auth_always_returns_link(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.create_authorization_state",
        lambda uid: ("state", "https://accounts.google.com/o/oauth2/auth?x=1"),
    )
    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", lambda uid: True)
    update = make_update()
    get_settings.cache_clear()
    asyncio.run(auth_command(update, SimpleNamespace()))
    text = update.message.reply_text.call_args.args[0]
    assert "https://accounts.google.com/o/oauth2/auth?x=1" in text
    assert "refresh" in text


def test_auth_first_time_wording(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.create_authorization_state",
        lambda uid: ("state", "https://example.test/auth"),
    )
    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", lambda uid: False)
    update = make_update()
    get_settings.cache_clear()
    asyncio.run(auth_command(update, SimpleNamespace()))
    text = update.message.reply_text.call_args.args[0]
    assert "Connect your Google Calendar" in text
