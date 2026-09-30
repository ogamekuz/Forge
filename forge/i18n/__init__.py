"""Язык интерфейса Forge (RUS/ENG): ``tr()`` переводит русскую строку на текущий язык.

Исходный язык — русский: строки в коде остаются русскими и служат ключами словаря (как msgid
у gettext). Английские переводы — словари ``EN`` в ``forge/i18n/en_*.py`` (по файлу на область
интерфейса), собираются при импорте. Нет перевода — русский текст как есть.

Правила (их проверяет ``tests/test_i18n.py``):

- Всякая русская строка, которую увидит пользователь, идёт через ``tr("…")`` в момент показа.
  Подстановки — именованные: ``tr("Синк: {src}…", src=name)``, НЕ f-строка (ключ должен быть
  постоянным). Литеральные фигурные скобки в таком тексте — ``{{``/``}}``.
- Константы модуля/класса и значения по умолчанию вычисляются ОДИН раз при импорте — там
  ``N_("…")`` (только помечает строку для словаря), а ``tr(константа)`` — где показываем.
  ``tr`` от уже переведённого (или вообще не-ключа) возвращает текст как есть.
- Числительные — ``plural(n, "день", "дня", "дней")``: в английском берутся переводы первой
  («один») и последней («много») форм.

Язык — на весь процесс (один пользователь, один пульт): пульт ставит его при старте из
``[ui] lang`` конфига и при переключении RUS/ENG; сервис и HTML-отчёты читают ``lang()``.
Модуль-лист без зависимостей — импортировать можно из любого слоя.
"""

from __future__ import annotations

import string

LANGS = ("ru", "en")
DEFAULT_LANG = "ru"
LANG_LABELS = {"ru": "RUS", "en": "ENG"}

_lang = DEFAULT_LANG


def _load_en() -> dict[str, str]:
    from . import (
        en_backend,
        en_calculator,
        en_pages,
        en_report,
        en_settings,
        en_shell,
        en_stock,
    )

    merged: dict[str, str] = {}
    for mod in (en_shell, en_backend, en_calculator, en_pages, en_stock, en_settings, en_report):
        merged.update(mod.EN)
    return merged


EN: dict[str, str] = _load_en()


def normalize(code: str | None) -> str:
    """«EN»/«en-US»/мусор → код из ``LANGS`` (неизвестное — русский, язык по умолчанию)."""
    c = (code or "").strip().lower()[:2]
    return c if c in LANGS else DEFAULT_LANG


def set_lang(code: str | None) -> str:
    global _lang
    _lang = normalize(code)
    return _lang


def lang() -> str:
    return _lang


def is_en() -> bool:
    return _lang == "en"


def N_(text: str) -> str:
    """Пометить строку для перевода, не переводя (константы, значения по умолчанию)."""
    return text


def tr(text: str, /, **kw: object) -> str:
    """Перевод ``text`` на текущий язык; ``kw`` — подстановки ``{name}`` (после перевода)."""
    s = EN.get(text, text) if _lang == "en" else text
    return s.format(**kw) if kw else s


def plural(n: float, one: str, few: str, many: str) -> str:
    """Форма слова для числа ``n``: русские правила (1 день, 2 дня, 5 дней); по-английски —
    перевод ``one`` для 1 и перевод ``many`` для остальных."""
    k = abs(int(n))
    if _lang == "en":
        return tr(one) if k == 1 else tr(many)
    if k % 10 == 1 and k % 100 != 11:
        return one
    if 2 <= k % 10 <= 4 and not 12 <= k % 100 <= 14:
        return few
    return many


# --------------------------------------------------------------------------- числа

def group(s: str) -> str:
    """Разряды в строке после ``format(v, ",…")``: по-русски — пробел, по-английски — запятая."""
    return s if _lang == "en" else s.replace(",", " ")


def num(v: float, digits: int = 0) -> str:
    """Число с разрядами: «1 234 567» / «1,234,567» (``digits`` — знаков после точки)."""
    return group(f"{v:,.{digits}f}")


def dec(s: str) -> str:
    """Десятичный разделитель в готовой строке: по-русски — запятая (12,5%), по-английски — точка."""
    return s if _lang == "en" else s.replace(".", ",")


def parse_number(text: str) -> float | None:
    """Разбор числа, введённого человеком: пробелы тысяч убираются; запятая — десятичная
    по-русски («1,5») и разделитель тысяч по-английски («1,500»). Пусто/мусор — None."""
    raw = (text or "").replace(" ", "").replace(" ", "").replace(" ", "").strip()
    raw = raw.replace(",", "") if _lang == "en" else raw.replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def short_dt(d) -> str:
    """Дата-время для таблиц и отчётов: «29.09 14:05» / «29 Sep 14:05» (без локали ОС)."""
    if _lang == "en":
        return f"{d.day:02d} {_MONTHS_EN[d.month - 1]} {d:%H:%M}"
    return f"{d:%d.%m %H:%M}"


def fields(text: str) -> set[str]:
    """Имена подстановок ``{name}`` в строке (для проверки переводов)."""
    try:
        return {f for _lit, f, _spec, _conv in string.Formatter().parse(text) if f}
    except ValueError:
        return set()
