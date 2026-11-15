"""Проверка собранного Forge.app на настоящем macOS (раннер GitHub Actions, ``.github/workflows/macos.yml``).

    python3 tools/macos_check.py dist/Forge_<версия>_macOS/Forge.app --out dist/macos-check

1. Окно пульта встроенным Python на платформе cocoa (Qt grab, без сети за иконками): вкладки
   «Обзор», «Что строить», «Настройки» → ``window_*.png``; какие шрифты тема выбрала на macOS →
   ``fonts.txt``.
2. Запуск как из Finder (``open``): в ~/Library/Application Support/Forge появляется forge.toml,
   процесс жив через 20 с, в forge_desktop.log нет трейсов; снимок экрана (если раннер даст
   screencapture); «Завершить» через AppleScript.
Падает (код 1), если пульт не поднялся или в логе трейс.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

SHOT = r"""
import os, shutil, sys, tempfile, time
from pathlib import Path
res, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(res / "app"))
tmp = Path(tempfile.mkdtemp(prefix="forge-shot-"))
cfg = tmp / "forge.toml"
shutil.copyfile(res / "forge.toml", cfg)
os.chdir(tmp)
from PySide6.QtWidgets import QApplication
from forge.desktop import theme
from forge.desktop.main_window import MainWindow, config_ui
app = QApplication([sys.argv[0]])
app.setApplicationName("Forge")
theme.activate(config_ui(str(cfg))[1])
theme.apply(app)
win = MainWindow(str(cfg), start_server=False)
win.resize(1440, 876)
win.show()
def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
pump(3)
for key in ("dash", "recommend", "settings"):
    win._show_tab(key)
    pump(1.5)
    win.grab().save(str(out / f"window_{key}.png"))
(out / "fonts.txt").write_text(
    f"text={theme.text_family()}\ndisplay={theme.display_family()}\nmono={theme.mono_family()}\n",
    encoding="utf-8")
print("window shots ok", flush=True)
os._exit(0)
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def pids(app: Path) -> list[str]:
    out = subprocess.run(["pgrep", "-f", str(app / "Contents" / "MacOS" / "Forge")],  # noqa: S603, S607
                         capture_output=True, text=True).stdout
    return out.split()


def window_shots(app: Path, out: Path) -> None:
    res = app / "Contents" / "Resources"
    env = {**os.environ, "FORGE_NO_ICON_NET": "1"}
    env.pop("QT_QPA_PLATFORM", None)  # настоящая платформа cocoa, не offscreen
    subprocess.run([str(res / "python" / "bin" / "python3"), "-I", "-c", SHOT, str(res), str(out)],  # noqa: S603
                   check=True, env=env, timeout=180)
    log((out / "fonts.txt").read_text(encoding="utf-8").strip())


def launch(app: Path, out: Path) -> list[str]:
    problems: list[str] = []
    bundle_id = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())["CFBundleIdentifier"]
    data = Path.home() / "Library" / "Application Support" / "Forge"
    subprocess.run(["open", "-n", str(app)], check=True)  # noqa: S603, S607
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not ((data / "forge.toml").exists() and pids(app)):
        time.sleep(1)
    if not (data / "forge.toml").exists():
        problems.append(f"нет {data / 'forge.toml'} через 60 с после запуска")
    time.sleep(20)
    if not pids(app):
        problems.append("процесс Forge не жив через 20 с после старта")
    try:
        subprocess.run(["screencapture", "-x", str(out / "screen.png")], timeout=30, check=True)  # noqa: S603, S607
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"screencapture не вышел: {exc}")
    try:
        subprocess.run(["osascript", "-e", f'tell application id "{bundle_id}" to quit'],  # noqa: S603, S607
                       timeout=30, check=True)
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"AppleScript quit не вышел: {exc}")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and pids(app):
        time.sleep(1)
    left = pids(app)
    if left:
        log("Forge не завершился по quit — kill " + " ".join(left))
        subprocess.run(["kill", *left])  # noqa: S603, S607
    log_file = data / "forge_desktop.log"
    if log_file.exists():
        shutil.copy2(log_file, out / "forge_desktop.log")
        if "Traceback" in log_file.read_text(encoding="utf-8", errors="replace"):
            problems.append("в forge_desktop.log есть трейс")
    if data.is_dir():
        log("Папка данных: " + ", ".join(sorted(p.name for p in data.iterdir())))
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка Forge.app на macOS")
    ap.add_argument("app", type=Path)
    ap.add_argument("--out", type=Path, default=Path("dist/macos-check"))
    args = ap.parse_args()
    out = args.out.resolve()  # снимки пишет процесс, который сам делает chdir — путь абсолютный
    out.mkdir(parents=True, exist_ok=True)
    app = args.app.resolve()
    window_shots(app, out)
    problems = launch(app, out)
    for p in problems:
        log("ОШИБКА: " + p)
    if not problems:
        log("Forge.app запускается и работает")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
