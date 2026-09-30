"""Инициализация схемы БД и учёт версии.

Простые SQL-миграции (без alembic). При первом запуске создаётся вся схема из
``schema.py`` и проставляется ``SCHEMA_VERSION``. Идемпотентно: повторный вызов
ничего не ломает (все CREATE — ``IF NOT EXISTS``).
"""

from __future__ import annotations

import sqlite3

from .db import transaction
from .schema import SCHEMA_SQL, SCHEMA_VERSION


def current_version(conn: sqlite3.Connection) -> int:
    """Текущая версия схемы; 0 — если БД ещё не инициализирована."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    ver = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(ver["v"]) if ver and ver["v"] is not None else 0


def init_db(conn: sqlite3.Connection) -> int:
    """Создать схему, если её нет, и вернуть актуальную версию."""
    with transaction(conn):
        conn.executescript(SCHEMA_SQL)
        if current_version(conn) < SCHEMA_VERSION:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
    return current_version(conn)
