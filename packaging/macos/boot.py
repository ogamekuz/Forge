"""Старт Forge.app: код — в бандле, данные — в ``~/Library/Application Support/Forge``.

Запускается из ``Contents/MacOS/Forge`` (встроенный Python, изолированный режим). При первом
запуске кладёт в папку данных ``forge.toml`` из бандла (client_id приложения EVE SSO сборки) и
открывает пульт с ним: база, SDE, отчёты, лог и состояние окна живут рядом с ``forge.toml``.
Бандл не меняется — его подпись цела, а macOS может запускать его из папки только для чтения
(App Translocation, если программу не перенесли из «Загрузок»). Обновление Forge — заменить
Forge.app; настройки и база остаются.

``Forge --check`` — проверка сборки без окна: импорт пульта и зависимостей, печать версии.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

RES = Path(__file__).resolve().parent
sys.path.insert(0, str(RES / "app"))


def data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Forge"


def check() -> int:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    import keyring  # noqa: F401
    import pydantic  # noqa: F401
    import PySide6.QtNetwork
    import PySide6.QtSvg
    import PySide6.QtWidgets  # noqa: F401
    import tomli_w  # noqa: F401
    import typer  # noqa: F401
    import uvicorn  # noqa: F401

    import forge
    import forge.desktop.app
    import forge.interface.cli
    import forge.web.app

    print("ok", forge.__version__, forge.__file__, "python", sys.version.split()[0])
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--check"]:
        return check()
    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "forge.toml"
    if not cfg.exists():
        shutil.copyfile(RES / "forge.toml", cfg)
    from forge.desktop.app import main as desktop

    # Аргументы запуска (Finder их почти не передаёт) пульту не нужны — только свой конфиг.
    return desktop(["--config", str(cfg)])


sys.exit(main())
