from app.config import Settings


def test_allowed_ids_parsing_strips_spaces() -> None:
    settings = Settings(telegram_allowed_user_ids="123, 456,789")
    assert settings.allowed_telegram_user_ids == frozenset({123, 456, 789})


def test_allowed_ids_empty() -> None:
    settings = Settings(telegram_allowed_user_ids="")
    assert settings.allowed_telegram_user_ids == frozenset()


def test_default_timezone_is_jakarta() -> None:
    assert Settings().timezone == "Asia/Jakarta"
