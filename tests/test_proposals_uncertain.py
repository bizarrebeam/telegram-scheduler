from datetime import UTC, datetime, timedelta

import httpx

from app.services import proposals


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, proposal, session, task):
        self.proposal = proposal
        self.session = session
        self.task = task
        self.calls: list[str] = []

    def execute(self, sql, params=None):
        self.calls.append(sql)
        if "FROM schedule_proposals" in sql:
            return _Result(self.proposal)
        if "FROM task_sessions" in sql:
            return _Result(self.session)
        if "FROM tasks" in sql:
            return _Result(self.task)
        return _Result(None)


class _FakeCtx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def test_approve_timeout_leaves_proposal_pending(monkeypatch) -> None:
    now = datetime.now(UTC)
    proposal = {
        "id": "prop-1",
        "status": "pending",
        "expires_at": now + timedelta(minutes=20),
        "session_id": "sess-1",
        "task_id": "task-1",
        "proposed_start_at": now + timedelta(hours=1),
        "proposed_end_at": now + timedelta(hours=2),
    }
    session = {
        "id": "sess-1",
        "planned_duration_minutes": 60,
    }
    task = {"id": "task-1", "title": "Apply", "first_step": "Open form"}
    conn = _FakeConn(proposal, session, task)

    monkeypatch.setattr(proposals, "get_conn_dict", lambda: _FakeCtx(conn))
    monkeypatch.setattr(proposals, "load_user_credentials", lambda uid: object())
    monkeypatch.setattr(
        proposals.settings_service,
        "get_or_create_user_settings",
        lambda uid: {"google_calendar_id": "primary"},
    )
    monkeypatch.setattr(proposals, "get_busy_intervals", lambda *a, **k: [])
    monkeypatch.setattr(proposals, "_own_planned_intervals", lambda *a, **k: [])
    monkeypatch.setattr(proposals, "history", type("H", (), {"record": lambda *a, **k: None}))

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("calendar timed out")

    monkeypatch.setattr(proposals, "_create_event_idempotent", timeout)

    result = proposals.approve_proposal("prop-1", 111)

    assert result["status"] == "event_uncertain"
    assert result["error_code"] == "timeout"
    assert not any("UPDATE schedule_proposals" in sql for sql in conn.calls)

def test_retry_recovers_own_existing_event_instead_of_slot_taken(monkeypatch) -> None:
    now = datetime.now(UTC)
    proposal = {
        "id": "prop-1",
        "status": "pending",
        "expires_at": now + timedelta(minutes=20),
        "session_id": "sess-1",
        "task_id": "task-1",
        "proposed_start_at": now + timedelta(hours=1),
        "proposed_end_at": now + timedelta(hours=2),
    }
    session = {"id": "sess-1", "planned_duration_minutes": 60}
    task = {"id": "task-1", "title": "Apply", "first_step": "Open form"}
    conn = _FakeConn(proposal, session, task)

    monkeypatch.setattr(proposals, "get_conn_dict", lambda: _FakeCtx(conn))
    monkeypatch.setattr(proposals, "load_user_credentials", lambda uid: object())
    monkeypatch.setattr(
        proposals.settings_service,
        "get_or_create_user_settings",
        lambda uid: {"google_calendar_id": "primary"},
    )
    monkeypatch.setattr(
        proposals,
        "get_busy_intervals",
        lambda *a, **k: [
            (
                proposal["proposed_start_at"],
                proposal["proposed_end_at"],
            )
        ],
    )
    monkeypatch.setattr(proposals, "_own_planned_intervals", lambda *a, **k: [])
    monkeypatch.setattr(
        proposals,
        "get_event",
        lambda creds, calendar_id, event_id: {"id": event_id},
    )
    monkeypatch.setattr(
        proposals,
        "create_event",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("create_event must not be called")
        ),
    )
    monkeypatch.setattr(proposals, "_enqueue_session_jobs", lambda session: None)
    monkeypatch.setattr(
        proposals, "history", type("H", (), {"record": lambda *a, **k: None})
    )

    result = proposals.approve_proposal("prop-1", 111)

    assert result["status"] == "approved"
    assert result["event_id"] == "sess1"
    assert any("UPDATE schedule_proposals" in sql for sql in conn.calls)
