from app.config import Settings


def make_settings(**overrides: str) -> Settings:
    base = {
        "telegram_allowed_user_ids": "111, 222",
    }
    base.update(overrides)
    return Settings(**base)


def test_authorized_user() -> None:
    settings = make_settings()
    from app.telegram_bot.auth import is_authorized

    assert is_authorized(111, settings) is True
    assert is_authorized(222, settings) is True


def test_unauthorized_user() -> None:
    settings = make_settings()
    from app.telegram_bot.auth import is_authorized

    assert is_authorized(999, settings) is False


def test_empty_allowlist_fails_closed() -> None:
    settings = make_settings(telegram_allowed_user_ids="")
    from app.telegram_bot.auth import is_authorized

    assert is_authorized(111, settings) is False


def test_none_user_id_rejected() -> None:
    settings = make_settings()
    from app.telegram_bot.auth import is_authorized

    assert is_authorized(None, settings) is False
