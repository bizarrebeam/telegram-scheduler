import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.config import get_settings
from app.telegram_bot.bot import attachment_message, files_command


def make_message(*, photo=None, document=None, caption=None, reply_to=None):
    return SimpleNamespace(
        photo=[SimpleNamespace(file_id=photo)] if photo else None,
        document=(
            SimpleNamespace(file_id=document, file_name="tugas.pdf")
            if document
            else None
        ),
        caption=caption,
        message_id=99,
        reply_to_message=reply_to,
        reply_text=AsyncMock(),
    )


def make_update(message, user_id: int = 111):
    return SimpleNamespace(
        update_id=7,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=555),
        message=message,
    )


def make_context(bot=None):
    return SimpleNamespace(
        args=[],
        bot=bot
        or SimpleNamespace(send_photo=AsyncMock(), send_document=AsyncMock()),
    )


def test_reply_attaches_to_existing_task(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.find_task_by_source_message",
        lambda mid: {"id": "full-id", "short_id": "abcd1234", "title": "Apply MT"},
    )
    saved = {}
    monkeypatch.setattr(
        "app.telegram_bot.bot.save_attachment",
        lambda **kw: saved.update(kw) or {},
    )
    monkeypatch.setattr("app.telegram_bot.bot.count_for_task", lambda tid: 2)
    created = {"called": False}
    monkeypatch.setattr(
        "app.telegram_bot.bot.create_task",
        lambda **kw: created.update(called=True) or "x",
    )
    update = make_update(
        make_message(photo="file-1", reply_to=SimpleNamespace(message_id=42))
    )
    get_settings.cache_clear()
    asyncio.run(attachment_message(update, make_context()))
    assert saved["task_id"] == "full-id"
    assert created["called"] is False
    text = update.message.reply_text.call_args.args[0]
    assert "Added 2 attachment(s)" in text and "abcd1234" in text


def test_no_reply_creates_new_task(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot.find_task_by_source_message", lambda m: None)
    created = {}
    monkeypatch.setattr(
        "app.telegram_bot.bot.create_task",
        lambda **kw: created.update(kw) or "new-task-id",
    )
    monkeypatch.setattr("app.telegram_bot.bot.save_attachment", lambda **kw: {})
    update = make_update(make_message(document="file-2", caption="Bukti transfer"))
    get_settings.cache_clear()
    asyncio.run(attachment_message(update, make_context()))
    assert created["title"] == "Bukti transfer"
    assert created["telegram_message_id"] == 99
    assert "/files" in update.message.reply_text.call_args.args[0]


def test_files_resends_photo_and_document(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task", lambda short: ("abcd1234", "Apply MT", "pending")
    )
    monkeypatch.setattr("app.telegram_bot.bot.resolve_task_id", lambda short: "full-id")
    monkeypatch.setattr(
        "app.telegram_bot.bot.list_for_task",
        lambda tid: [
            {"file_type": "photo", "telegram_file_id": "p1", "original_filename": None},
            {
                "file_type": "document",
                "telegram_file_id": "d1",
                "original_filename": "cv.pdf",
            },
        ],
    )
    update = make_update(make_message())
    context = make_context()
    context.args = ["abcd1234"]
    get_settings.cache_clear()
    asyncio.run(files_command(update, context))
    context.bot.send_photo.assert_awaited_once()
    context.bot.send_document.assert_awaited_once()
    assert context.bot.send_document.call_args.kwargs["filename"] == "cv.pdf"


def test_files_empty(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task", lambda short: ("abcd1234", "Apply MT", "pending")
    )
    monkeypatch.setattr("app.telegram_bot.bot.resolve_task_id", lambda short: "full-id")
    monkeypatch.setattr("app.telegram_bot.bot.list_for_task", lambda tid: [])
    update = make_update(make_message())
    context = make_context()
    context.args = ["abcd1234"]
    get_settings.cache_clear()
    asyncio.run(files_command(update, context))
    assert "no attachments" in update.message.reply_text.call_args.args[0]


def test_files_unknown_task(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot.get_task", lambda short: None)
    update = make_update(make_message())
    context = make_context()
    context.args = ["ffff9999"]
    get_settings.cache_clear()
    asyncio.run(files_command(update, context))
    assert "couldn't find that task" in update.message.reply_text.call_args.args[0]
