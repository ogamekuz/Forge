"""Общий запуск синка из интерфейса и планировщика — с защитой от параллелизма.

Кнопки на вкладке «Статус» (POST /api/sync/*) и фоновый ``AutoSyncScheduler`` делят один
лок: одновременно идёт максимум один синк (локальный SQLite — один писатель за раз). Каждый
запуск работает в демон-потоке со СВОИМ соединением (создаётся внутри потока, поэтому
``check_same_thread`` по умолчанию не мешает; WAL пускает читающие запросы FastAPI).

Слой ``sync`` — единственный (вместе с ``ingest``), которому разрешена сеть. ``interface``
тоже импортирует ``sync`` (CLI тянет ``sync.orchestrator``), границы не нарушаются.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .. import config as config_mod
from .. import storage
from ..i18n import tr
from ..ingest import character
from . import orchestrator

# Один писатель за раз: и ручной синк, и автосинк берут этот лок.
_lock = threading.Lock()
_state: dict[str, Any] = {"current": None, "started_at": None, "error": None}

# SSO-добавление чара — отдельный лок: интерактивный флоу ждёт браузер до 5 минут и не должен
# блокировать обычный синк рынка.
_auth_lock = threading.Lock()
_auth: dict[str, Any] = {"status": "idle", "name": None, "error": None}

VALID_SOURCES = ("character", "public", "sde")


def _open(config_path: str) -> tuple[config_mod.Config, sqlite3.Connection]:
    """Загрузить конфиг и открыть соединение (db_path — относительно файла конфига)."""
    cfg = config_mod.load(config_path) if Path(config_path).exists() else config_mod.Config()
    db = Path(cfg.db_path)
    if not db.is_absolute():
        db = Path(config_path).resolve().parent / db
    conn = storage.connect(str(db))
    # Подождать освобождения чужой записи, а не падать с "database is locked": auth-поток и
    # синк-поток оба могут писать (в WAL читатели не блокируются, но писатель один).
    conn.execute("PRAGMA busy_timeout = 5000")
    storage.init_db(conn)
    config_mod.resolve_locations(cfg, conn)
    return cfg, conn


def is_busy() -> bool:
    return _lock.locked()


def snapshot() -> dict:
    """Состояние для GET /api/status: что синкается сейчас + статус последнего SSO-входа."""
    return {
        "busy": _lock.locked(),
        "current": _state["current"],
        "started_at": _state["started_at"],
        "error": _state["error"],
        "auth": dict(_auth),
    }


def _run(source: str, config_path: str) -> None:
    cfg, conn = _open(config_path)
    try:
        if source == "character":
            orchestrator.sync_character(conn, cfg)
        elif source == "public":
            orchestrator.sync_industry(conn)
            orchestrator.sync_market(conn, cfg)
        elif source == "sde":
            orchestrator.sync_sde(conn, workdir=str(Path(config_path).resolve().parent))
        _state["error"] = None
    except Exception as exc:  # sync_state уже фиксирует ошибку в БД; дублируем для UI
        _state["error"] = f"{source}: {exc}"
    finally:
        conn.close()
        _state["current"] = None
        _state["started_at"] = None
        _lock.release()


def trigger(source: str, config_path: str = config_mod.DEFAULT_CONFIG_PATH) -> dict:
    """Запустить синк источника в фоне. Если синк уже идёт — ``{started: False, busy: True}``."""
    if source not in VALID_SOURCES:
        return {"started": False, "error": tr("неизвестный источник: {source}", source=source)}
    if not _lock.acquire(blocking=False):
        return {"started": False, "busy": True}
    _state.update(current=source, started_at=time.time(), error=None)
    threading.Thread(target=_run, args=(source, config_path), daemon=True).start()
    return {"started": True, "source": source}


def _run_auth(config_path: str) -> None:
    try:
        cfg, conn = _open(config_path)
        try:
            claims = character.add_character(conn, cfg)
        finally:
            conn.close()
        _auth.update(status="done", name=claims.name, error=None)
    except Exception as exc:
        _auth.update(status="error", error=str(exc))
    finally:
        _auth_lock.release()


def trigger_auth(config_path: str = config_mod.DEFAULT_CONFIG_PATH) -> dict:
    """Запустить EVE SSO-вход в фоне (откроется браузер). Один вход за раз."""
    if not _auth_lock.acquire(blocking=False):
        return {"started": False, "busy": True}
    _auth.update(status="running", name=None, error=None)
    threading.Thread(target=_run_auth, args=(config_path,), daemon=True).start()
    return {"started": True}
