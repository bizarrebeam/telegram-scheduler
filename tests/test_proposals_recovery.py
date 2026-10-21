from datetime import UTC, datetime

import pytest

from app.google.calendar import EventConflict
from app.services import proposals


def _args():
    start = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    return {
        "creds": object(),
        "calendar_id": "primary",
        "event_id": "evt-1",
        "summary": "[Task] Apply",
        "description": "Task ID: abc",
        "start": start,
        "end": start.replace(hour=10),
    }


def test_event_conflict_recovers_existing_event(monkeypatch) -> None:
    def conflict(*args, **kwargs):
        raise EventConflict("already exists")

    monkeypatch.setattr(proposals, "create_event", conflict)
    monkeypatch.setattr(
        proposals,
        "get_event",
        lambda creds, calendar_id, event_id: {"id": event_id},
    )
    assert proposals._create_event_idempotent(**_args()) == "evt-1"


def test_event_conflict_without_existing_event_reraises(monkeypatch) -> None:
    def conflict(*args, **kwargs):
        raise EventConflict("already exists")

    monkeypatch.setattr(proposals, "create_event", conflict)
    monkeypatch.setattr(
        proposals, "get_event", lambda creds, calendar_id, event_id: None
    )
    with pytest.raises(EventConflict):
        proposals._create_event_idempotent(**_args())
