"""Журнал синков — основа команды ``forge status`` и проверки ESI-кэша.

Хранит по каждому источнику: время запуска/успеха, до какого момента кэш ESI ещё свеж,
число строк и статус. ``sync`` читает ``expires``, чтобы не дёргать ESI чаще кэша (правило 3).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ..i18n import tr
from .db import transaction


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def mark_running(conn: sqlite3.Connection, source: str) -> None:
    # expires сбрасываем: раз решили обновлять — старое окно кэша не должно прятать
    # незавершённый (в т.ч. умерший) run от is_cache_fresh.
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO sync_state(source, last_run, status, note, expires)
            VALUES (?, ?, 'running', NULL, NULL)
            ON CONFLICT(source) DO UPDATE SET last_run=excluded.last_run,
                                              status='running', note=NULL, expires=NULL
            """,
            (source, _now_iso()),
        )


def mark_success(
    conn: sqlite3.Connection,
    source: str,
    rows: int,
    expires: str | None = None,
    note: str | None = None,
) -> None:
    now = _now_iso()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO sync_state(source, last_run, last_success, expires, rows, status, note)
            VALUES (?, ?, ?, ?, ?, 'ok', ?)
            ON CONFLICT(source) DO UPDATE SET
                last_run=excluded.last_run,
                last_success=excluded.last_success,
                expires=excluded.expires,
                rows=excluded.rows,
                status='ok',
                note=excluded.note
            """,
            (source, now, now, expires, rows, note),
        )


def mark_error(conn: sqlite3.Connection, source: str, note: str) -> None:
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO sync_state(source, last_run, status, note)
            VALUES (?, ?, 'error', ?)
            ON CONFLICT(source) DO UPDATE SET
                last_run=excluded.last_run, status='error', note=excluded.note
            """,
            (source, _now_iso(), note),
        )


def reap_stale(conn: sqlite3.Connection, max_age_minutes: int = 30) -> list[str]:
    """Помечает зависшие 'running'-источники как 'error' (процесс не завершился).

    Жёсткий kill процесса (закрытый терминал, Ctrl-C, сон ПК) минует try/except в
    оркестраторе, поэтому статус навсегда остаётся 'running'. Эта реконсиляция чинит
    застрявшие флаги: source считается мёртвым, если с last_run прошло >= max_age_minutes.
    Синк локально идёт по одному, поэтому на старте нового синка max_age_minutes=0 безопасно
    сбрасывает любой висящий running. Возвращает список затронутых source.
    """
    now = datetime.now(UTC)
    stale: list[str] = []
    rows = conn.execute(
        "SELECT source, last_run FROM sync_state WHERE status = 'running'"
    ).fetchall()
    for row in rows:
        last_run = row["last_run"]
        if last_run is None:
            stale.append(row["source"])
            continue
        try:
            ts = datetime.fromisoformat(last_run)
        except (ValueError, TypeError):
            stale.append(row["source"])
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if (now - ts).total_seconds() >= max_age_minutes * 60:
            stale.append(row["source"])
    if stale:
        with transaction(conn):
            for source in stale:
                conn.execute(
                    "UPDATE sync_state SET status='error', note=? WHERE source=?",
                    (tr("прервано: процесс не завершился"), source),
                )
    return stale


def get(conn: sqlite3.Connection, source: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sync_state WHERE source = ?", (source,)
    ).fetchone()


def all_states(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sync_state ORDER BY source"
    ).fetchall()


def is_cache_fresh(conn: sqlite3.Connection, source: str) -> bool:
    """True, если ESI-кэш по источнику ещё не истёк (можно пропустить синк)."""
    row = get(conn, source)
    if row is None or row["expires"] is None:
        return False
    try:
        expires = datetime.fromisoformat(row["expires"])
    except (ValueError, TypeError):
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return datetime.now(UTC) < expires
