"""Тесты автообновления: конфиг [autosync], runner (фоновый синк) и планировщик.

Сети нет — оркестратор и runner.trigger мокаются; БД временная на диске."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from forge import config as config_mod
from forge import storage
from forge.storage import sync_state
from forge.sync import orchestrator, runner, scheduler


def _write_config(tmp_path) -> str:
    """forge.toml во временной папке с db рядом; вернуть путь к конфигу."""
    cfg = config_mod.Config(db_path="forge.db")
    path = tmp_path / "forge.toml"
    config_mod.save(cfg, path)
    return str(path)


def _wait_idle(timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while runner.is_busy() and time.time() < deadline:
        time.sleep(0.02)


# --- конфиг -----------------------------------------------------------------
def test_autosync_defaults_off():
    a = config_mod.AutoSync()
    assert a.enabled is False
    assert a.market_minutes == 30 and a.character_minutes == 60


def test_autosync_roundtrip(tmp_path):
    cfg = config_mod.Config()
    cfg.autosync = config_mod.AutoSync(enabled=True, market_minutes=10, character_minutes=120)
    path = tmp_path / "forge.toml"
    config_mod.save(cfg, path)
    loaded = config_mod.load(path)
    assert loaded.autosync.enabled is True
    assert loaded.autosync.market_minutes == 10
    assert loaded.autosync.character_minutes == 120


# --- runner -----------------------------------------------------------------
def test_runner_trigger_public_runs_both(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(orchestrator, "sync_industry", lambda conn, *a, **k: calls.append("industry"))
    monkeypatch.setattr(orchestrator, "sync_market", lambda conn, cfg, *a, **k: calls.append("market"))

    res = runner.trigger("public", _write_config(tmp_path))
    assert res["started"] is True
    _wait_idle()
    assert calls == ["industry", "market"]
    assert runner.is_busy() is False


def test_runner_rejects_unknown_source(tmp_path):
    res = runner.trigger("nope", _write_config(tmp_path))
    assert res["started"] is False and "error" in res


def test_runner_busy_guard(tmp_path, monkeypatch):
    # Долгий синк держит лок → второй trigger получает busy.
    monkeypatch.setattr(orchestrator, "sync_sde", lambda conn, **k: time.sleep(0.3))
    first = runner.trigger("sde", _write_config(tmp_path))
    assert first["started"] is True
    second = runner.trigger("sde", _write_config(tmp_path))
    assert second == {"started": False, "busy": True}
    _wait_idle()


# --- планировщик ------------------------------------------------------------
def test_scheduler_triggers_only_due_sources(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)
    cfg = config_mod.load(config_path)
    cfg.autosync = config_mod.AutoSync(enabled=True, market_minutes=30, character_minutes=60)
    config_mod.save(cfg, config_path)

    # Подготовить sync_state: рынок только что, персонажи — давно.
    conn = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(conn)
    sync_state.mark_success(conn, "market", 1)  # last_success = сейчас → не пора
    old = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    conn.execute(
        "UPDATE sync_state SET last_success=? WHERE source='character'", (old,)
    )
    # строка character может отсутствовать — вставим вручную
    conn.execute(
        "INSERT OR IGNORE INTO sync_state(source, last_success, status) VALUES('character', ?, 'ok')",
        (old,),
    )
    conn.commit()
    conn.close()

    triggered: list[str] = []
    monkeypatch.setattr(scheduler.runner, "is_busy", lambda: False)
    monkeypatch.setattr(scheduler.runner, "trigger",
                        lambda source, cp: triggered.append(source) or {"started": True})

    sch = scheduler.AutoSyncScheduler(config_path)
    sch._tick()
    assert triggered == ["character"]  # рынок свежий, чары просрочены


def test_scheduler_disabled_does_nothing(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path)  # autosync.enabled = False по умолчанию
    triggered: list[str] = []
    monkeypatch.setattr(scheduler.runner, "is_busy", lambda: False)
    monkeypatch.setattr(scheduler.runner, "trigger", lambda s, cp: triggered.append(s))
    scheduler.AutoSyncScheduler(config_path)._tick()
    assert triggered == []
