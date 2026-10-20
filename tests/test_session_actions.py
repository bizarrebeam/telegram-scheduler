from datetime import UTC, datetime

from app.services import session_actions

NOW = datetime(2026, 10, 19, 20, 0, tzinfo=UTC)


def session(**overrides) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "task_id": "22222222-2222-2222-2222-222222222222",
        "task_title": "Apply MT",
        "status": "in_progress",
        "planned_start_at": NOW,
        "planned_end_at": datetime(2026, 10, 19, 21, 0, tzinfo=UTC),
        "planned_duration_minutes": 60,
        "additional_duration_minutes": 0,
        "calendar_event_id": "evt123",
    }
    base.update(overrides)
    return base


def patch_common(monkeypatch, *, active):
    monkeypatch.setattr(session_actions.history, "record", lambda *a, **k: None)
    monkeypatch.setattr(
        session_actions.sessions_service, "latest_active_session", lambda: active
    )
    monkeypatch.setattr(
        session_actions,
        "_calendar_context",
        lambda uid: (object(), "primary"),
    )


def test_extend_without_active_session(monkeypatch) -> None:
    patch_common(monkeypatch, active=None)
    assert session_actions.extend_active_session(111, 30)["status"] == "no_active_session"


def test_extend_updates_calendar(monkeypatch) -> None:
    patch_common(monkeypatch, active=session())
    updated = session(
        planned_end_at=datetime(2026, 10, 19, 21, 30, tzinfo=UTC),
        additional_duration_minutes=30,
    )
    monkeypatch.setattr(
        session_actions.sessions_service, "extend", lambda *a, **k: updated
    )
    calls = {}
    monkeypatch.setattr(
        session_actions,
        "update_event_time",
        lambda creds, cal, evt, *, start, end: calls.update({"evt": evt, "end": end}) or True,
    )
    result = session_actions.extend_active_session(111, 30)
    assert result["status"] == "extended"
    assert calls["evt"] == "evt123"
    assert calls["end"] == updated["planned_end_at"]


def test_extend_limit_reached(monkeypatch) -> None:
    patch_common(monkeypatch, active=session())
    monkeypatch.setattr(session_actions.sessions_service, "extend", lambda *a, **k: None)
    assert session_actions.extend_active_session(111, 30)["status"] == "limit_reached"


def test_extend_reports_calendar_failure(monkeypatch) -> None:
    patch_common(monkeypatch, active=session())
    monkeypatch.setattr(
        session_actions.sessions_service, "extend", lambda *a, **k: session()
    )

    def boom(*a, **k):
        raise RuntimeError("google down")

    monkeypatch.setattr(session_actions, "update_event_time", boom)
    assert session_actions.extend_active_session(111, 30)["status"] == "calendar_failed"


def test_extend_without_event_skips_calendar(monkeypatch) -> None:
    patch_common(monkeypatch, active=session(calendar_event_id=None))
    monkeypatch.setattr(
        session_actions.sessions_service, "extend", lambda *a, **k: session()
    )

    def boom(*a, **k):
        raise AssertionError("calendar must not be called")

    monkeypatch.setattr(session_actions, "update_event_time", boom)
    assert session_actions.extend_active_session(111, 30)["status"] == "extended"


def test_reschedule_cancels_and_reproposes(monkeypatch) -> None:
    monkeypatch.setattr(session_actions.history, "record", lambda *a, **k: None)
    monkeypatch.setattr(
        session_actions.sessions_service,
        "get_session_with_task",
        lambda sid: session(status="planned"),
    )
    monkeypatch.setattr(
        session_actions.sessions_service, "set_status", lambda sid, st: session()
    )
    monkeypatch.setattr(
        session_actions, "_calendar_context", lambda uid: (object(), "primary")
    )
    monkeypatch.setattr(session_actions, "delete_event", lambda *a, **k: True)
    monkeypatch.setattr(
        session_actions.proposals,
        "propose_for_task",
        lambda task_id, uid: {"status": "proposed"},
    )
    result = session_actions.reschedule_session(session()["id"], 111)
    assert result["status"] == "proposed"
    assert result["previous_session_id"] == session()["id"]


def test_reschedule_keeps_session_when_calendar_delete_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        session_actions.sessions_service,
        "get_session_with_task",
        lambda sid: session(status="planned"),
    )
    status = []
    monkeypatch.setattr(
        session_actions.sessions_service,
        "set_status",
        lambda sid, st: status.append(st),
    )
    monkeypatch.setattr(
        session_actions, "_calendar_context", lambda uid: (object(), "primary")
    )

    def boom(*args, **kwargs):
        raise RuntimeError("calendar down")

    monkeypatch.setattr(session_actions, "delete_event", boom)
    result = session_actions.reschedule_session(session()["id"], 111)
    assert result["status"] == "calendar_failed"
    assert status == []


def test_skip_closed_session_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(
        session_actions.sessions_service,
        "get_session_with_task",
        lambda sid: session(status="completed"),
    )
    assert session_actions.skip_session("x")["status"] == "already_closed"


def test_complete_cancels_reminder_jobs(monkeypatch) -> None:
    sid = session()["id"]
    monkeypatch.setattr(session_actions.history, "record", lambda *a, **k: None)
    monkeypatch.setattr(
        session_actions.sessions_service,
        "get_session_with_task",
        lambda s: session(status="in_progress"),
    )
    monkeypatch.setattr(
        session_actions.sessions_service,
        "mark_completed",
        lambda s, when: session(status="completed"),
    )
    monkeypatch.setattr(
        session_actions.reminders, "mark_response", lambda s: None
    )
    monkeypatch.setattr("app.services.tasks.complete_task", lambda short: "completed")
    cancelled = {}
    monkeypatch.setattr(
        session_actions.jobs,
        "cancel_session_jobs",
        lambda s: cancelled.update({"id": s}) or 3,
    )
    result = session_actions.complete_session(sid)
    assert result["status"] == "completed"
    assert cancelled["id"] == sid


def test_skip_cancels_reminder_jobs(monkeypatch) -> None:
    monkeypatch.setattr(session_actions.history, "record", lambda *a, **k: None)
    monkeypatch.setattr(
        session_actions.sessions_service,
        "get_session_with_task",
        lambda s: session(status="planned"),
    )
    monkeypatch.setattr(
        session_actions.sessions_service, "set_status", lambda s, st: session()
    )
    cancelled = {}
    monkeypatch.setattr(
        session_actions.jobs,
        "cancel_session_jobs",
        lambda s: cancelled.update({"id": s}) or 1,
    )
    assert session_actions.skip_session("x")["status"] == "skipped"
    assert "id" in cancelled
