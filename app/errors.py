from __future__ import annotations

from typing import Any

import httpx


def classify_error(exc: BaseException) -> tuple[str, dict[str, Any]]:
    from app.google.calendar import EventConflict, GoogleAuthRequired

    if isinstance(exc, GoogleAuthRequired):
        return "auth_required", {}
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", {}
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        code = "rate_limited" if status == 429 else f"http_{status}"
        return code, {"http_status": status}
    if isinstance(exc, httpx.RequestError):
        return "network", {}
    if isinstance(exc, EventConflict):
        return "event_conflict", {}

    try:
        import psycopg

        if isinstance(exc, psycopg.Error):
            return "database_error", {}
    except ImportError:
        pass
    return "unknown", {}


def error_fields(exc: BaseException) -> dict[str, Any]:
    code, fields = classify_error(exc)
    return {
        "error_type": type(exc).__name__,
        "error_code": code,
        **fields,
    }


def is_uncertain_calendar_error(exc: BaseException) -> bool:
    code, _ = classify_error(exc)
    return code in {"timeout", "network", "event_conflict"} or code.startswith(
        "http_5"
    )
