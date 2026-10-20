from app.db import get_conn_dict


def get_or_create_user_settings(telegram_user_id: int) -> dict:
    with get_conn_dict() as conn:
        row = conn.execute(
            "SELECT * FROM user_settings WHERE telegram_user_id = %s",
            (telegram_user_id,),
        ).fetchone()
        if row is not None:
            return row
        return conn.execute(
            "INSERT INTO user_settings (telegram_user_id) VALUES (%s) RETURNING *",
            (telegram_user_id,),
        ).fetchone()
