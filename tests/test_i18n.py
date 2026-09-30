"""Язык интерфейса (RUS/ENG): ``forge.i18n`` и полнота английского словаря.

Статическая проверка по исходникам: каждая русская строка (кроме докстрингов и комментариев)
стоит в ``tr(…)``/``N_(…)``/``plural(…)`` — иначе в английском пульте останется русский текст;
у каждого ключа есть английский перевод без кириллицы и с теми же подстановками ``{name}``.
Проверка одного файла: ``pytest tests/test_i18n.py -k settings``.
"""

from __future__ import annotations

import ast
import importlib
import re
from datetime import datetime
from pathlib import Path

import pytest

from forge import i18n

ROOT = Path(__file__).resolve().parents[1]
CYR = re.compile(r"[А-Яа-яЁё]")
TR_FUNCS = {"tr", "N_", "plural"}
PRAGMA = "i18n: ok"  # строка намеренно русская (разбор старых данных и т.п.) — с объяснением рядом
EXEMPT = {
    "forge/desktop/theme.py": "кириллица только в комментариях QSS",
    "forge/storage/schema.py": "комментарии SQL",
    "forge/interface/cli.py": "CLI — инструмент разработчика, остаётся русским",
}
DICT_MODULES = ("en_shell", "en_backend", "en_calculator", "en_pages", "en_stock", "en_settings",
                "en_report")


def _sources() -> list[str]:
    out = []
    for p in sorted((ROOT / "forge").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith("forge/i18n/") or rel in EXEMPT:
            continue
        out.append(rel)
    return out


def _func_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _is_str(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def scan(rel: str) -> tuple[list[str], list[tuple[str, int]]]:
    """(проблемы, ключи словаря с номерами строк) для одного файла."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parents[id(ch)] = node
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and _is_str(body[0].value):
                docs.add(id(body[0].value))
    problems: list[str] = []
    keys: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _func_name(node) in TR_FUNCS:
            name = _func_name(node)
            idx = (0,) if name in ("tr", "N_") else (1, 3)
            for i, a in enumerate(node.args):
                if isinstance(a, ast.JoinedStr):
                    problems.append(f"{rel}:{a.lineno}: {name}(f\"…\") — ключ должен быть постоянным: "
                                    f"{name}(\"… {{x}}\", x=…)")
                elif _is_str(a) and i in idx:
                    keys.append((a.value, a.lineno))
        if _is_str(node) and id(node) not in docs and CYR.search(node.value):
            parent = parents.get(id(node))
            if isinstance(parent, ast.Call) and _func_name(parent) in TR_FUNCS and node in parent.args:
                continue
            span = lines[node.lineno - 1:(node.end_lineno or node.lineno)]
            if any(PRAGMA in ln for ln in span):
                continue
            problems.append(f"{rel}:{node.lineno}: русская строка без tr(): {node.value[:70]!r}")
    return problems, keys


@pytest.mark.parametrize("rel", _sources())
def test_russian_strings_go_through_tr(rel):
    problems, keys = scan(rel)
    missing = sorted({f"{rel}:{ln}: нет перевода: {k[:80]!r}" for k, ln in keys if k not in i18n.EN})
    assert not problems + missing, "\n".join(problems + missing)


def test_translations_are_english_and_keep_placeholders():
    bad = []
    for ru, en in i18n.EN.items():
        if not en.strip():
            bad.append(f"пустой перевод: {ru!r}")
        elif CYR.search(en):
            bad.append(f"кириллица в переводе: {ru!r} → {en!r}")
        elif i18n.fields(ru) != i18n.fields(en):
            bad.append(f"подстановки не совпали: {ru!r} → {en!r}")
    assert not bad, "\n".join(bad)


def test_dictionaries_do_not_conflict():
    seen: dict[str, tuple[str, str]] = {}
    bad = []
    for name in DICT_MODULES:
        for ru, en in importlib.import_module(f"forge.i18n.{name}").EN.items():
            if ru in seen and seen[ru][1] != en:
                bad.append(f"{ru!r}: {seen[ru][0]} → {seen[ru][1]!r}, {name} → {en!r}")
            seen.setdefault(ru, (name, en))
    assert not bad, "\n".join(bad)


def test_tr_plural_and_numbers_follow_language(monkeypatch):
    monkeypatch.setitem(i18n.EN, "день", "day")
    monkeypatch.setitem(i18n.EN, "дней", "days")
    assert i18n.lang() == "ru"
    assert i18n.tr("Обзор") == "Обзор"
    assert i18n.plural(21, "день", "дня", "дней") == "день"
    assert i18n.plural(3, "день", "дня", "дней") == "дня"
    assert i18n.num(1234567) == "1 234 567" and i18n.dec("12.5%") == "12,5%"
    assert i18n.parse_number("1 500,5") == 1500.5
    assert i18n.short_dt(datetime(2026, 9, 29, 14, 5)) == "29.09 14:05"
    assert i18n.tr("нет такого ключа {x}", x=1) == "нет такого ключа 1"

    i18n.set_lang("en")
    assert i18n.tr("Обзор") == "Overview"
    assert i18n.tr("Overview") == "Overview"  # уже переведённое — как есть
    assert i18n.plural(1, "день", "дня", "дней") == "day"
    assert i18n.plural(21, "день", "дня", "дней") == "days"
    assert i18n.num(1234567) == "1,234,567" and i18n.dec("12.5%") == "12.5%"
    assert i18n.parse_number("1,500.5") == 1500.5
    assert i18n.short_dt(datetime(2026, 9, 29, 14, 5)) == "29 Sep 14:05"

    assert i18n.set_lang("EN-us") == "en" and i18n.set_lang("de") == "ru" and i18n.set_lang(None) == "ru"
