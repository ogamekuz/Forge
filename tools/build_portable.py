"""Переносная сборка Forge для Windows: embeddable Python + зависимости + код + лаунчер → zip.

    py -3.12 tools\\build_portable.py [--python-embed PATH] [--client-id ID] [--no-zip]

Результат — ``dist/Forge_<версия>/`` и ``dist/Forge_<версия>.zip`` (версия из pyproject.toml,
«3.0.0» → «3.0»). Внутри::

    Forge.cmd            лаунчер: python\\pythonw.exe -m forge.desktop --config forge.toml
    forge.toml           forge.example.toml + client_id приложения EVE SSO для сборки
    python\\              embeddable Python; зависимости — в python\\Lib\\site-packages
    app\\forge\\           код Forge (без тестов); ``..\\app`` прописан в python3XX._pth
    README.txt, README.ru.txt, LICENSE.txt

Embeddable Python: ``--python-embed`` — zip или папка (из папки берётся всё, кроме
Lib\\site-packages); без него — официальный zip с python.org в кэш ``.build/``. Собирать тем же
минорным Python, что и embeddable (py -3.12 для 3.12): pip ставит колёса и маркеры зависимостей
под версию интерпретатора, которым запущен.
"""

from __future__ import annotations

import argparse
import re
import shutil
import struct
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTABLE = ROOT / "packaging" / "portable"
DEFAULT_PY = "3.12.10"  # последний 3.12 с бинарными сборками на python.org
# Приложение EVE SSO автора для переносных сборок. PKCE: client_id публичный, секрета нет.
# Своя сборка — со своим приложением: --client-id (Callback URL http://localhost:8765/callback).
DIST_CLIENT_ID = "361c15660c2a43e98ed55eaa00a1dabd"
# Пульт берёт из Qt только QtCore/QtGui/QtWidgets/QtSvg/QtNetwork (tests/test_desktop*.py). Из
# колеса PySide6-Essentials выкидываем остальное: QML/Quick, Designer, OpenGL (в т.ч. программный
# opengl32sw.dll — виджетам он не нужен), инструменты разработчика, переводы самого Qt, стабы .pyi.
# Проверка — smoke() ниже: пульт поднимается из собранной папки.
QT_DROP_GLOBS = ("*.exe", "*.pyi", "opengl32sw.dll",
                 "Qt6Qml*.dll", "Qt6Quick*.dll", "Qt6Labs*.dll", "Qt6Designer*.dll", "Qt6Help.dll",
                 "Qt6UiTools.dll", "Qt6OpenGL*.dll", "Qt6ShaderTools.dll", "Qt6Sql.dll", "Qt6Test.dll",
                 "Qt6Concurrent.dll", "Qt6Xml.dll", "Qt6PrintSupport.dll",
                 "QtQml*.pyd", "QtQuick*.pyd", "QtDesigner.pyd", "QtHelp.pyd", "QtUiTools.pyd",
                 "QtOpenGL*.pyd", "QtSql.pyd", "QtTest.pyd", "QtConcurrent.pyd", "QtXml.pyd",
                 "QtPrintSupport.pyd")
QT_DROP_DIRS = ("qml", "translations", "examples", "include", "typesystems", "glue", "doc", "scripts",
                "metatypes", "resources", "lib",
                "plugins/qmltooling", "plugins/qmllint", "plugins/designer", "plugins/sqldrivers",
                "plugins/scenegraph", "plugins/qmlls")


def log(msg: str) -> None:
    print(msg, flush=True)


def project_meta() -> tuple[str, list[str]]:
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    deps = list(meta["dependencies"]) + list(meta["optional-dependencies"]["desktop"])
    return meta["version"], sorted(set(deps))


def fetch_embed(py_version: str) -> Path:
    cache = ROOT / ".build"
    cache.mkdir(exist_ok=True)
    name = f"python-{py_version}-embed-amd64.zip"
    dest = cache / name
    if not dest.exists():
        url = f"https://www.python.org/ftp/python/{py_version}/{name}"
        log(f"Скачиваю {url}")
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        tmp.replace(dest)
    return dest


def install_embed(src: Path, py_dir: Path) -> str:
    """Развернуть embeddable Python в ``py_dir``; вернуть тег версии («312»)."""
    if src.is_file():
        with zipfile.ZipFile(src) as zf:
            zf.extractall(py_dir)
    else:
        def ignore(d: str, names: list[str]) -> set[str]:
            skip = {n for n in names if n == "__pycache__"}
            if Path(d).resolve() == (src / "Lib").resolve():
                skip.add("site-packages")
            return skip
        shutil.copytree(src, py_dir, ignore=ignore)
    dlls = [p.name for p in py_dir.glob("python3*.dll") if re.fullmatch(r"python3\d+\.dll", p.name)]
    if len(dlls) != 1 or not (py_dir / "pythonw.exe").exists():
        sys.exit(f"{src}: не похоже на embeddable Python (нет python3XX.dll / pythonw.exe)")
    tag = dlls[0][len("python"):-len(".dll")]
    for pth in py_dir.glob("python3*._pth"):
        pth.unlink()
    (py_dir / f"python{tag}._pth").write_text(
        f"python{tag}.zip\n.\nLib\\site-packages\n..\\app\n\nimport site\n", encoding="ascii")
    return tag


