from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


def _connect(row_factory=None) -> psycopg.Connection:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    if row_factory is None:
        return psycopg.connect(settings.database_url)
    return psycopg.connect(settings.database_url, row_factory=row_factory)


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    with _connect() as conn:
        yield conn


@contextmanager
def get_conn_dict() -> Iterator[psycopg.Connection]:
    with _connect(dict_row) as conn:
        yield conn
