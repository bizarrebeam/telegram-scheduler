from app.config import Settings


def is_authorized(user_id: int | None, settings: Settings) -> bool:
    if user_id is None:
        return False
    allowed = settings.allowed_telegram_user_ids
    if not allowed:
        return False
    return user_id in allowed
