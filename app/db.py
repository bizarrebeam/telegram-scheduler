import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings
from app.errors import error_fields
from app.observability import log_event

SLOW_CONNECT_MS = 250


def _connect(row_factory=None) -> psycopg.Connection:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    started = time.perf_counter()
    try:
        if row_factory is None:
            conn = psycopg.connect(settings.database_url)
        else:
            conn = psycopg.connect(
                settings.database_url, row_factory=row_factory
            )
    except Exception as exc:
        log_event(
            logging.ERROR,
            "db_connect",
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            outcome="error",
            **error_fields(exc),
        )
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    level = logging.INFO if duration_ms >= SLOW_CONNECT_MS else logging.DEBUG
    log_event(level, "db_connect", duration_ms=duration_ms, outcome="ok")
    return conn


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    with _connect() as conn:
        yield conn


@contextmanager
def get_conn_dict() -> Iterator[psycopg.Connection]:
    with _connect(dict_row) as conn:
        yield conn
