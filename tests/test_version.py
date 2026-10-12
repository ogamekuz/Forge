"""Версия Forge — одна: ``forge.__version__`` совпадает с pyproject.toml (имя архива сборки, шапка пульта)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import forge

ROOT = Path(__file__).resolve().parents[1]


def test_version_matches_pyproject():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert forge.__version__ == meta["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", forge.__version__)


def test_readme_names_the_archive_of_this_version():
    """README зовёт скачать ``Forge_<версия>.zip`` — то же имя, что даёт ``tools/build_portable.py``."""
    v = forge.__version__
    short = ".".join(v.split(".")[:2]) if v.endswith(".0") else v
    for name in ("README.md", "README.ru.md"):
        assert f"Forge_{short}.zip" in (ROOT / name).read_text(encoding="utf-8"), name
