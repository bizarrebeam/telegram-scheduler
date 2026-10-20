import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.config import get_settings
from app.google.calendar import create_all_day_event
from app.parsing.basic import parse_captured_message, parse_deadline_date
from app.telegram_bot import bot
from app.telegram_bot.bot import (
    callback_handler,
    capture_message,
    deadline_command,
)
from app.telegram_bot.ui import deadline_keyboard, deadline_text
from app.workers import runner

CREDS = type("C", (), {"token": "x"})()


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def make_update(text: str):
    return SimpleNamespace(
        update_id=77,
        effective_user=SimpleNamespace(id=111),
        effective_chat=SimpleNamespace(id=555),
        message=SimpleNamespace(
            text=text,
            message_id=8,
            reply_text=AsyncMock(),
        ),
    )


def make_callback(data: str):
    return SimpleNamespace(
        update_id=78,
        effective_user=SimpleNamespace(id=111),
        effective_chat=SimpleNamespace(id=555),
        callback_query=SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=555),
        ),
    )


def make_context():
    return SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock()),
        args=[],
        user_data={},
    )


def run(handler, update, context=None, args=None):
    get_settings.cache_clear()
    context = context or make_context()
    context.args = args or []
    asyncio.run(handler(update, context))


def patch_capture(monkeypatch, *, reminder_id="rem-1"):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot.has_credentials", lambda uid: True)
    monkeypatch.setattr(
        "app.telegram_bot.bot.create_task_from_message",
        lambda **kwargs: ("abcd1234", kwargs["title"]),
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.resolve_task_id", lambda short: "task-full"
    )
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.deadlines.propose_deadline",
        lambda task_id: {
            "status": "proposed",
            "reminder": {"id": reminder_id},
        },
    )


def test_parser_extracts_deadline_and_cleans_title() -> None:
    parsed = parse_captured_message(
        "prepare CCEP sampai 25 Oktober", today=date(2026, 10, 20)
    )
    assert parsed.title == "prepare CCEP"
    assert parsed.deadline_date == date(2026, 10, 25)
    assert parsed.deadline_unclear is False
    assert parsed.deadline_at.hour == 23


def test_parser_flags_ambiguous_deadline_date() -> None:
    parsed = parse_captured_message("deadline 25/10", today=date(2026, 10, 20))
    assert parsed.deadline_unclear is True
    assert parsed.deadline_date is None


def test_parser_detects_reminder_only() -> None:
    parsed = parse_captured_message(
        "prepare CCEP sampai 25 Oktober, reminder only",
        today=date(2026, 10, 20),
    )
    assert parsed.reminder_only is True


def test_deadline_date_argument_accepts_iso_and_month_name() -> None:
    assert parse_deadline_date("2026-10-25") == date(2026, 10, 25)
    assert parse_deadline_date("25 Oktober 2026") == date(2026, 10, 25)


def test_all_day_event_uses_date_fields_not_midnight_datetime(monkeypatch) -> None:
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return FakeResponse(200, {"id": json["id"]})

    monkeypatch.setattr("app.google.calendar.httpx.post", fake_post)
    event_id = create_all_day_event(
        CREDS,
        "primary",
        summary="DEADLINE: CCEP",
        description="d",
        event_date=date(2026, 10, 25),
        event_id="rem123",
    )
    assert event_id == "rem123"
    assert captured["json"]["start"] == {"date": "2026-10-25"}
    assert captured["json"]["end"] == {"date": "2026-10-26"}
    assert captured["json"]["transparency"] == "transparent"
    assert "dateTime" not in captured["json"]["start"]


def test_deadline_only_message_gets_meaningful_title() -> None:
    parsed = parse_captured_message(
        "deadline 25 Oktober", today=date(2026, 10, 20)
    )
    assert parsed.title == "Deadline reminder"
    assert parsed.deadline_date == date(2026, 10, 25)


def test_deadline_event_id_is_calendar_safe(monkeypatch) -> None:
    captured = {}

    def fake_create(creds, calendar_id, **kwargs):
        captured.update(kwargs)
        return kwargs["event_id"]

    monkeypatch.setattr(
        "app.services.deadlines.create_all_day_event", fake_create
    )
    from app.services.deadlines import _create_all_day_idempotent

    event_id = _create_all_day_idempotent(
        None,
        "primary",
        reminder_id="c4203afe-dd7f-43ae-b7bc-be42aba7525d",
        summary="DEADLINE: prepare Y",
        description="d",
        event_date=date(2026, 10, 23),
    )
    assert event_id == "dlc4203afedd7f43aeb7bcbe42aba7525d"
    assert "-" not in captured["event_id"]


def test_deadline_text_separates_deadline_from_preparation() -> None:
    text = deadline_text(
        "prepare CCEP", date(2026, 10, 25), 60, "estimate", True
    )
    assert "Due: Sunday, 25 Oct 2026 (all-day)" in text
    assert "Preparation estimate: 60 minutes (provisional)" in text
    assert "separate preparation proposal" in text
    keyboard = deadline_keyboard("rem-1", "abcd1234")
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "deadline_ok:rem-1" in datas
    assert "prep:abcd1234" in datas
    assert "deadline_only:rem-1" in datas


