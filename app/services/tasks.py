from app.db import get_conn, get_conn_dict


def create_task_from_message(
    *,
    title: str,
    description: str,
    category: str,
    estimated_duration_minutes: int,
    duration_source: str,
    telegram_message_id: int,
    telegram_update_id: int,
    first_step: str | None = None,
    deadline_at=None,
    deadline_date=None,
    reminder_only: bool = False,
) -> tuple[str, str] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO tasks (
                title, description, category, estimated_duration_minutes,
                duration_source, first_step, deadline_at, deadline_date,
                reminder_only, source_telegram_message_id,
                source_telegram_update_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_telegram_update_id) DO NOTHING
            RETURNING left(id::text, 8) AS short_id, title
            """,
            (
                title,
                description,
                category,
                estimated_duration_minutes,
                duration_source,
                first_step,
                deadline_at,
                deadline_date,
                reminder_only,
                telegram_message_id,
                telegram_update_id,
            ),
        ).fetchone()
        return row


def create_subtasks(parent_task_id: str, steps: list[str]) -> list[tuple[str, str]]:
    cleaned = [s.strip() for s in steps if s and s.strip()]
    if not cleaned:
        return []
    with get_conn() as conn:
        parent = conn.execute(
            """
            SELECT estimated_duration_minutes FROM tasks WHERE id = %s
            """,
            (parent_task_id,),
        ).fetchone()
        if parent is None:
            return []
        per_step = max(parent[0] // len(cleaned), 5)
        created = []
        for step in cleaned:
            row = conn.execute(
                """
                INSERT INTO tasks (
                    parent_task_id, title, description, category,
                    estimated_duration_minutes, duration_source
                )
                VALUES (%s, %s, %s, 'other', %s, 'estimate')
                RETURNING left(id::text, 8) AS short_id, title
                """,
                (parent_task_id, step[:100], step, per_step),
            ).fetchone()
            created.append(row)
        return created


def list_open_tasks() -> list[tuple]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT left(id::text, 8) AS short_id, title, estimated_duration_minutes
            FROM tasks
            WHERE status IN ('captured', 'pending')
              AND parent_task_id IS NULL
              AND reminder_only = false
            ORDER BY created_at
            """
        ).fetchall()


def create_task(
    *,
    title: str,
    description: str | None = None,
    category: str = "other",
    estimated_duration_minutes: int = 60,
    duration_source: str = "estimate",
    parent_task_id: str | None = None,
    telegram_message_id: int | None = None,
) -> str:
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO tasks (
                title, description, category, estimated_duration_minutes,
                duration_source, parent_task_id, source_telegram_message_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                title,
                description,
                category,
                estimated_duration_minutes,
                duration_source,
                parent_task_id,
                telegram_message_id,
            ),
        ).fetchone()
        return row[0]


def find_task_by_source_message(message_id: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT id::text AS id, left(id::text, 8) AS short_id, title
            FROM tasks
            WHERE source_telegram_message_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (message_id,),
        ).fetchone()


def complete_task(short_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE tasks
            SET status = 'completed', completed_at = now()
            WHERE left(id::text, 8) = %s
              AND status IN ('captured', 'pending', 'scheduled', 'in_progress')
            RETURNING 1
            """,
            (short_id,),
        ).fetchone()
        if row:
            return "completed"
        exists = conn.execute(
            "SELECT 1 FROM tasks WHERE left(id::text, 8) = %s", (short_id,)
        ).fetchone()
        return "invalid_state" if exists else "not_found"


def skip_task(short_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE tasks
            SET status = 'skipped'
            WHERE left(id::text, 8) = %s
              AND status IN ('captured', 'pending', 'scheduled')
            RETURNING 1
            """,
            (short_id,),
        ).fetchone()
        if row:
            return "skipped"
        exists = conn.execute(
            "SELECT 1 FROM tasks WHERE left(id::text, 8) = %s", (short_id,)
        ).fetchone()
        return "invalid_state" if exists else "not_found"


def get_task(short_id: str) -> tuple | None:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT left(id::text, 8) AS short_id, title, status
            FROM tasks
            WHERE left(id::text, 8) = %s
            """,
            (short_id,),
        ).fetchone()


def resolve_task_id(short_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id::text FROM tasks WHERE left(id::text, 8) = %s", (short_id,)
        ).fetchone()
        return row[0] if row else None


def latest_open_task_id() -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id::text FROM tasks
            WHERE status IN ('captured', 'pending')
              AND parent_task_id IS NULL
              AND reminder_only = false
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        return row[0] if row else None


def reschedule_task(short_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE tasks
            SET status = 'captured'
            WHERE left(id::text, 8) = %s
              AND status IN ('completed', 'skipped', 'cancelled')
            RETURNING 1
            """,
            (short_id,),
        ).fetchone()
        if row:
            return "captured"
        exists = conn.execute(
            "SELECT 1 FROM tasks WHERE left(id::text, 8) = %s", (short_id,)
        ).fetchone()
        return "invalid_state" if exists else "not_found"


def delete_task(short_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "DELETE FROM tasks WHERE left(id::text, 8) = %s RETURNING 1",
            (short_id,),
        ).fetchone()
        return "deleted" if row else "not_found"


def get_task_details(short_id: str) -> dict | None:
    with get_conn_dict() as conn:
        task = conn.execute(
            """
            SELECT id::text AS id, left(id::text, 8) AS short_id, title,
                   description, category, deadline_date, reminder_only,
                   deadline_at, estimated_duration_minutes, duration_source,
                   first_step, status,
                   parent_task_id IS NOT NULL AS is_subtask
            FROM tasks
            WHERE left(id::text, 8) = %s
            """,
            (short_id,),
        ).fetchone()
        if task is None:
            return None
        task["attachment_count"] = conn.execute(
            """
            SELECT count(*) AS n
            FROM task_attachments a
            JOIN tasks t ON t.id = a.task_id
            WHERE left(t.id::text, 8) = %s
            """,
            (short_id,),
        ).fetchone()["n"]
        task["subtask_count"] = conn.execute(
            """
            SELECT count(*) AS n
            FROM tasks c
            JOIN tasks p ON p.id = c.parent_task_id
            WHERE left(p.id::text, 8) = %s
            """,
            (short_id,),
        ).fetchone()["n"]
    return task


def cancel_task(short_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE tasks
            SET status = 'cancelled'
            WHERE left(id::text, 8) = %s
              AND status IN ('captured', 'pending', 'scheduled')
            RETURNING 1
            """,
            (short_id,),
        ).fetchone()
        if row:
            return "cancelled"
        exists = conn.execute(
            "SELECT 1 FROM tasks WHERE left(id::text, 8) = %s", (short_id,)
        ).fetchone()
        return "invalid_state" if exists else "not_found"




def set_duration(short_id: str, minutes: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE tasks
            SET estimated_duration_minutes = %s, duration_source = 'user'
            WHERE left(id::text, 8) = %s
              AND status NOT IN ('completed', 'cancelled')
            RETURNING left(id::text, 8) AS short_id
            """,
            (minutes, short_id),
        ).fetchone()
        if row is not None:
            return "updated"
        exists = conn.execute(
            "SELECT 1 FROM tasks WHERE left(id::text, 8) = %s", (short_id,)
        ).fetchone()
        return "not_actionable" if exists else "not_found"
