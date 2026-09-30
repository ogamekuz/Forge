"""Точка входа пульта Forge на PySide6: ``pythonw -m forge.desktop`` / ``forge-desktop`` /
``forge desktop`` / ярлык «Forge 3.0».

Под pythonw консоли нет — необработанные ошибки пишем в ``forge_desktop.log`` рядом с
forge.toml и показываем окно с текстом ошибки.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG_NAME = "forge_desktop.log"


def _default_config() -> str:
    """forge.toml: из аргумента/переменной FORGE_CONFIG, иначе — в текущей папке, иначе —
    в корне проекта (две папки выше пакета)."""
    env = os.environ.get("FORGE_CONFIG")
    if env:
        return env
    cwd = Path.cwd() / "forge.toml"
    if cwd.exists():
        return str(cwd)
    return str(Path(__file__).resolve().parents[2] / "forge.toml")


def ensure_std_streams(log_path: Path) -> None:
    """Под pythonw (ярлык, Forge.cmd) у процесса нет консоли: ``sys.stdout``/``sys.stderr`` —
    ``None``. Библиотеки, которые на них смотрят (uvicorn проверяет ``isatty()`` для цветного
    лога, ``logging.lastResort`` пишет в stderr), падают или теряют сообщения. Даём настоящие
    потоки: stderr — в лог пульта (предупреждения и трейсы не пропадают), stdout — в никуда."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # живёт весь процесс
    if sys.stderr is None:
        try:
            sys.stderr = open(log_path, "a", encoding="utf-8", buffering=1)
        except OSError:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    # Справка командной строки — до чтения конфига (язык ещё не известен), для разработчика.
    parser = argparse.ArgumentParser(prog="forge-desktop", description="Пульт Forge (PySide6)")  # i18n: ok
    parser.add_argument("-c", "--config", default=None, help="путь к forge.toml")  # i18n: ok
    parser.add_argument("--no-server", action="store_true", help="не поднимать сервер отчётов")  # i18n: ok
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    config_path = str(Path(args.config or _default_config()).resolve())
    log_path = Path(config_path).parent / LOG_NAME
    os.chdir(Path(config_path).parent)  # относительные пути (db_path, reports/) — от конфига
    console = sys.__stderr__ is not None  # запущен из консоли (python), а не pythonw
    ensure_std_streams(log_path)

    def log_exception(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")
        except OSError:
            pass
        if console:  # под pythonw stderr — тот же лог: второй раз трейс не пишем
            sys.__excepthook__(exc_type, exc, tb)

    if sys.platform == "win32":
        try:  # своя иконка на панели задач, а не иконка pythonw
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Forge.Desktop.3")
        except Exception:  # noqa: S110 — не Windows 7+/нет shell32: просто иконка pythonw
            pass
    sys.excepthook = log_exception

    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..i18n import tr
    from . import theme
    from .main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Forge")
    app.setOrganizationName("Forge")
    theme.apply(app)
    QApplication.setWindowIcon(theme.app_icon())
    try:
        win = MainWindow(config_path, start_server=not args.no_server)
    except Exception:
        text = traceback.format_exc()
        log_exception(*sys.exc_info())
        QMessageBox.critical(None, "Forge", tr("Пульт не запустился:\n\n{error}\n\nЛог: {log}",
                                               error=text[-1500:], log=log_path))
        return 1
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
