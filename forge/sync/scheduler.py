"""Фоновый планировщик автообновления (поднимается командой ``forge web``).

Раз в минуту смотрит ``cfg.autosync``: если включено и по источнику истёк интервал
(``now − last_success ≥ N`` минут) — просит :mod:`runner` запустить синк. Сам синк всё равно
уважает ESI-кэш оркестратора (пропустит, пока кэш свеж) и общий лок раннера (не запустит
второй параллельный синк). Конфиг перечитывается КАЖДЫЙ тик — переключатель в UI действует сразу.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .. import config as config_mod
from .. import storage
from ..storage import sync_state
from . import runner

TICK_SECONDS = 60


def _minutes_since_success(conn: sqlite3.Connection, source: str) -> float | None:
    """Сколько минут прошло с последнего успешного синка источника (None — ни разу)."""
    row = sync_state.get(conn, source)
    if row is None or row["last_success"] is None:
        return None
    try:
        ts = datetime.fromisoformat(row["last_success"])
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds() / 60.0


class AutoSyncScheduler(threading.Thread):
    """Демон-поток: тикает раз в минуту и триггерит просроченные источники."""

    def __init__(self, config_path: str = config_mod.DEFAULT_CONFIG_PATH) -> None:
        super().__init__(daemon=True, name="forge-autosync")
        self.config_path = config_path
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _open(self) -> tuple[config_mod.Config, sqlite3.Connection]:
        cfg = (
            config_mod.load(self.config_path)
            if Path(self.config_path).exists()
            else config_mod.Config()
        )
        db = Path(cfg.db_path)
        if not db.is_absolute():
            db = Path(self.config_path).resolve().parent / db
        return cfg, storage.connect(str(db))

    def _tick(self) -> None:
        try:
            cfg, conn = self._open()
        except Exception:
            return  # битый конфиг/БД — переживём до следующего тика
        try:
            auto = cfg.autosync
            if not auto.enabled or runner.is_busy():
                return
            # «market» по интервалу market_minutes → public (рынок Jita + индустрия-индексы).
            due: list[str] = []
            m = _minutes_since_success(conn, "market")
            if auto.market_minutes > 0 and (m is None or m >= auto.market_minutes):
                due.append("public")
            c = _minutes_since_success(conn, "character")
            if auto.character_minutes > 0 and (c is None or c >= auto.character_minutes):
                due.append("character")
            # Один синк за тик (лок всё равно не пустит второй); остальное подхватит след. тик.
            for source in due:
                if runner.trigger(source, self.config_path).get("started"):
                    break
        finally:
            conn.close()

    def run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(TICK_SECONDS)
