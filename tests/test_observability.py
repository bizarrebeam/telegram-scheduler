
import logging

from app import db
from app.config import get_settings


def test_db_connect_logs_duration_without_dsn(monkeypatch, caplog) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/db")
    get_settings.cache_clear()
    monkeypatch.setattr("app.db.psycopg.connect", lambda *a, **k: object())

    with caplog.at_level(logging.DEBUG, logger="app"):
        assert db._connect() is not None

    records = [r for r in caplog.records if getattr(r, "event", "") == "db_connect"]
    assert records
    assert records[-1].duration_ms >= 0
    assert records[-1].outcome == "ok"
    assert "secret" not in caplog.text
    get_settings.cache_clear()

from types import SimpleNamespace

from app.google import calendar


def test_google_call_logs_timing_without_token(monkeypatch, caplog) -> None:
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "evt-1"}

    monkeypatch.setattr(calendar.httpx, "get", lambda *a, **k: Response())
    creds = SimpleNamespace(token="secret-token")

    with caplog.at_level(logging.INFO, logger="app"):
        assert calendar.get_event(creds, "primary", "evt-1") == {"id": "evt-1"}

    records = [
        r
        for r in caplog.records
        if getattr(r, "event", "") == "google_calendar_call"
    ]
    assert records
    assert records[-1].operation == "getEvent"
    assert records[-1].status_code == 200
    assert records[-1].duration_ms >= 0
    assert "secret-token" not in caplog.text
