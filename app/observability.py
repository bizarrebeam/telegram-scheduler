from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from app.errors import error_fields

_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
_ENTRY_POINT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "entry_point", default="-"
)

_ALLOWED_FIELDS = {
    "action",
    "attempt",
    "count",
    "duration_ms",
    "error_code",
    "error_type",
    "handler",
    "http_status",
    "job_id",
    "job_kind",
    "operation",
    "outcome",
    "status_code",
    "tick_id",
    "update_id",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _REQUEST_ID.get(),
            "entry_point": _ENTRY_POINT.get(),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        for key in _ALLOWED_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=True, separators=(",", ":"))


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if not any(
        getattr(handler, "_telegram_scheduler_handler", False)
        for handler in root.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._telegram_scheduler_handler = True
        root.addHandler(handler)
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_event(level: int, event: str, **fields: Any) -> None:
    safe = {key: value for key, value in fields.items() if key in _ALLOWED_FIELDS}
    logging.getLogger("app").log(level, event, extra={"event": event, **safe})


@contextmanager
def log_context(
    entry_point: str,
    request_id: str | None = None,
    **fields: Any,
) -> Iterator[str]:
    resolved = request_id or f"{entry_point}-{uuid.uuid4().hex[:12]}"
    request_token = _REQUEST_ID.set(resolved)
    entry_token = _ENTRY_POINT.set(entry_point)
    try:
        yield resolved
    finally:
        _REQUEST_ID.reset(request_token)
        _ENTRY_POINT.reset(entry_token)


@contextmanager
def timed_event(
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    except BaseException as exc:
        log_event(
            logging.ERROR,
            event,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            outcome="error",
            **error_fields(exc),
            **fields,
        )
        raise
    else:
        log_event(
            level,
            event,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            outcome="ok",
            **fields,
        )
