from datetime import UTC, datetime

from app.db import get_conn_dict
from app.scheduler.priority import describe_priority, sort_open_tasks
from app.services import sessions as sessions_service
from app.services import settings as settings_service

OPEN_STATUSES = ("captured", "pending", "scheduled", "in_progress")

DEFAULT_FIRST_STEP = (
    "Open the most relevant file or link. Then write down one small action you "
    "can start in the next two minutes."
)


def open_tasks() -> list[dict]:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = ANY(%s) AND parent_task_id IS NULL
              AND reminder_only = false
            """,
            (list(OPEN_STATUSES),),
        ).fetchall()


def pick_next_action(
    telegram_user_id: int, exclude_task_id: str | None = None
) -> dict:
    user_settings = settings_service.get_or_create_user_settings(telegram_user_id)
    timezone = user_settings["timezone"]
    now = datetime.now(UTC)
    tasks = sort_open_tasks(open_tasks())
    if exclude_task_id:
        tasks = [
            task
            for task in tasks
            if not str(task["id"]).startswith(exclude_task_id)
        ]
    upcoming = sessions_service.next_upcoming(now, timezone)
    if not tasks and upcoming is None:
        return {"status": "empty"}
    result: dict = {"status": "ok", "upcoming": upcoming, "now": now}
    if tasks:
        top = tasks[0]
        result["task"] = top
        result["reason"] = describe_priority(top["deadline_at"], now)
        result["first_step"] = (top.get("first_step") or "").strip() or DEFAULT_FIRST_STEP
        result["alternatives"] = tasks[1:4]
    else:
        result["task"] = None
        result["reason"] = "No open tasks"
        result["first_step"] = DEFAULT_FIRST_STEP
        result["alternatives"] = []
    return result
