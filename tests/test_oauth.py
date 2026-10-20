from app.config import get_settings


def make_settings(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client123.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "shh")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI", "http://localhost:8000/oauth/google/callback"
    )
    get_settings.cache_clear()


def test_authorization_url_contains_required_parts(monkeypatch) -> None:
    make_settings(monkeypatch)
    from app.google.oauth import create_authorization_state

    state, url = create_authorization_state(12345)
    assert url.startswith("https://accounts.google.com/o/oauth2/auth")
    assert "client_id=client123.apps.googleusercontent.com" in url
    assert "calendar.readonly" in url
    assert "calendar.events" in url
    assert f"state={state}" in url
    assert "access_type=offline" in url


def test_state_roundtrip_and_expiry(monkeypatch) -> None:
    make_settings(monkeypatch)
    from app.google.oauth import create_authorization_state, pop_valid_state

    state, _ = create_authorization_state(999)
    parsed = pop_valid_state(state)
    assert parsed is not None
    user_id, code_verifier = parsed
    assert user_id == 999
    assert code_verifier
    assert pop_valid_state(state) is None


def test_exchange_passes_code_verifier(monkeypatch) -> None:
    make_settings(monkeypatch)
    captured: dict = {}

    class FakeFlow:
        def __init__(self, *args, **kwargs):
            self.code_verifier = None
            self.credentials = type(
                "Creds", (), {"to_json": lambda self: '{"refresh_token": "x"}'}
            )()

        @classmethod
        def from_client_config(cls, *args, **kwargs):
            return cls()

        def fetch_token(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.google.oauth.Flow", FakeFlow)
    monkeypatch.setattr(
        "app.google.oauth.save_credentials_json", lambda *a, **k: None
    )
    from app.google.oauth import exchange_code_for_user

    exchange_code_for_user("thecode", 5, "verifier-123")
    assert captured["code"] == "thecode"
    assert captured["code_verifier"] == "verifier-123"


def test_state_invalid(monkeypatch) -> None:
    make_settings(monkeypatch)
    from app.google.oauth import pop_valid_state

    assert pop_valid_state("nope") is None
