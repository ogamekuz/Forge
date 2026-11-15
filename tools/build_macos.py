"""Сборка Forge для macOS (Apple Silicon): Forge.app со встроенным Python → zip.

    python3 tools/build_macos.py [--client-id ID] [--no-zip]

Только на macOS arm64: нужны clang, lipo, otool, codesign, sips, iconutil, ditto (Command Line
Tools; на раннерах GitHub Actions есть — сборка релиза идёт там, ``.github/workflows/macos.yml``).
Сам скрипт запускается любым Python 3.12+, зависимости ставит pip встроенного Python.

Результат — ``dist/Forge_<версия>_macOS/`` и ``dist/Forge_<версия>_macOS.zip`` (версия из
pyproject.toml, «3.2.0» → «3.2»). Внутри::

    Forge.app/Contents/MacOS/Forge           запускалка (packaging/macos/launcher.c)
    Forge.app/Contents/Resources/boot.py      старт: данные — в ~/Library/Application Support/Forge
    Forge.app/Contents/Resources/forge.toml   шаблон настроек: client_id приложения EVE SSO сборки
    Forge.app/Contents/Resources/python/      Python (python-build-standalone) + зависимости
    Forge.app/Contents/Resources/app/forge/   код Forge (без тестов)
    README.txt, README.ru.txt, LICENSE.txt

Подпись — ad-hoc, без Apple Developer ID: при первом запуске macOS попросит разрешения
(«Системные настройки → Конфиденциальность и безопасность → Всё равно открыть»).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_portable as common  # общие с Windows: project_meta, write_config, client_id

ROOT = common.ROOT
MAC = ROOT / "packaging" / "macos"
# python-build-standalone (Astral): переносимый CPython под macOS. Закреплён вместе с контрольной
# суммой — обновлять тройкой (релиз, версия, sha256 из SHA256SUMS релиза).
PBS_TAG = "20261026"
PBS_PY = "3.12.16"
PBS_SHA256 = "353d5f1b4fd2715e7053f12f068435d5bbad16b91e057006b5d01e0925709a7e"
PBS_NAME = f"cpython-{PBS_PY}+{PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
           f"{PBS_TAG}/{PBS_NAME.replace('+', '%2B')}")
PY_TAG = "python3.12"
ARCH = "arm64"
BUNDLE_ID = "io.github.ogamekuz.forge"
# Пульт берёт из Qt только эти модули (как и Windows-сборка). Остальное из колеса PySide6 —
# QML/Quick, Designer, переводы самого Qt, инструменты, стабы .pyi — выкидываем; фреймворки
# Qt оставляем только те, от которых по otool зависят нужные модули и плагины.
QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtSvg", "QtNetwork")
QT_PLUGIN_DIRS = ("platforms", "imageformats", "iconengines", "styles", "tls", "networkinformation")
QT_DROP_DIRS = ("qml", "translations", "metatypes", "libexec", "include", "typesystems", "glue",
                "scripts", "doc", "examples")
# Из Python: Tcl/Tk, IDLE, тесты, заголовки (нужны только запускалке при сборке), pip.
PY_DROP = ("include", "share", "lib/pkgconfig", "lib/itcl*", "lib/thread*", "lib/tcl*", "lib/tk*",
           "lib/libtcl*", f"lib/{PY_TAG}/idlelib", f"lib/{PY_TAG}/tkinter", f"lib/{PY_TAG}/turtledemo",
           f"lib/{PY_TAG}/turtle.py", f"lib/{PY_TAG}/ensurepip", f"lib/{PY_TAG}/lib2to3",
           f"lib/{PY_TAG}/test", f"lib/{PY_TAG}/config-3.12-darwin", f"lib/{PY_TAG}/lib-dynload/_tkinter*",
           f"lib/{PY_TAG}/site-packages/pip", f"lib/{PY_TAG}/site-packages/pip-*")
PY_KEEP_BIN = ("python3", PY_TAG)

log = common.log


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)  # noqa: S603 — свои инструменты и пути


def fetch_python() -> Path:
    cache = ROOT / ".build"
    cache.mkdir(exist_ok=True)
    dest = cache / PBS_NAME
    if not dest.exists():
        log(f"Скачиваю {PBS_URL}")
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(PBS_URL, timeout=120) as resp, tmp.open("wb") as fh:  # noqa: S310
            shutil.copyfileobj(resp, fh)
        tmp.replace(dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if digest != PBS_SHA256:
        dest.unlink()
        sys.exit(f"{PBS_NAME}: sha256 {digest} ≠ {PBS_SHA256} — архив удалён, запусти ещё раз")
    return dest


def install_python(tarball: Path, res: Path) -> Path:
    with tarfile.open(tarball) as tf:
        tf.extractall(res, filter="tar")  # внутри одна папка python/
    py_home = res / "python"
    if not (py_home / "bin" / PY_TAG).exists() or not (py_home / "lib" / f"lib{PY_TAG}.dylib").exists():
        sys.exit(f"{tarball.name}: нет bin/{PY_TAG} или lib/lib{PY_TAG}.dylib")
    return py_home


def pip_install(py: Path, deps: list[str]) -> None:
    log("pip install " + " ".join(deps))
    run([str(py), "-m", "pip", "install", "--only-binary=:all:", "--no-compile", "--no-warn-script-location",
         "--disable-pip-version-check", "--no-cache-dir", *deps])


def is_macho(p: Path) -> bool:
    if p.is_symlink() or not p.is_file() or p.suffix == ".class":
        return False
    with p.open("rb") as fh:
        head = fh.read(4)
    return head in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")


def macho_deps(p: Path) -> set[str]:
    """Фреймворки Qt, которые грузит бинарник (``@rpath/QtGui.framework/…`` → ``QtGui``)."""
    out = run(["otool", "-L", str(p)], capture_output=True, text=True).stdout
    return set(re.findall(r"@rpath/(Qt\w+)\.framework/", out))


def prune_qt(site: Path) -> None:
    pyside = site / "PySide6"
    qt = pyside / "Qt"
    for base in (pyside, qt):
        for name in QT_DROP_DIRS:
            shutil.rmtree(base / name, ignore_errors=True)
    for app in pyside.glob("*.app"):
        shutil.rmtree(app)
    for f in pyside.rglob("*.pyi"):
        f.unlink()
    for so in pyside.glob("Qt*.abi3.so"):
        if so.name.split(".")[0] not in QT_MODULES:
            so.unlink()
    for lib in pyside.glob("libpysideqml*"):
        lib.unlink()
    for f in pyside.iterdir():  # исполняемые инструменты (designer, lupdate, …) — не модули
        if f.is_file() and not f.suffix and is_macho(f):
            f.unlink()
    plugins = qt / "plugins"
    for d in plugins.iterdir():
        if d.name in QT_PLUGIN_DIRS:
            continue
        if d.is_dir():
            shutil.rmtree(d)
        else:
            d.unlink()

    frameworks = qt / "lib"
    have = {fw.name.removesuffix(".framework") for fw in frameworks.glob("*.framework")}
    # Плагин, которому нужен фреймворк не из Essentials (imageformats/libqpdf → QtPdf из
    # PySide6-Addons), и так не загрузится — выкидываем его, а не тащим лишнее.
    plugin_libs = []
    for p in (p for p in plugins.rglob("*") if is_macho(p)):
        missing = macho_deps(p) - have
        if missing:
            log(f"Плагин {p.relative_to(plugins)} без {', '.join(sorted(missing))} — убран")
            p.unlink()
        else:
            plugin_libs.append(p)
    roots = [*pyside.glob("Qt*.abi3.so"), *pyside.glob("libpyside6*.dylib"),
             *(site / "shiboken6").glob("*.dylib"), *(site / "shiboken6").glob("*.so"), *plugin_libs]
    need: set[str] = set()
    todo = set().union(*(macho_deps(p) for p in roots))
    while todo:
        name = todo.pop()
        if name in need:
            continue
        need.add(name)
        binary = frameworks / f"{name}.framework" / "Versions" / "A" / name
        if not binary.exists():
            sys.exit(f"Нет фреймворка {name}, хотя он нужен: {binary}")
        todo |= macho_deps(binary) - need
    for fw in frameworks.glob("*.framework"):
        if fw.name.removesuffix(".framework") not in need:
            shutil.rmtree(fw)
    log("Фреймворки Qt: " + ", ".join(sorted(need)))


def prune_python(py_home: Path) -> None:
    for pattern in PY_DROP:
        for p in py_home.glob(pattern):
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
    for p in (py_home / "bin").iterdir():
        if p.name not in PY_KEEP_BIN:
            p.unlink()


def thin(root: Path) -> int:
    """Универсальные (x86_64 + arm64) бинарники колёс → только arm64: вдвое меньше. Подпись у
    каждого среза своя, так что после lipo -thin она остаётся действительной."""
    count = 0
    for p in root.rglob("*"):
        if not is_macho(p):
            continue
        with p.open("rb") as fh:
            fat = fh.read(4) in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")
        if not fat:
            continue
        archs = run(["lipo", "-archs", str(p)], capture_output=True, text=True).stdout.split()
        if ARCH not in archs:
            sys.exit(f"{p}: нет среза {ARCH} ({archs})")
        tmp = p.with_name(p.name + ".thin")
        run(["lipo", str(p), "-thin", ARCH, "-output", str(tmp)])
        shutil.copymode(p, tmp)
        tmp.replace(p)
        count += 1
    return count


def min_macos(site: Path) -> str:
    """Минимальная macOS — самая новая из требований колёс (тег ``macosx_13_0_…``) и Python (11.0)."""
    best = (11, 0)
    for wheel in site.glob("*.dist-info/WHEEL"):
        for m in re.finditer(r"macosx_(\d+)_(\d+)_", wheel.read_text(encoding="utf-8")):
            best = max(best, (int(m[1]), int(m[2])))
    return f"{best[0]}.{best[1]}"


def build_launcher(py_home: Path, dest: Path, minos: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["clang", "-arch", ARCH, f"-mmacosx-version-min={minos}", "-O2", "-Wall", "-Wextra",
         "-isystem", str(py_home / "include" / PY_TAG), str(MAC / "launcher.c"),
         f"-L{py_home / 'lib'}", f"-l{PY_TAG}", "-Wl,-rpath,@executable_path/../Resources/python/lib",
         "-o", str(dest)])


def build_icon(dest: Path) -> None:
    src = ROOT / "forge" / "desktop" / "assets" / "forge.png"  # 256×256
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "forge.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256):
            for scale in (1, 2):
                px = size * scale
                if px > 256:
                    continue
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                run(["sips", "-z", str(px), str(px), str(src), "--out", str(iconset / name)],
                    stdout=subprocess.DEVNULL)
        run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)])


def write_plist(dest: Path, version: str, minos: str) -> None:
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleLocalizations": ["en", "ru"],  # системные диалоги — на языке macOS
        "CFBundleDisplayName": "Forge",
        "CFBundleName": "Forge",
        "CFBundleExecutable": "Forge",
        "CFBundleIconFile": "forge",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": minos,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSAppSleepDisabled": True,  # автосинк по таймерам не должен засыпать в фоне (App Nap)
        "NSHumanReadableCopyright": "Copyright © 2026 Kalaratri Omanid. MIT License.",
    }
    with dest.open("wb") as fh:
        plistlib.dump(info, fh)


def compile_pyc(py: Path, *dirs: Path) -> None:
    """.pyc заранее: в бандл при запуске не пишется ничего (подпись цела, папка может быть только
    для чтения). ``unchecked-hash`` — без сверки с mtime исходника: после распаковки zip mtime
    может сдвинуться, а перекомпиляция в памяти на каждом старте — лишние секунды."""
    res = subprocess.run([str(py), "-m", "compileall", "-q", "-f", "-j", "0",  # noqa: S603
                          "--invalidation-mode", "unchecked-hash", *map(str, dirs)])
    if res.returncode:
        log(f"compileall: код {res.returncode} — часть файлов не скомпилировалась (см. выше)")


def sign(app: Path) -> None:
    run(["xattr", "-cr", str(app)])  # codesign не принимает «Finder info»/resource forks
    run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(app)])
    run(["codesign", "--verify", "--strict", "--verbose=2", str(app)])


def smoke(app: Path) -> None:
    env = {"HOME": tempfile.mkdtemp(prefix="forge-home-"), "PATH": "/usr/bin:/bin"}
    out = run([str(app / "Contents" / "MacOS" / "Forge"), "--check"], capture_output=True, text=True, env=env)
    if not out.stdout.startswith("ok "):
        sys.exit(f"Forge --check: {out.stdout}\n{out.stderr}")
    log("Проверка запускалки: " + out.stdout.strip())


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def main() -> None:
    ap = argparse.ArgumentParser(description="Сборка Forge для macOS (Forge.app в zip)")
    ap.add_argument("--client-id", default=common.DIST_CLIENT_ID, help="Client ID приложения EVE SSO "
                    "для forge.toml ('' — пусто, пользователь впишет свой)")
    ap.add_argument("--no-zip", action="store_true", help="только папка, без архива")
    args = ap.parse_args()

    if sys.platform != "darwin" or platform.machine() != ARCH:
        sys.exit("Собирать на macOS с Apple Silicon (arm64)")
    version, deps = common.project_meta()
    short = ".".join(version.split(".")[:2]) if version.endswith(".0") else version
    out = ROOT / "dist" / f"Forge_{short}_macOS"
    if out.exists():
        shutil.rmtree(out)
    app = out / "Forge.app"
    contents = app / "Contents"
    res = contents / "Resources"
    res.mkdir(parents=True)

    py_home = install_python(fetch_python(), res)
    py = py_home / "bin" / PY_TAG
    pip_install(py, deps)
    site = py_home / "lib" / PY_TAG / "site-packages"
    minos = min_macos(site)
    build_launcher(py_home, contents / "MacOS" / "Forge", minos)
    prune_qt(site)
    prune_python(py_home)
    log(f"Универсальных бинарников урезано до {ARCH}: {thin(res)}")

    shutil.copytree(ROOT / "forge", res / "app" / "forge", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(MAC / "boot.py", res / "boot.py")
    common.write_config(res / "forge.toml", args.client_id, short)
    build_icon(res / "forge.icns")
    write_plist(contents / "Info.plist", version, minos)
    compile_pyc(py, py_home / "lib" / PY_TAG, res / "app")
    for name in ("README.txt", "README.ru.txt"):
        text = (MAC / name).read_text(encoding="utf-8").replace("{version}", short).replace("{macos}", minos)
        (out / name).write_text(text, encoding="utf-8")
    shutil.copy2(ROOT / "LICENSE", out / "LICENSE.txt")
    sign(app)
    smoke(app)

    log(f"Папка: {out}  ({dir_size(out) / 2**20:.0f} МБ, macOS {minos}+)")
    if not args.no_zip:
        z = out.parent / f"{out.name}.zip"
        z.unlink(missing_ok=True)
        run(["ditto", "-c", "-k", "--keepParent", str(out), str(z)])
        log(f"Архив: {z}  ({z.stat().st_size / 2**20:.0f} МБ)")
    if not args.client_id:
        log("ВНИМАНИЕ: client_id пуст — пользователю придётся регистрировать своё приложение EVE SSO")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"folder={out}\napp={app}\nzip={out.parent / (out.name + '.zip')}\n")


if __name__ == "__main__":
    main()
