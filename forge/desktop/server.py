"""Встроенный сервер отчётов пульта: FastAPI (``forge.web.app``) в фоновом потоке на 127.0.0.1.

HTML-отчёты по стройкам — самодостаточные страницы для браузера (чек-листы, S-кривая,
план/факт) и сохраняют отметки через ``/api/report-tracking``. Пульт поднимает для них
локальный сервер на свободном порту 8000..8010 и открывает отчёт в браузере по умолчанию.
Слушает только 127.0.0.1 — снаружи не виден. Плюс фоновое автообновление (как ``forge web``).
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

from ..i18n import tr

PORTS = range(8000, 8011)


def _free_port() -> int | None:
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return None


class ReportServer:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.port: int | None = None
        self.error: str | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._scheduler: Any = None

    @property
    def base_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port and self.running else None

    @property
    def running(self) -> bool:
        return bool(self._server is not None and getattr(self._server, "started", False))

    def start(self) -> None:
        try:
            import uvicorn

            from ..sync.scheduler import AutoSyncScheduler
            from ..web import create_app
        except ImportError as e:  # нет extra [web]
            self.error = tr("нет веб-зависимостей ({name}) — pip install -e .[desktop]", name=e.name)
            return
        port = _free_port()
        if port is None:
            self.error = tr("порты 8000–8010 заняты")
            return
        try:
            app = create_app(self.config_path)
        except Exception as e:
            self.error = str(e)
            return
        try:
            # log_config=None: uvicorn НЕ ставит свои консольные хендлеры. Под pythonw (ярлык,
            # Forge.cmd) консоли нет — sys.stdout is None, и его форматтер падал на
            # sys.stdout.isatty() прямо в конструкторе Config (пульт не запускался вовсе).
            # Предупреждения uvicorn уходят в stderr процесса — у пульта это forge_desktop.log.
            config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                    access_log=False, lifespan="off", log_config=None)
            self._server = uvicorn.Server(config)
        except Exception as e:  # сервер отчётов — вспомогательный: пульт работает и без него
            self.error = tr("сервер отчётов не создан: {error}", error=e)
            self._server = None
            return
        self.port = port
        self._thread = threading.Thread(target=self._server.run, name="forge-reports", daemon=True)
        self._thread.start()
        # Автообновление по [autosync] — как в `forge web` (конфиг перечитывается каждую минуту).
        self._scheduler = AutoSyncScheduler(self.config_path)
        self._scheduler.start()

    def wait_ready(self, timeout: float = 3.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.running:
                return True
            time.sleep(0.05)
        return self.running

    def stop(self) -> None:
        if self._scheduler is not None:
            self._scheduler.stop()
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3)

    def url(self, path: str) -> str | None:
        base = self.base_url
        return base + path if base else None
