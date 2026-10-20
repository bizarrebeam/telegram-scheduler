from fastapi.testclient import TestClient

from app.main import app


def test_callback_invalid_state_returns_400() -> None:
    client = TestClient(app)
    response = client.get(
        "/oauth/google/callback", params={"code": "abc", "state": "invalid"}
    )
    assert response.status_code == 400


def test_callback_missing_params_returns_400() -> None:
    client = TestClient(app)
    response = client.get("/oauth/google/callback")
    assert response.status_code == 400


def test_callback_error_param_returns_400() -> None:
    client = TestClient(app)
    response = client.get(
        "/oauth/google/callback", params={"error": "access_denied"}
    )
    assert response.status_code == 400
