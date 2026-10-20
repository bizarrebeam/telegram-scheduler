from app.db import get_conn_dict


def save_attachment(
    *,
    task_id: str,
    telegram_file_id: str,
    file_type: str,
    original_filename: str | None = None,
    upload_status: str = "stored_telegram",
) -> dict:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            INSERT INTO task_attachments
                (task_id, telegram_file_id, file_type, original_filename, upload_status)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (task_id, telegram_file_id, file_type, original_filename, upload_status),
        ).fetchone()


def list_for_task(task_id: str) -> list[dict]:
    with get_conn_dict() as conn:
        return conn.execute(
            """
            SELECT * FROM task_attachments
            WHERE task_id = %s
            ORDER BY created_at
            """,
            (task_id,),
        ).fetchall()


def count_for_task(task_id: str) -> int:
    with get_conn_dict() as conn:
        row = conn.execute(
            "SELECT count(*) AS n FROM task_attachments WHERE task_id = %s",
            (task_id,),
        ).fetchone()
        return row["n"] if row else 0
