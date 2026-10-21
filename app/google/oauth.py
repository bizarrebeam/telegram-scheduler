import json
import logging
import secrets
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.config import get_settings
from app.errors import error_fields
from app.google.token_store import load_credentials_json, save_credentials_json
from app.observability import log_event

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

_STATE_TTL_SECONDS = 600
_auth_states: dict[str, tuple[int, float, str | None]] = {}


def _client_config() -> dict:
    settings = get_settings()
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def create_authorization_state(telegram_user_id: int) -> tuple[str, str]:
    state = secrets.token_urlsafe(32)
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, state=state)
    flow.redirect_uri = get_settings().google_redirect_uri
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    _auth_states[state] = (
        telegram_user_id,
        time.monotonic() + _STATE_TTL_SECONDS,
        flow.code_verifier,
    )
    return state, url


def pop_valid_state(state: str) -> tuple[int, str | None] | None:
    entry = _auth_states.pop(state, None)
    if entry is None:
        return None
    user_id, expires_at, code_verifier = entry
    if time.monotonic() > expires_at:
        return None
    return user_id, code_verifier


def exchange_code_for_user(
    code: str, telegram_user_id: int, code_verifier: str | None = None
) -> None:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = get_settings().google_redirect_uri
    kwargs: dict = {}
    if code_verifier:
        flow.code_verifier = code_verifier
        kwargs["code_verifier"] = code_verifier
    flow.fetch_token(code=code, **kwargs)
    creds = flow.credentials
    save_credentials_json(telegram_user_id, creds.to_json(), " ".join(SCOPES))


def load_user_credentials(telegram_user_id: int) -> Credentials | None:
    raw = load_credentials_json(telegram_user_id)
    if raw is None:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(raw))
    if creds.expired and creds.refresh_token:
        started = time.perf_counter()
        try:
            creds.refresh(Request())
        except Exception as exc:
            log_event(
                logging.WARNING,
                "google_token_refresh",
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                outcome="error",
                **error_fields(exc),
            )
            raise
        log_event(
            logging.INFO,
            "google_token_refresh",
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            outcome="ok",
        )
        save_credentials_json(telegram_user_id, creds.to_json(), " ".join(SCOPES))
    return creds


def has_credentials(telegram_user_id: int) -> bool:
    return load_credentials_json(telegram_user_id) is not None
