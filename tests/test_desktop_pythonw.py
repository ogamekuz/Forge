"""Пульт под pythonw (ярлык «Forge 3.0», Forge.cmd) — без консоли.

У pythonw ``sys.stdout``/``sys.stderr`` равны ``None``. uvicorn в конструкторе ``Config``
настраивает свой цветной консольный лог и без потоков падает на ``sys.stdout.isatty()`` — пульт
с ярлыка не запустился бы вовсе («Unable to configure formatter 'default'»), хотя из консоли
всё работает.
Сервер поднимается на 127.0.0.1 (без внешней сети); автообновление в тестовом конфиге выключено.
"""

from __future__ import annotations

import sys
import urllib.request

import pytest

from forge import storage

TOML = 'db_path = "forge.db"\n'


@pytest.fixture
def cfg_path(tmp_path):
    p = tmp_path / "forge.toml"
    p.write_text(TOML, encoding="utf-8")
    conn = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(conn)
    conn.close()
    return p


def test_report_server_starts_without_console(cfg_path, monkeypatch):
    pytest.importorskip("uvicorn")
    from forge.desktop.server import ReportServer

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    srv = ReportServer(str(cfg_path))
    try:
        srv.start()
        assert srv.error is None
        assert srv.wait_ready(5.0)
        with urllib.request.urlopen(f"{srv.base_url}/api/health", timeout=5) as r:  # noqa: S310 — 127.0.0.1
            assert r.status == 200
    finally:
        srv.stop()


def test_ensure_std_streams_redirects_stderr_to_log(tmp_path, monkeypatch):
    from forge.desktop.app import ensure_std_streams

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log = tmp_path / "forge_desktop.log"
    ensure_std_streams(log)
    try:
        assert sys.stdout is not None and sys.stderr is not None
        print("предупреждение", file=sys.stderr)
        sys.stderr.flush()
        assert "предупреждение" in log.read_text(encoding="utf-8")
    finally:
        sys.stdout.close()
        sys.stderr.close()
