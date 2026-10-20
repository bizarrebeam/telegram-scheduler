from datetime import UTC, datetime, timedelta

from app.scheduler.priority import describe_priority, sort_open_tasks


def task(task_id, *, deadline=None, priority=3, created=None):
    return {
        "id": task_id,
        "deadline_at": deadline,
        "priority": priority,
        "created_at": created or datetime(2026, 1, 1, tzinfo=UTC),
    }


def test_deadline_tasks_before_no_deadline() -> None:
    later = task("b", deadline=datetime(2026, 10, 30, tzinfo=UTC))
    nod = task("a")
    ordered = sort_open_tasks([nod, later])
    assert [t["id"] for t in ordered] == ["b", "a"]


def test_earlier_deadline_wins() -> None:
    soon = task("soon", deadline=datetime(2026, 10, 20, tzinfo=UTC))
    late = task("late", deadline=datetime(2026, 10, 25, tzinfo=UTC))
    ordered = sort_open_tasks([late, soon])
    assert [t["id"] for t in ordered] == ["soon", "late"]


def test_priority_breaks_tie() -> None:
    low = task("low", deadline=datetime(2026, 10, 20, tzinfo=UTC), priority=5)
    high = task("high", deadline=datetime(2026, 10, 20, tzinfo=UTC), priority=1)
    ordered = sort_open_tasks([low, high])
    assert [t["id"] for t in ordered] == ["high", "low"]


def test_created_at_breaks_remaining_tie() -> None:
    older = task("older", created=datetime(2026, 1, 1, tzinfo=UTC))
    newer = task("newer", created=datetime(2026, 1, 2, tzinfo=UTC))
    ordered = sort_open_tasks([newer, older])
    assert [t["id"] for t in ordered] == ["older", "newer"]


def test_order_is_deterministic_for_full_ties() -> None:
    a = task("aaa")
    b = task("bbb")
    assert [t["id"] for t in sort_open_tasks([b, a])] == ["aaa", "bbb"]


def test_describe_priority_hours_and_days() -> None:
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    assert "hours" in describe_priority(now + timedelta(hours=5), now)
    assert "days" in describe_priority(now + timedelta(days=3), now)
    assert describe_priority(None, now) == "No deadline"
    assert "passed" in describe_priority(now - timedelta(hours=1), now)
