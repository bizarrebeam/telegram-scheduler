from datetime import UTC, datetime

from app.telegram_bot.ui import (
    fmt_datetime,
    fmt_range,
    fmt_short_datetime,
    fmt_time,
    missed_keyboard,
    proposal_keyboard,
    proposal_text,
    session_keyboard,
)


def test_fmt_time_converts_to_jakarta() -> None:
    moment = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
    assert fmt_time(moment, "Asia/Jakarta") == "20:00"


def test_fmt_datetime_includes_day_name() -> None:
    moment = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
    assert fmt_datetime(moment, "Asia/Jakarta").startswith("Monday")


def test_fmt_datetime_uses_short_month_name() -> None:
    moment = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
    assert fmt_datetime(moment, "Asia/Jakarta") == "Monday, 19 Oct, 20:00"


def test_fmt_short_datetime_omits_day_name() -> None:
    moment = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
    assert fmt_short_datetime(moment, "Asia/Jakarta") == "19 Oct, 20:00"


def test_fmt_range_uses_en_dash() -> None:
    start = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
    end = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
    assert fmt_range(start, end, "Asia/Jakarta") == "Monday, 19 Oct, 20:00\u201321:00"


def test_proposal_keyboard_has_stable_callback_data() -> None:
    markup = proposal_keyboard("prop-1")
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "approve:prop-1" in datas
    assert "reject:prop-1" in datas
    assert "another:prop-1" in datas


def test_session_keyboard_actions() -> None:
    markup = session_keyboard("sess-9")
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert datas == ["done:sess-9", "addtime:sess-9", "reschedule:sess-9", "skip:sess-9"]


def test_missed_keyboard_offers_start_now() -> None:
    markup = missed_keyboard("sess-9")
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "startnow:sess-9" in datas


def test_proposal_text_mentions_approval() -> None:
    text = proposal_text(
        task_title="Apply MT",
        start=datetime(2026, 10, 19, 13, 0, tzinfo=UTC),
        end=datetime(2026, 10, 19, 14, 0, tzinfo=UTC),
        duration_minutes=60,
        deadline_at=None,
        timezone="Asia/Jakarta",
    )
    assert "Apply MT" in text
    assert "20:00" in text
    assert "approve" in text
    assert "60 minutes" in text