def pip_install(deps: list[str], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    # --no-compile: .pyc в zip бесполезны — zip хранит mtime с точностью 2 с, после распаковки
    # Python всё равно их перекомпилирует (в свою папку, при первом запуске).
    cmd = [sys.executable, "-m", "pip", "install", "--target", str(target), "--only-binary=:all:",
           "--no-compile", "--no-warn-script-location", "--disable-pip-version-check", *deps]
    log("pip install " + " ".join(deps))
    subprocess.run(cmd, check=True)  # noqa: S603 — свой интерпретатор, аргументы из pyproject
    shutil.rmtree(target / "bin", ignore_errors=True)  # exe-обёртки консольных скриптов не нужны
    pyside = target / "PySide6"
    for pattern in QT_DROP_GLOBS:
        for f in pyside.glob(pattern):
            f.unlink()
    for sub in QT_DROP_DIRS:
        shutil.rmtree(pyside / sub, ignore_errors=True)


def write_config(dest: Path, client_id: str, version: str) -> None:
    text = (ROOT / "forge.example.toml").read_text(encoding="utf-8")
    header, sep, body = text.partition("\n\n")
    if not sep:
        sys.exit("forge.example.toml: не нашёл конец шапки")
    header = (f"# Forge {version} — настройки переносной сборки. Почти всё правится в пульте\n"
              "# («Настройки», «Склад», «Персонажи»); пульт переписывает этот файл без комментариев.\n"
              "#\n"
              "# ВАЖНО про TOML: ключи верхнего уровня пишутся ДО первой [секции] — иначе попадут внутрь неё.")
    body, n = re.subn(r'(?m)^client_id = ""$', f'client_id = "{client_id}"', body)
    if n != 1:
        sys.exit('forge.example.toml: ожидалась ровно одна строка client_id = ""')
    dest.write_text(header + sep + body, encoding="utf-8")


def smoke(py_dir: Path) -> None:
    code = ("import PySide6.QtWidgets, PySide6.QtSvg, fastapi, uvicorn, keyring, httpx, pydantic, "
            "typer, tomli_w; import forge.desktop.app, forge.web.app, forge.interface.cli; "
            "print('ok', forge.__file__)")
    out = subprocess.run([str(py_dir / "python.exe"), "-c", code], check=True,  # noqa: S603
                         capture_output=True, text=True)
    log("Проверка импорта: " + out.stdout.strip())


def make_zip(folder: Path) -> Path:
    zpath = folder.parent / f"{folder.name}.zip"  # не with_suffix: «Forge_3.0» → «Forge_3.zip»
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, p.relative_to(folder.parent).as_posix())
    return zpath


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main() -> None:
    ap = argparse.ArgumentParser(description="Переносная сборка Forge для Windows (zip)")
    ap.add_argument("--python-embed", type=Path, help="embeddable Python: zip или папка "
                    "(по умолчанию — скачать с python.org)")
    ap.add_argument("--python-version", default=DEFAULT_PY, help=f"для скачивания (по умолчанию {DEFAULT_PY})")
    ap.add_argument("--client-id", default=DIST_CLIENT_ID, help="Client ID приложения EVE SSO для forge.toml "
                    "('' — пусто, пользователь впишет свой)")
    ap.add_argument("--no-zip", action="store_true", help="только папка, без архива")
    args = ap.parse_args()

    if sys.platform != "win32" or struct.calcsize("P") != 8:
        sys.exit("Собирать на 64-битном Python под Windows")
    version, deps = project_meta()
    short = ".".join(version.split(".")[:2]) if version.endswith(".0") else version
    out = ROOT / "dist" / f"Forge_{short}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    embed = args.python_embed or fetch_embed(args.python_version)
    tag = install_embed(embed, out / "python")
    if tag != f"{sys.version_info.major}{sys.version_info.minor}":
        sys.exit(f"Embeddable Python {tag[0]}.{tag[1:]}, а сборка запущена на "
                 f"{sys.version_info.major}.{sys.version_info.minor}: запусти py -{tag[0]}.{tag[1:]}")
    pip_install(deps, out / "python" / "Lib" / "site-packages")

    shutil.copytree(ROOT / "forge", out / "app" / "forge",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    write_config(out / "forge.toml", args.client_id, short)
    shutil.copy2(PORTABLE / "Forge.cmd", out / "Forge.cmd")
    for name in ("README.txt", "README.ru.txt"):  # имена — ASCII: не все распаковщики чтут UTF-8 в zip
        text = (PORTABLE / name).read_text(encoding="utf-8").replace("{version}", short)
        (out / name).write_text(text, encoding="utf-8-sig")  # BOM — чтобы старый Блокнот не гадал
    shutil.copy2(ROOT / "LICENSE", out / "LICENSE.txt")
    smoke(out / "python")
    for junk in out.rglob("__pycache__"):  # smoke-импорт в app\ насорил — в архив не надо
        if (out / "app") in junk.parents:
            shutil.rmtree(junk)

    log(f"Папка: {out}  ({dir_size(out) / 2**20:.0f} МБ)")
    if not args.no_zip:
        z = make_zip(out)
        log(f"Архив: {z}  ({z.stat().st_size / 2**20:.0f} МБ)")
    if not args.client_id:
        log("ВНИМАНИЕ: client_id пуст — пользователю придётся регистрировать своё приложение EVE SSO")


if __name__ == "__main__":
    main()
