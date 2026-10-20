from datetime import UTC, datetime

from app.services import overwhelm


def task(task_id, *, deadline=None, priority=3, created=None, first_step=None):
    return {
        "id": task_id,
        "title": f"Tugas {task_id}",
        "deadline_at": deadline,
        "priority": priority,
        "created_at": created or datetime(2026, 1, 1, tzinfo=UTC),
        "first_step": first_step,
        "status": "captured",
    }


def test_empty_when_no_tasks_and_no_sessions(monkeypatch) -> None:
    monkeypatch.setattr(overwhelm, "open_tasks", lambda: [])
    monkeypatch.setattr(
        overwhelm.settings_service,
        "get_or_create_user_settings",
        lambda uid: {"timezone": "Asia/Jakarta"},
    )
    monkeypatch.setattr(overwhelm.sessions_service, "next_upcoming", lambda *a: None)
    result = overwhelm.pick_next_action(111)
    assert result["status"] == "empty"


def test_picks_most_urgent_and_gives_first_step(monkeypatch) -> None:
    monkeypatch.setattr(
        overwhelm,
        "open_tasks",
        lambda: [
            task("later", deadline=datetime(2026, 10, 30, tzinfo=UTC)),
            task("soon", deadline=datetime(2026, 10, 20, tzinfo=UTC), first_step="Buka CV"),
        ],
    )
    monkeypatch.setattr(
        overwhelm.settings_service,
        "get_or_create_user_settings",
        lambda uid: {"timezone": "Asia/Jakarta"},
    )
    monkeypatch.setattr(overwhelm.sessions_service, "next_upcoming", lambda *a: None)
    result = overwhelm.pick_next_action(111)
    assert result["status"] == "ok"
    assert result["task"]["id"] == "soon"
    assert result["first_step"] == "Buka CV"
    assert result["alternatives"][0]["id"] == "later"


def test_default_first_step_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(overwhelm, "open_tasks", lambda: [task("a")])
    monkeypatch.setattr(
        overwhelm.settings_service,
        "get_or_create_user_settings",
        lambda uid: {"timezone": "Asia/Jakarta"},
    )
    monkeypatch.setattr(overwhelm.sessions_service, "next_upcoming", lambda *a: None)
    result = overwhelm.pick_next_action(111)
    assert result["first_step"] == overwhelm.DEFAULT_FIRST_STEP


def test_reports_upcoming_session(monkeypatch) -> None:
    monkeypatch.setattr(overwhelm, "open_tasks", lambda: [])
    monkeypatch.setattr(
        overwhelm.settings_service,
        "get_or_create_user_settings",
        lambda uid: {"timezone": "Asia/Jakarta"},
    )
    monkeypatch.setattr(
        overwhelm.sessions_service,
        "next_upcoming",
        lambda *a: {"task_title": "Olahraga"},
    )
    result = overwhelm.pick_next_action(111)
    assert result["status"] == "ok"
    assert result["upcoming"]["task_title"] == "Olahraga"
