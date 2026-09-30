"""Подключение к локальной БД (SQLite) — единственная точка низкоуровневого доступа.

Код намеренно избегает SQLite-специфики в публичном API репозиториев, чтобы позже
слой можно было перенести на Postgres. SQLite-настройки (PRAGMA, типы) живут только здесь
и в ``schema.py``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Открыть соединение с БД и применить базовые PRAGMA.

    ``:memory:`` поддерживается для тестов.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # FK-связи в schema.sql документируют отношения и нужны для Postgres, но в SQLite
    # их НЕ форсируем: источники (SDE / market / industry) синкаются независимо и в
    # произвольном порядке — строгая проверка FK уронила бы, например, синк рынка до
    # загрузки SDE. Для локального ingest-кэша целостность обеспечивается порядком sync_all.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Транзакция: commit при успехе, rollback при исключении."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
