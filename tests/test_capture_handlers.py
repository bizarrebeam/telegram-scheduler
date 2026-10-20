import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import get_settings
from app.telegram_bot.bot import (
    cancel_command,
    capture_message,
    delete_command,
    done_command,
    queue_command,
    reschedule_command,
    skip_command,
)


def make_update(user_id: int | None = 111, text: str | None = None):
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(
        update_id=42,
        effective_user=user,
        message=SimpleNamespace(
            text=text,
            message_id=7,
            reply_text=AsyncMock(),
        ),
    )


def run(handler, update, args: list[str] | None = None) -> None:
    get_settings.cache_clear()
    asyncio.run(handler(update, SimpleNamespace(args=args or [])))


def test_capture_stores_task(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", lambda uid: False)
    update = make_update(text="Apply MT, butuh 1 jam")
    with patch("app.telegram_bot.bot.create_task_from_message", return_value=("abcd1234", "Apply MT, butuh 1 jam")) as mock_create:
        run(capture_message, update)
    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs["estimated_duration_minutes"] == 60
    update.message.reply_text.assert_awaited()
    first_reply = update.message.reply_text.call_args_list[0].args[0]
    assert first_reply == "Saving this task\u2026"
    status = update.message.reply_text.return_value
    saved_text = status.edit_text.call_args_list[-1].args[0]
    assert "abcd1234" in saved_text
    assert "Connect Google Calendar" in saved_text


def test_capture_duplicate_update_reports_nothing_new(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", lambda uid: False)
    update = make_update(text="Apply MT")
    with patch("app.telegram_bot.bot.create_task_from_message", return_value=None):
        run(capture_message, update)
    status = update.message.reply_text.return_value
    assert "already saved" in status.edit_text.call_args.args[0]


def test_queue_empty(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update()
    with patch("app.telegram_bot.bot.list_open_tasks", return_value=[]):
        run(queue_command, update)
    assert "Your queue is empty" in update.message.reply_text.call_args.args[0]


def test_queue_lists_tasks(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update()
    with patch(
        "app.telegram_bot.bot.list_open_tasks",
        return_value=[("abcd1234", "Apply MT", 60)],
    ):
        run(queue_command, update)
    text = update.message.reply_text.call_args.args[0]
    assert "Apply MT" in text and "Estimated time: 60 min" in text
    keyboard = update.message.reply_text.call_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "schedule:abcd1234" in datas
    assert "view:abcd1234" in datas


def test_done_not_found(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update()
    with patch("app.telegram_bot.bot.complete_task", return_value="not_found"):
        run(done_command, update, args=["ffff9999"])
    assert "couldn't find that task" in update.message.reply_text.call_args.args[0]


def test_done_completed(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task", lambda short: ("abcd1234", "Apply MT", "captured")
    )
    update = make_update()
    with patch("app.telegram_bot.bot.complete_task", return_value="completed"):
        run(done_command, update, args=["abcd1234"])
    assert "Done: Apply MT" in update.message.reply_text.call_args.args[0]


def test_cancel_not_found(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update()
    with patch("app.telegram_bot.bot.cancel_task", return_value="not_found"):
        run(cancel_command, update, args=["ffff9999"])
    assert "couldn't find that task" in update.message.reply_text.call_args.args[0]


def test_skip_skipped(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task",
        lambda short: ("abcd1234", "Apply MT", "scheduled"),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.resolve_task_id", lambda short: "full-id"
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task",
        lambda tid: {"id": "sess-1"},
    )
    update = make_update()
    with patch("app.telegram_bot.bot.skip_task", return_value="skipped"):
        run(skip_command, update, args=["abcd1234"])
    assert "Skipped this session" in update.message.reply_text.call_args.args[0]


def test_skip_without_session_keeps_task(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task",
        lambda short: ("abcd1234", "Apply MT", "captured"),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.resolve_task_id", lambda short: "full-id"
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    update = make_update()
    with patch("app.telegram_bot.bot.skip_task") as mock_skip:
        run(skip_command, update, args=["abcd1234"])
    mock_skip.assert_not_called()
    assert "There isn't an active session to skip" in update.message.reply_text.call_args.args[0]


def test_reschedule_without_session_offers_new_time(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task",
        lambda short: ("abcd1234", "Apply MT", "captured"),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.resolve_task_id", lambda short: "full-id"
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.sessions_service.session_for_task", lambda tid: None
    )
    update = make_update()
    run(reschedule_command, update, args=["abcd1234"])
    text = update.message.reply_text.call_args.args[0]
    assert "doesn't have an active scheduled session" in text
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "schedule:abcd1234" in datas
    assert "dismiss" in datas


def test_delete_requires_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    update = make_update()
    context = SimpleNamespace(args=["abcd1234"], user_data={})
    get_settings.cache_clear()
    with patch("app.telegram_bot.bot.get_task", return_value=("abcd1234", "Judul", "captured")):
        with patch("app.telegram_bot.bot.delete_task", return_value="deleted") as mock_delete:
            asyncio.run(delete_command(update, context))
            mock_delete.assert_not_called()
            asyncio.run(delete_command(update, context))
            mock_delete.assert_called_once_with("abcd1234")
    assert "Deleted" in update.message.reply_text.call_args.args[0]
