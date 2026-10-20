from datetime import UTC, datetime

import pytest

from app.config import get_settings
from app.parsing import ai
from app.parsing.basic import parse_captured_message


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def with_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _reply(payload: dict) -> FakeResponse:
    import json

    return FakeResponse(
        200, {"choices": [{"message": {"content": json.dumps(payload)}}]}
    )


def test_no_key_returns_none(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    assert ai.parse_with_ai("apa saja") is None


def test_ai_parse_happy_path(monkeypatch, with_openai) -> None:
    monkeypatch.setattr(
        ai.httpx,
        "post",
        lambda *a, **k: _reply(
            {
                "title": "Apply MT",
                "description": "kirim CV",
                "category": "work",
                "estimated_duration_minutes": 90,
                "first_step": "Buka email lowongan",
                "deadline_at": "2026-10-25T17:00:00+07:00",
            }
        ),
    )
    parsed = ai.parse_with_ai("apply MT besok")
    assert parsed is not None
    assert parsed.title == "Apply MT"
    assert parsed.category == "work"
    assert parsed.estimated_duration_minutes == 90
    assert parsed.duration_source == "user"
    assert parsed.first_step == "Buka email lowongan"
    assert parsed.deadline_at == datetime(2026, 10, 25, 10, 0, tzinfo=UTC)


def test_invalid_category_falls_back_to_other(monkeypatch, with_openai) -> None:
    monkeypatch.setattr(
        ai.httpx,
        "post",
        lambda *a, **k: _reply(
            {
                "title": "Baca bab 3",
                "category": "kuliah-banget",
                "estimated_duration_minutes": 30,
            }
        ),
    )
    parsed = ai.parse_with_ai("baca bab 3")
    assert parsed.category == "other"


def test_out_of_range_duration_uses_estimate(monkeypatch, with_openai) -> None:
    monkeypatch.setattr(
        ai.httpx,
        "post",
        lambda *a, **k: _reply(
            {"title": "Nulis", "category": "work", "estimated_duration_minutes": 99999}
        ),
    )
    parsed = ai.parse_with_ai("nulis 2 jam")
    assert parsed.estimated_duration_minutes == 120
    assert parsed.duration_source == "user"


def test_missing_title_falls_back_to_basic_parser(monkeypatch, with_openai) -> None:
    monkeypatch.setattr(
        ai.httpx, "post", lambda *a, **k: _reply({"category": "work"})
    )
    parsed = ai.parse_with_ai("Apply MT, butuh 1 jam")
    assert parsed == parse_captured_message("Apply MT, butuh 1 jam")


def test_bad_json_falls_back(monkeypatch, with_openai) -> None:
    class Bad(FakeResponse):
        def json(self):
            return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr(ai.httpx, "post", lambda *a, **k: Bad(200, {}))
    assert ai.parse_with_ai("apa saja") is None


def test_http_error_falls_back(monkeypatch, with_openai) -> None:
    monkeypatch.setattr(ai.httpx, "post", lambda *a, **k: FakeResponse(500, {}))
    assert ai.parse_with_ai("apa saja") is None


def test_parse_message_uses_fallback_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    assert ai.parse_message("Apply MT, 30 menit") == parse_captured_message(
        "Apply MT, 30 menit"
    )