def test_capture_creates_deadline_and_preparation_separately(monkeypatch) -> None:
    patch_capture(monkeypatch)
    work = AsyncMock(
        return_value={
            "status": "proposed",
            "proposal": {
                "id": "p1",
                "proposed_start_at": datetime(2026, 10, 21, 1, tzinfo=UTC),
                "proposed_end_at": datetime(2026, 10, 21, 2, tzinfo=UTC),
            },
            "task": {
                "title": "prepare CCEP",
                "estimated_duration_minutes": 60,
                "deadline_at": None,
            },
        }
    )
    calls = []

    def fake_work(*args):
        calls.append(args)
        return work.return_value

    monkeypatch.setattr("app.telegram_bot.bot.propose_for_task", fake_work)
    update = make_update("prepare CCEP sampai 25 Oktober")
    run(capture_message, update)
    replies = [c.args[0] for c in update.message.reply_text.call_args_list]
    assert any("Deadline reminder" in text for text in replies)
    assert any("21 Oct" in text for text in replies)
    assert len(calls) == 1


def test_reminder_only_capture_never_requests_work_session(monkeypatch) -> None:
    patch_capture(monkeypatch)
    work = AsyncMock()
    monkeypatch.setattr("app.telegram_bot.bot.propose_for_task", work)
    monkeypatch.setattr(
        "app.telegram_bot.bot.deadlines.set_reminder_only", lambda *a: {}
    )
    update = make_update("prepare CCEP sampai 25 Oktober, reminder only")
    run(capture_message, update)
    work.assert_not_called()
    status = update.message.reply_text.return_value
    assert "Reminder only" in status.edit_text.call_args.args[0]


def test_preparation_only_capture_has_no_deadline_proposal(monkeypatch) -> None:
    patch_capture(monkeypatch)
    deadline = AsyncMock()
    monkeypatch.setattr("app.telegram_bot.bot.deadlines.propose_deadline", deadline)
    monkeypatch.setattr(
        "app.telegram_bot.bot.propose_for_task",
        lambda *a: {"status": "proposed", "proposal": {
            "id": "p1",
            "proposed_start_at": datetime(2026, 10, 21, 1, tzinfo=UTC),
            "proposed_end_at": datetime(2026, 10, 21, 2, tzinfo=UTC),
        }, "task": {"title": "Apply MT", "estimated_duration_minutes": 60,
                    "deadline_at": None}},
    )
    update = make_update("Apply MT")
    run(capture_message, update)
    deadline.assert_not_called()


def test_ambiguous_deadline_asks_one_clarification(monkeypatch) -> None:
    patch_capture(monkeypatch)
    work = AsyncMock()
    monkeypatch.setattr("app.telegram_bot.bot.propose_for_task", work)
    update = make_update("deadline 25/10")
    run(capture_message, update)
    work.assert_not_called()
    status = update.message.reply_text.return_value
    assert "What date is the deadline?" in status.edit_text.call_args.args[0]


def test_approving_deadline_does_not_schedule_preparation(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr("app.telegram_bot.bot._timezone", lambda uid: "Asia/Jakarta")
    monkeypatch.setattr(
        "app.telegram_bot.bot.deadlines.approve_deadline",
        lambda rid, uid: {
            "status": "approved",
            "event_date": date(2026, 10, 25),
            "task_title": "prepare CCEP",
        },
    )
    work = AsyncMock()
    monkeypatch.setattr("app.telegram_bot.bot.propose_for_task", work)
    update = make_callback("deadline_ok:rem-1")
    run(callback_handler, update)
    work.assert_not_called()
    assert "all-day" in update.callback_query.edit_message_text.call_args.args[0]


def test_repeated_deadline_approval_press_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    approve = AsyncMock()
    monkeypatch.setattr("app.telegram_bot.bot.deadlines.approve_deadline", approve)
    assert bot._claim_request(111, "deadline_ok:rem-1") is True
    try:
        update = make_callback("deadline_ok:rem-1")
        run(callback_handler, update)
        update.callback_query.answer.assert_awaited_once_with(
            "I'm already working on this request."
        )
        approve.assert_not_called()
    finally:
        bot._release_request(111, "deadline_ok:rem-1")


def test_deadline_change_does_not_move_sessions(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    monkeypatch.setattr(
        "app.telegram_bot.bot.get_task_details",
        lambda short: {
            "title": "prepare CCEP",
            "estimated_duration_minutes": 60,
            "duration_source": "estimate",
        },
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.resolve_task_id", lambda short: "task-full"
    )
    monkeypatch.setattr(
        "app.telegram_bot.bot.deadlines.change_deadline",
        lambda task_id, day: {"status": "pending_update", "reminder": {"id": "r1"}},
    )
    reschedule = AsyncMock()
    monkeypatch.setattr(bot.session_actions, "reschedule_session", reschedule)
    update = make_update("/deadline abcd1234 2026-10-30")
    run(deadline_command, update, args=["abcd1234", "2026-10-30"])
    assert "Approve it before I update the calendar" in (
        update.message.reply_text.call_args.args[0]
    )
    reschedule.assert_not_called()


def test_past_due_task_notifies_without_completing(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
    marked = AsyncMock()
    monkeypatch.setattr(
        runner.deadlines,
        "overdue_tasks",
        lambda now: [
            {
                "task_id": "t1",
                "title": "prepare CCEP",
                "deadline_date": date(2026, 10, 1),
            }
        ],
    )
    monkeypatch.setattr(
        runner.deadlines, "mark_overdue_notified", marked
    )
    sent = AsyncMock()
    monkeypatch.setattr(runner, "_notify", sent)
    asyncio.run(runner._handle_deadlines(SimpleNamespace()))
    text = sent.call_args.args[1]
    assert "still open" in text
    marked.assert_called_once()
    assert "complete" not in text.lower()

