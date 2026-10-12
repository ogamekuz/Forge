"""Вид пульта Forge из активной темы: палитра, шрифты, stylesheet, иконки и логотип.

Платформа — Qt-шелл, стеклянные панели, сегменты-вкладки, пульсирующие статусы. Тем десять
(``themes.py``); по умолчанию — Forge в цветах EVE Online: глубокий космос (#04070b → #0b1118),
неоновый cyan интерфейса Photon (#3fb8e0), золото Omega/ISK (#e8a93a), зелень прибыли, красный
убытков, пурпур науки, ночное небо со звёздами.

Тема — одна на процесс: ``activate()`` ставит её и переставляет токены модуля (``TEXT``,
``CYAN``…), ``apply()`` красит приложение. Менять можно на ходу — окно после этого пересобирает
шапку и страницы (``MainWindow.set_theme``), поэтому цвета берутся при постройке виджета:
``theme.TEXT`` в теле функции, а не в значении по умолчанию и не в константе класса.
``spec`` у функций — тема вместо активной: так окно «Тема» рисует предпросмотр любой из них.

Только представление.
"""

from __future__ import annotations

import ctypes
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QStandardPaths, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer

from . import themes
from .themes import THEMES, Rgb, Theme

# ---------------------------------------------------------------------------
# Активная тема и её токены
# ---------------------------------------------------------------------------

_active: Theme = THEMES[themes.DEFAULT_THEME]

BG0: str            # середина градиента фона окна
BG1: str            # тело окон и диалогов
BG_DEEP: str
POPUP: str
TEXT: str
MUTED: str
FAINT: str

CYAN: str           # акцент (у Forge — Photon UI)
GOLD: str           # ISK и предупреждения (у Forge — Omega)
GREEN: str
RED: str
VIOLET: str

# тон -> (r, g, b базового цвета, цвет текста/иконки этого тона на панели)
TONES: Mapping[str, tuple[Rgb, str]]
COOL: Rgb           # вуаль: наведение, чередование строк, линии сетки
LINE: str
LINE_STRONG: str
SERIES: tuple[str, ...]   # цвета рядов (персонажи на диаграмме Ганта)
WHITE: Rgb = (255, 255, 255)


def active() -> Theme:
    """Тема, с которой работает процесс."""
    return _active


def activate(theme_id: str | None) -> Theme:
    """Сделать тему активной (неизвестное имя — тема по умолчанию) и переставить токены модуля.
    Приложение красит ``apply()``; уже построенные виджеты остаются в прежних цветах."""
    global _active, BG0, BG1, BG_DEEP, POPUP, TEXT, MUTED, FAINT, CYAN, GOLD, GREEN, RED, VIOLET
    global TONES, COOL, LINE, LINE_STRONG, SERIES
    t = themes.get(theme_id)
    _active = t
    BG_DEEP, BG1, BG0 = t.deep, t.window, t.backdrop[1]
    POPUP, TEXT, MUTED, FAINT = t.popup, t.text, t.muted, t.faint
    CYAN, GOLD, GREEN = t.accent, t.tone_color("gold"), t.tone_color("green")
    RED, VIOLET = t.tone_color("red"), t.tone_color("violet")
    TONES, COOL, LINE, LINE_STRONG, SERIES = t.tones, t.wash, t.line, t.line_strong, t.series
    return t


activate(themes.DEFAULT_THEME)

# Цвет по уровню безопасности системы — как на звёздной карте EVE.
_SEC_COLORS = [
    (1.0, "#2fefef"), (0.9, "#48f0c0"), (0.8, "#00ef47"), (0.7, "#00f000"), (0.6, "#8fef2f"),
    (0.5, "#efef00"), (0.4, "#d77700"), (0.3, "#f06000"), (0.2, "#f04800"), (0.1, "#d73000"),
]


def security_color(sec: float | None) -> str:
    if sec is None:
        return FAINT
    s = round(sec, 1)
    for threshold, color in _SEC_COLORS:
        if s >= threshold:
            return color
    return "#f00000"


def tone_rgb(tone, spec=None):
    return (spec or _active).tone_rgb(tone)


def tone_text(tone, spec=None):
    return (spec or _active).tone_text(tone)


def rgba(tone_or_rgb, alpha, spec=None):
    """'rgba(r,g,b,A)' для QSS; alpha — доля 0..1."""
    return themes.rgba(tone_rgb(tone_or_rgb, spec) if isinstance(tone_or_rgb, str) else tone_or_rgb, alpha)


def qcolor(tone_or_rgb, alpha=1.0, spec=None):
    r, g, b = tone_rgb(tone_or_rgb, spec) if isinstance(tone_or_rgb, str) else tone_or_rgb
    return QColor(r, g, b, round(alpha * 255))


def brush_color(value: str) -> QColor:
    """``QColor`` цвета темы для своей отрисовки: ``#rrggbb``, имя, ``transparent`` или
    ``rgba(r,g,b,A)`` из ``rgba()`` (``A`` 0..255 — сам ``QColor`` такое не разбирает). У
    градиента QSS берётся первый стоп."""
    value = value.strip()
    if value.startswith("qlineargradient"):
        first = re.search(r"stop:0\s+(rgba\([^)]*\)|#[0-9a-fA-F]+|\w+)", value)
        value = first.group(1) if first else "transparent"
    if value.startswith("rgba("):
        r, g, b, a = (int(part) for part in value[5:-1].split(","))
        return QColor(r, g, b, a)
    return QColor(value)


# ---------------------------------------------------------------------------
# Шрифты
# ---------------------------------------------------------------------------

_resolved: dict[tuple[str, ...], str] = {}


def _pick(candidates):
    key = tuple(candidates)
    if key not in _resolved:
        have = set(QFontDatabase.families())
        _resolved[key] = next((f for f in candidates if f in have), candidates[-1])
    return _resolved[key]


def text_family(spec=None):
    return _pick((spec or _active).text_fonts)


def display_family(spec=None):
    return _pick((spec or _active).display_fonts)


def mono_family(spec=None):
    return _pick((spec or _active).mono_fonts)


def font(px, weight=400, display=False, mono=False, spacing=None, num=False, spec=None):
    """``num`` — табличные цифры (tnum): колонки ISK не «пляшут» по ширине."""
    t = spec or _active
    f = QFont(_pick(t.mono_fonts if mono else t.display_fonts if display else t.text_fonts))
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    if spacing is not None:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, spacing)
    if num:
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
        except (AttributeError, TypeError):  # Qt < 6.6 — без табличных цифр
            pass
    return f


# ---------------------------------------------------------------------------
# Иконки (stroke 1.7, viewBox 24 — тот же стиль, что у пульта-образца)
# ---------------------------------------------------------------------------

_ICONS = {
    "activity": '<path d="M3 12h3.8L9.5 5.5l4.5 13L17 12h4"/>',
    "zap": '<path d="M13.2 2.5 4.5 14h5.8l-1.5 7.5L17.5 9.5h-5.8l1.5-7Z"/>',
    "gauge": '<path d="M5.2 18.6a8.6 8.6 0 1 1 13.6 0"/><path d="M12 13.8l3.4-3.4"/>'
             '<circle cx="12" cy="13.8" r="1.2" fill="currentColor" stroke="none"/>',
    "clock": '<circle cx="12" cy="12" r="8.6"/><path d="M12 7.2v4.8l3.4 2"/>',
    "sync": '<path d="M4.2 12a7.8 7.8 0 0 1 13.3-5.5M19.8 12a7.8 7.8 0 0 1-13.3 5.5"/>'
            '<path d="M17.6 3.4v3.4h-3.4M6.4 20.6v-3.4h3.4"/>',
    "server": '<rect x="3.5" y="4" width="17" height="7" rx="1.6"/>'
              '<rect x="3.5" y="13" width="17" height="7" rx="1.6"/>'
              '<path d="M7 7.5h.01M10 7.5h.01M7 16.5h.01M10 16.5h.01"/>',
    "check": '<path d="m5 12.6 4.4 4.4L19 7.4"/>',
    "x": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "minus": '<path d="M5.5 12h13"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "spark": '<path d="M12 3.2c.8 4.3 2 6.2 2.9 7.1.9.9 2.8 2 5.9 1.5-4.3.8-6.2 2-7.1 2.9-.9.9-2 '
             '2.8-1.5 5.9-.8-4.3-2-6.2-2.9-7.1-.9-.9-2.8-2-5.9-1.5 4.3-.8 6.2-2 7.1-2.9.9-.9 '
             '2-2.8 1.5-5.9Z"/>',
    "calendar": '<rect x="4" y="6" width="16" height="14.4" rx="2"/>'
                '<path d="M4 10.4h16M8.5 3.6V7M15.5 3.6V7"/>',
    "target": '<circle cx="12" cy="12" r="8.6"/><circle cx="12" cy="12" r="4.4"/>'
              '<circle cx="12" cy="12" r="0.8" fill="currentColor" stroke="none"/>',
    "chevron": '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>',
    "chevron-right": '<path d="m9.5 6.5 5.5 5.5-5.5 5.5"/>',
    "arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "play": '<path d="M7.5 5.2v13.6a.8.8 0 0 0 1.2.7l10.8-6.8a.8.8 0 0 0 0-1.4L8.7 4.5a.8.8 0 0 0-1.2.7Z"/>',
    "power": '<path d="M12 3.2v8.3"/><path d="M17.8 6.9a8.2 8.2 0 1 1-11.6 0"/>',
    "sliders": '<path d="M4 6.5h9M17 6.5h3M4 12h3M11 12h9M4 17.5h11M19 17.5h1"/>'
               '<circle cx="15" cy="6.5" r="2"/><circle cx="9" cy="12" r="2"/>'
               '<circle cx="17" cy="17.5" r="2"/>',
    "search": '<circle cx="11" cy="11" r="6.8"/><path d="m20 20-4.2-4.2"/>',
    "copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.2"/>'
            '<path d="M15.5 5.6V5a1.8 1.8 0 0 0-1.8-1.8H5A1.8 1.8 0 0 0 3.2 5v8.7A1.8 1.8 0 0 0 5 15.5h.6"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M5.5 7l.9 11.2A2 2 0 0 0 8.4 20h7.2a2 2 0 0 0 '
             '2-1.8L18.5 7M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
    "repeat": '<path d="M3.8 12a8.2 8.2 0 1 0 2.4-5.8"/><path d="M3.8 3.8v4.6h4.6"/>',
    "shield": '<path d="M12 3.2 5 6v5.6c0 4.3 3 7.6 7 9.2 4-1.6 7-4.9 7-9.2V6l-7-2.8Z"/>'
              '<path d="m9 12.2 2.2 2.2 3.8-4"/>',
    "folder": '<path d="M3.5 7.2V17a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2V9.4a2 2 0 0 0-2-2h-6.3L10.4 '
              '5H5.5a2 2 0 0 0-2 2.2Z"/>',
    "database": '<ellipse cx="12" cy="5.8" rx="7.5" ry="2.8"/><path d="M4.5 5.8v12.4c0 1.5 3.4 2.8 '
                '7.5 2.8s7.5-1.3 7.5-2.8V5.8"/><path d="M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>',
    "lock": '<rect x="5" y="11" width="14" height="9.5" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "info": '<circle cx="12" cy="12" r="8.6"/><path d="M12 11v5.2M12 7.8h.01"/>',
    "alert": '<path d="M10.3 4.3 2.9 17.2A2 2 0 0 0 4.6 20h14.8a2 2 0 0 0 1.7-2.8L13.7 4.3a2 2 0 0 '
             '0-3.4 0Z"/><path d="M12 9.5v4M12 16.8h.01"/>',
    "user": '<circle cx="12" cy="8.2" r="3.8"/><path d="M4.8 20a7.2 7.2 0 0 1 14.4 0"/>',
    "users": '<circle cx="9" cy="8.5" r="3.4"/><path d="M2.8 19.5a6.2 6.2 0 0 1 12.4 0"/>'
             '<path d="M15.6 5.4a3.3 3.3 0 0 1 0 6.3M17.4 13.6a6.2 6.2 0 0 1 3.8 5.9"/>',
    "link": '<path d="M10 14a4.2 4.2 0 0 0 6 0l3-3a4.2 4.2 0 0 0-6-6l-1 1"/>'
            '<path d="M14 10a4.2 4.2 0 0 0-6 0l-3 3a4.2 4.2 0 0 0 6 6l1-1"/>',
    "external": '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 13.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4.5"/>',
    "heart": '<path d="M12 19.5s-7.2-4.4-7.2-9.6A4.1 4.1 0 0 1 12 7.6a4.1 4.1 0 0 1 7.2 2.3c0 5.2-7.2 9.6-7.2 9.6Z"/>',
    # — индустрия EVE —
    "hammer": '<path d="m14.5 6.5 3-3 3 3-3 3"/><path d="m16 8-9.8 9.8a1.8 1.8 0 0 1-2.5 0 1.8 1.8 0 0 1 0-2.5L13.5 5.5"/>'
              '<path d="m12 4 2.5-.5 1 1"/>',
    "anvil": '<path d="M3.5 7.5h11.5c0 2.6 1.8 4 5.5 4v1.2c-2.4 0-3.8.8-4.3 2.3H9.2c-.6-1.8-2.2-2.8-4.7-3.1Z"/>'
             '<path d="M8.5 15v3M15.5 15v3M6 20.5h12"/>',
    "calc": '<rect x="5" y="3" width="14" height="18" rx="2.2"/><path d="M8.5 7h7"/>'
            '<path d="M8.5 11h.01M12 11h.01M15.5 11h.01M8.5 14.5h.01M12 14.5h.01M15.5 14.5h.01M8.5 18h.01M12 18h.01M15.5 18h.01"/>',
    "box": '<path d="M12 2.8 20 7v10l-8 4.2L4 17V7Z"/><path d="M4 7l8 4.2L20 7M12 11.2V21"/>',
    "boxes": '<rect x="3.5" y="12.5" width="8" height="7.5" rx="1"/><rect x="12.5" y="12.5" width="8" height="7.5" rx="1"/>'
             '<rect x="8" y="4" width="8" height="7.5" rx="1"/>',
    "pin": '<path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11Z"/><circle cx="12" cy="10" r="2.4"/>',
    "planet": '<circle cx="12" cy="12" r="6"/><path d="M3.5 15.5c-1.6 2.3-.9 3.4 1.6 3.2 3-.3 7.7-2.3 11.4-5.2 3.7-2.9 5.3-5.6 4.1-6.7-.8-.7-2.6-.5-4.8.4"/>',
    "ship": '<path d="M12 2.5 15 9l5.5 3.5L15 15l-3 6.5L9 15l-5.5-2.5L9 9Z"/><circle cx="12" cy="12" r="1.6"/>',
    "flask": '<path d="M9.5 3.5h5M10.5 3.5v5.2L5.2 18.2A1.9 1.9 0 0 0 6.9 21h10.2a1.9 1.9 0 0 0 1.7-2.8L13.5 8.7V3.5"/>'
             '<path d="M7.8 14.5h8.4"/>',
    "coins": '<ellipse cx="9" cy="7" rx="5.5" ry="2.6"/><path d="M3.5 7v4c0 1.4 2.5 2.6 5.5 2.6s5.5-1.2 5.5-2.6V7"/>'
             '<path d="M9.5 13.6v3.4c0 1.4 2.5 2.6 5.5 2.6s5.5-1.2 5.5-2.6v-4c0-1.4-2.5-2.6-5.5-2.6-.6 0-1.2 0-1.7.1"/>',
    "cart": '<path d="M3 4h2.6l2.2 11.2a1.6 1.6 0 0 0 1.6 1.3h8.1a1.6 1.6 0 0 0 1.6-1.2L21 8H6.3"/>'
            '<circle cx="10" cy="20" r="1.3"/><circle cx="17" cy="20" r="1.3"/>',
    "gantt": '<path d="M4 4v16h16"/><path d="M7.5 8h6M10 12h7.5M8 16h5"/>',
    "clipboard": '<rect x="5" y="4.5" width="14" height="16.5" rx="2"/><path d="M9 4.5V3.5h6v1"/>'
                 '<path d="m8.8 13 2.2 2.2 4.2-4.4"/>',
    "list": '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
    "filter": '<path d="M4 5h16l-6.2 7.3v6.2l-3.6 1.8v-8Z"/>',
    "ban": '<circle cx="12" cy="12" r="8.6"/><path d="m6 6 12 12"/>',
    "recycle": '<path d="M7.5 9.5 10 5.3a2.3 2.3 0 0 1 4 0l1.6 2.7"/><path d="m17.5 11.5 2.2 3.8a2.3 2.3 0 0 1-2 3.5H14"/>'
               '<path d="M9.5 18.8H6.3a2.3 2.3 0 0 1-2-3.5L6.4 12"/><path d="M13.4 5.6 15.6 8 12.7 8.6M16.6 20.4 14 18.8l2-2.4M4.2 13.9 6.4 12l.9 3"/>',
    "star": '<path d="m12 3.5 2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.8-5.2 2.8 1-5.8-4.2-4.1 5.8-.8Z"/>',
    "route": '<circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/>'
             '<path d="M8.2 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.8"/>',
    "tag": '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1.4 1.4 0 0 1 0 2l-6.7 6.7a1.4 1.4 0 0 1-2 0Z"/>'
           '<circle cx="8" cy="8" r="1.3"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.8"/>',
    "download": '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
    "palette": '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.1 0 1.6-.8 1.3-1.7-.4-1-.1-2 1-2.2H17a3.5 3.5 0 0 0 '
               '3.5-3.6A8.4 8.4 0 0 0 12 3.5Z"/><circle cx="7.8" cy="11.5" r="1.1"/>'
               '<circle cx="10.5" cy="7.6" r="1.1"/><circle cx="15" cy="7.8" r="1.1"/>',
}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{b}</svg>')

_pixmaps: dict[tuple, QPixmap] = {}


def _render(svg, w, h, dpr):
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, QRectF(0, 0, w * dpr, h * dpr))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon_pixmap(name, color=None, size=16, dpr=2.0, stroke=1.7):
    """Штриховая иконка из набора (цвет по умолчанию — приглушённый текст темы)."""
    color = color or MUTED
    key = (name, color, size, dpr, stroke)
    if key not in _pixmaps:
        body = _ICONS.get(name, _ICONS["info"]).replace("currentColor", color)
        _pixmaps[key] = _render(_SVG.format(c=color, w=stroke, b=body), size, size, dpr)
    return _pixmaps[key]


def icon(name, color=None, size=16, disabled_color=None):
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, color, size), QIcon.Mode.Normal)
    ic.addPixmap(icon_pixmap(name, color, size), QIcon.Mode.Active)
    ic.addPixmap(icon_pixmap(name, disabled_color or FAINT, size), QIcon.Mode.Disabled)
    return ic


# Логотип: наковальня Forge в шестигранной рамке (мотив UI EVE) + искра; градиент — из темы
# (у Forge cyan → золото).
_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none">
<defs><linearGradient id="g" x1="6" y1="4" x2="42" y2="44" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="{s0}"/><stop offset="0.55" stop-color="{s1}"/>
<stop offset="1" stop-color="{s2}"/></linearGradient>
<radialGradient id="s" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="{glow0}"/>
<stop offset="1" stop-color="{glow1}" stop-opacity="0"/></radialGradient></defs>
<path d="M24 3.5 41.8 13.75v20.5L24 44.5 6.2 34.25v-20.5Z" fill="{bg}" stroke="url(#g)" stroke-width="2.4"
 stroke-linejoin="round"/>
<path d="M24 8.6 37.4 16.3v15.4L24 39.4 10.6 31.7V16.3Z" stroke="url(#g)" stroke-width="0.9" opacity="0.35"/>
<path d="M12.5 20.5h15.2c0 3.2 2.4 5 7.3 5v1.7c-3.2 0-5 1-5.7 2.9H19.8c-.8-2.3-2.9-3.6-6.2-4Z"
 fill="url(#g)" opacity="0.95"/>
<path d="M19 30.4v3.4M28.4 30.4v3.4M16.6 35h14.2" stroke="url(#g)" stroke-width="2.2" stroke-linecap="round"/>
<circle cx="33.5" cy="15.5" r="4.6" fill="url(#s)"/>
<path d="M33.5 11.4c.3 1.8.8 2.6 1.2 3 .4.4 1.2.9 2.5.6-1.8.3-2.6.8-3 1.2-.4.4-.9 1.2-.6 2.5-.3-1.8-.8-2.6-1.2-3-.4-.4-1.2-.9-2.5-.6 1.8-.3 2.6-.8 3-1.2.4-.4.9-1.2.6-2.5Z"
 fill="{star}"/>
</svg>"""


def logo_pixmap(size=40, dpr=2.0, bg=None, spec=None):
    t = spec or _active
    bg = bg or t.window
    key = ("__logo__", t.id, size, dpr, bg)
    if key not in _pixmaps:
        s0, s1, s2 = t.logo_stops
        glow0, glow1, star = t.logo_spark
        svg = _LOGO.format(s0=s0, s1=s1, s2=s2, glow0=glow0, glow1=glow1, star=star, bg=bg)
        _pixmaps[key] = _render(svg, size, size, dpr)
    return _pixmaps[key]


def app_icon():
    """Иконка окна/панели задач: логотип на плашке в цвет фона темы."""
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(BG_DEEP))
        p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.22, s * 0.22)
        inset = s * 0.06
        p.drawPixmap(QRectF(inset, inset, s - 2 * inset, s - 2 * inset),
                     logo_pixmap(int(s), 1.0), QRectF(0, 0, s, s))
        p.end()
        ic.addPixmap(pm)
    return ic


# ---------------------------------------------------------------------------
# Палитра и stylesheet
# ---------------------------------------------------------------------------

def palette(spec=None):
    """``QPalette`` темы ``spec`` (по умолчанию — активной)."""
    t = spec or _active
    pal = QPalette()
    for role, col in (
        (QPalette.ColorRole.Window, t.window),
        (QPalette.ColorRole.WindowText, t.text),
        (QPalette.ColorRole.Base, t.base),
        (QPalette.ColorRole.AlternateBase, t.alt_base),
        (QPalette.ColorRole.Text, t.text),
        (QPalette.ColorRole.Button, t.button),
        (QPalette.ColorRole.ButtonText, t.text),
        (QPalette.ColorRole.BrightText, "#ffffff"),
        (QPalette.ColorRole.Highlight, t.highlight),
        (QPalette.ColorRole.HighlightedText, t.text),
        (QPalette.ColorRole.ToolTipBase, t.popup),
        (QPalette.ColorRole.ToolTipText, t.text),
        (QPalette.ColorRole.PlaceholderText, t.faint),
        (QPalette.ColorRole.Link, t.accent_text),
        (QPalette.ColorRole.LinkVisited, t.accent_text),
    ):
        pal.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.faint))
    return pal


# Картинки stylesheet (галка, шевроны, ветки дерева) — в цветах темы: имя файла →
# (тело SVG, атрибут темы с цветом, толщина штриха). ``url()`` в QSS нужен файл, поэтому они
# пишутся в кэш рядом с иконками предметов.
_DOWN = '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>'
_ASSETS: dict[str, tuple[str, str, float]] = {
    "check.svg": ('<path d="m5 12.6 4.4 4.4L19 7.4"/>', "check_mark", 3.0),
    "chevron-down.svg": (_DOWN, "muted", 2.0),
    "chevron-down-hover.svg": (_DOWN, "accent_text", 2.0),
    "branch-closed.svg": ('<path d="m9.5 6.5 5.5 5.5-5.5 5.5"/>', "faint", 2.0),
    "branch-open.svg": (_DOWN, "accent_text", 2.0),
}


def assets_dir(spec=None) -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
    return Path(base or tempfile.gettempdir()) / "Forge" / "theme" / (spec or _active).id


def _write_asset(path: Path, svg: str) -> bool:
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != svg:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(svg, encoding="utf-8")
        return True
    except OSError:
        return False


def asset(name, spec=None):
    """Путь (прямые слеши) к картинке stylesheet в цветах темы — для ``url()``; файл пишется,
    если его нет или он устарел. Кэш недоступен для записи — во временную папку."""
    t = spec or _active
    body, attr, width = _ASSETS[name]
    svg = _SVG.format(c=getattr(t, attr), w=f"{width:g}", b=body)
    path = assets_dir(t) / name
    if not _write_asset(path, svg):
        path = Path(tempfile.gettempdir()) / "Forge" / "theme" / t.id / name
        _write_asset(path, svg)  # оформление: без картинки stylesheet всё равно работает
    return path.as_posix()


def _status_rules(t):
    # радиусы — не больше половины высоты виджета: иначе Qt молча рисует прямые углы
    rules = []
    pill_radius = 14 if t.radius_chip > 6 else t.radius_chip + 1
    for kind in ("ok", "err", "info", "warn"):
        line, pill = t.statuses[kind], t.pills[kind]
        rules.append(f"""
QLabel[status="{kind}"] {{
    color: {line.fg}; background: {line.bg};
    border: 1px solid {line.border}; border-radius: {min(10, t.radius_tile)}px; padding: 8px 12px;
}}
QFrame[pill="{kind}"] {{ background: {pill.bg}; border: 1px solid {pill.border};
    border-radius: {pill_radius}px; }}
QFrame[pill="{kind}"] QLabel {{ color: {pill.fg}; }}""")
    return "".join(rules)


def _chip_rules(t):
    rules = []
    radius = min(t.radius_chip, 9)  # чип невысок: радиус больше половины высоты Qt не рисует
    for tone in ("cyan", "gold", "green", "red", "violet", "steel"):
        look = t.chip(tone)
        rules.append(f"""
QLabel[chip="{tone}"] {{ font-size: 11px; color: {look.fg}; background: {look.bg};
    border: 1px solid {look.border}; border-radius: {radius}px; padding: 1px 8px; }}""")
    return "".join(rules)


def stylesheet(spec=None):
    """Stylesheet приложения в теме ``spec`` (по умолчанию — активной)."""
    t = spec or _active
    tf = text_family(t)
    d = display_family(t)
    mono = mono_family(t)

    def wash(alpha):
        return themes.rgba(t.wash, alpha)

    def acc(alpha):
        return themes.rgba(t.accent_rgb, alpha)

    def tone(name, alpha):
        return themes.rgba(t.tone_rgb(name), alpha)

    def sel(alpha):
        # выделение: оттенок акцента (сила — по месту) либо сплошной цвет темы
        return t.selected or acc(alpha)

    field_bg = themes.rgba(t.field, 0.85)
    rb, rf, rp, rt = t.radius_button, t.radius_field, t.radius_panel, t.radius_tile
    rc, rm = t.radius_chip, t.radius_menu
    seg = t.tab_active
    return f"""
QWidget {{ color: {t.text}; font-family: "{tf}"; font-size: 13px; }}
QMainWindow {{ background: {t.deep}; }}
QStackedWidget, #Page, #ScrollBody {{ background: transparent; }}
QToolTip {{ background: {t.popup}; color: {t.text}; border: 1px solid {t.line_strong};
    border-radius: {min(8, rm)}px; padding: 6px 8px; }}
QLabel {{ background: transparent; }}

/* поверхности */
QFrame[card="panel"] {{
    background: {t.panel_bg};
    border: 1px solid {t.line}; border-radius: {rp}px; }}
QFrame[card="panel"][hover="true"]:hover {{ border-color: {t.line_strong}; }}
QFrame[card="tile"] {{ background: {t.tile_bg}; border: 1px solid {t.line};
    border-radius: {rt}px; }}
QFrame[card="tile"][hover="true"]:hover {{ background: {wash(0.06)};
    border-color: {t.line_strong}; }}
QFrame[card="accent"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {acc(0.14)},
                                stop:1 {tone("gold", 0.06)});
    border: 1px solid {acc(0.28)}; border-radius: {min(14, rp)}px; }}
QFrame[card="host"] {{ background: {wash(0.02)}; border: 1px solid {t.line};
    border-radius: {rt}px; }}
QFrame[card="host"]:hover {{ background: {wash(0.045)}; border-color: {t.line_strong}; }}
QFrame[card="host"][on="true"] {{ border-color: {acc(0.45)};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {acc(0.12)},
                                stop:1 {tone("steel", 0.07)}); }}
QFrame[chip="true"] {{ background: {wash(0.05)}; border: 1px solid {t.line};
    border-radius: {rb}px; }}
QFrame[chip="true"]:hover {{ border-color: {t.line_strong}; }}
QFrame[seg="true"] {{ background: {wash(0.04)}; border: 1px solid {t.line};
    border-radius: {rb + 6}px; }}
QFrame[hline="true"] {{ background: {t.line}; border: none; max-height: 1px; min-height: 1px; }}
QFrame[popup="true"] {{ background: {t.popup}; border: 1px solid {t.line_strong}; border-radius: {rm}px; }}

/* типографика */
QLabel[role="title"] {{ font-family: "{d}"; font-size: 15px; font-weight: 600; }}
QLabel[role="meta"] {{ font-size: 12px; color: {t.faint}; }}
QLabel[role="muted"] {{ color: {t.muted}; }}
QLabel[role="faint"] {{ color: {t.faint}; font-size: 12px; }}
QLabel[role="label"] {{ color: {t.muted}; font-size: 12px; }}
QLabel[role="section"] {{ font-family: "{d}"; color: {t.accent_text}; font-size: 11px;
    font-weight: 600; }}
QLabel[role="stat-value"] {{ font-family: "{d}"; font-size: 24px; font-weight: 600; }}
QLabel[role="stat-sub"] {{ font-size: 12px; color: {t.faint}; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 12px; color: {t.faint}; }}
QLabel[role="tagline"] {{ font-size: 13px; color: {t.muted}; }}
QLabel[role="empty-title"] {{ font-family: "{d}"; font-size: 20px; font-weight: 600; }}
QLabel[role="body"] {{ font-size: 14px; }}
QLabel[role="good"] {{ color: {t.tone_text("green")}; }}
QLabel[role="bad"] {{ color: {t.tone_text("red")}; }}
QLabel[role="warn"] {{ color: {t.tone_text("gold")}; }}
QLabel[role="isk"] {{ color: {t.tone_text("gold")}; }}
QLabel[role="code"] {{ font-family: "{mono}"; font-size: 12px; color: {t.accent_text};
    background: {t.code_bg}; border: 1px solid {t.line}; border-radius: {min(10, rt)}px;
    padding: 8px 12px; }}
{_chip_rules(t)}

/* кнопки */
QPushButton {{ background: {wash(0.05)}; color: {t.muted}; border: 1px solid {t.line};
    border-radius: {rb}px; padding: 6px 14px; font-size: 13px; min-height: 16px; }}
QPushButton:hover {{ color: {t.text}; border-color: {t.line_strong}; }}
QPushButton:pressed {{ background: {wash(0.02)}; }}
QPushButton:disabled {{ color: {t.faint}; border-color: {wash(0.06)}; }}
QPushButton:checked {{ color: {t.checked.fg}; border-color: {t.checked.border};
    background: {t.checked.bg}; }}
QPushButton[variant="primary"] {{ color: {t.primary.fg}; border-color: {t.primary.border};
    background: {t.primary.bg}; }}
QPushButton[variant="primary"]:hover {{ background: {t.primary_hover_bg}; }}
QPushButton[variant="primary"]:disabled {{ color: {t.faint}; border-color: {t.line};
    background: {wash(0.03)}; }}
QPushButton[variant="gold"] {{ color: {t.tone_text("gold")}; border-color: {tone("gold", 0.42)};
    background: {tone("gold", 0.08)}; }}
QPushButton[variant="gold"]:hover {{ background: {tone("gold", 0.16)}; }}
QPushButton[variant="good"] {{ color: {t.tone_text("green")}; border-color: {tone("green", 0.42)};
    background: {tone("green", 0.08)}; }}
QPushButton[variant="good"]:hover {{ background: {tone("green", 0.16)}; }}
QPushButton[variant="gold"]:disabled, QPushButton[variant="good"]:disabled {{ color: {t.faint};
    border-color: {t.line}; background: {wash(0.03)}; }}
QPushButton[variant="danger"] {{ color: {t.danger.fg}; border-color: {t.danger.border};
    background: {t.danger.bg}; }}
QPushButton[variant="danger"]:hover {{ border-color: {tone("red", 0.7)};
    background: {t.danger_hover_bg}; }}
QPushButton[variant="hero"] {{ color: {t.hero.fg}; font-family: "{d}"; font-size: 14px; font-weight: 600;
    border: 1px solid {t.hero.border}; border-radius: {rb}px; padding: 10px 18px;
    background: {t.hero.bg}; }}
QPushButton[variant="hero"]:hover {{ border-color: {t.hero_hover[1]};
    background: {t.hero_hover[0]}; }}
QPushButton[variant="hero"]:disabled {{ color: {t.faint}; border-color: {t.line};
    background: {wash(0.04)}; }}
QPushButton[variant="hero-gold"] {{ color: {t.hero_gold.fg}; font-family: "{d}"; font-size: 14px; font-weight: 600;
    border: 1px solid {t.hero_gold.border}; border-radius: {rb}px; padding: 10px 18px;
    background: {t.hero_gold.bg}; }}
QPushButton[variant="hero-gold"]:hover {{ border-color: {t.hero_gold_hover_border}; }}
QPushButton[variant="hero-gold"]:disabled {{ color: {t.faint}; border-color: {t.line};
    background: {wash(0.04)}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent;
    padding: 5px 9px; border-radius: {min(10, rb)}px; }}
QPushButton[variant="ghost"]:hover {{ background: {wash(0.06)}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; padding: 2px 0;
    color: {t.accent_text}; text-align: left; }}
QPushButton[variant="link"]:hover {{ color: {t.text}; }}
QPushButton[variant="row"] {{ text-align: left; padding: 8px 10px; border-radius: {min(10, rb)}px;
    border: 1px solid transparent; background: transparent; color: {t.text}; }}
QPushButton[variant="row"]:hover {{ background: {wash(0.04)}; border-color: {t.line}; }}
QPushButton[variant="toggle"] {{ padding: 2px 8px; border-radius: {min(rc, 9)}px; font-size: 11px; min-height: 12px; }}
QPushButton[variant="toggle"][state="build"] {{ color: {t.tone_text("green")};
    border-color: {tone("green", 0.35)}; background: {tone("green", 0.08)}; }}
QPushButton[variant="toggle"][state="buy"] {{ color: {t.tone_text("gold")};
    border-color: {tone("gold", 0.38)}; background: {tone("gold", 0.08)}; }}
QPushButton[variant="toggle"][state="exact"] {{ color: {t.tone_text("violet")};
    border-color: {tone("violet", 0.40)}; background: {tone("violet", 0.10)}; }}

/* сегменты */
QPushButton[segbtn="true"] {{ background: transparent; border: 1px solid transparent;
    border-radius: {rb + 2}px; padding: 7px 14px; color: {t.muted}; font-weight: 500; }}
QPushButton[segbtn="true"]:hover {{ color: {t.text}; }}
QPushButton[segbtn="true"]:checked {{ color: {seg.fg}; background: {seg.bg};
    border-color: {seg.border}; }}
QPushButton[segbtn="true"]:disabled {{ color: {t.faint}; }}
QFrame[seg="true"][small="true"] {{ border-radius: {rb + 2}px; }}
QFrame[seg="true"][small="true"] QPushButton[segbtn="true"] {{ padding: 5px 12px; font-size: 12px;
    border-radius: {max(rb - 2, 2)}px; }}

/* поля ввода */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {field_bg}; color: {t.text}; border: 1px solid {t.line};
    border-radius: {rf}px; padding: 6px 10px; font-size: 13px;
    selection-background-color: {t.text_selection}; selection-color: {t.text}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {t.focus}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {t.faint}; }}
QLineEdit[mono="true"] {{ font-family: "{mono}"; font-size: 12px; }}
QLineEdit[compact="true"], QSpinBox[compact="true"], QDoubleSpinBox[compact="true"] {{
    padding: 3px 6px; border-radius: {min(8, rf)}px; font-size: 12px; }}
QSpinBox, QDoubleSpinBox {{ padding: 5px 8px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0; border: none; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
    width: 28px; border: none; }}
QComboBox::down-arrow {{ image: url("{asset("chevron-down.svg", t)}"); width: 14px; height: 14px;
    margin-right: 10px; }}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{
    image: url("{asset("chevron-down-hover.svg", t)}"); }}
QComboBox QAbstractItemView {{ background: {t.popup}; color: {t.text}; border: 1px solid {t.line_strong};
    border-radius: {rm}px; padding: 4px; outline: 0;
    selection-background-color: {sel(0.20)}; selection-color: {t.text}; }}

QCheckBox {{ spacing: 8px; color: {t.text}; background: transparent; }}
QCheckBox:disabled {{ color: {t.faint}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: {min(5, rf)}px; border: 1px solid {t.line_strong};
    background: {field_bg}; }}
QCheckBox::indicator:hover {{ border-color: {acc(0.6)}; }}
QCheckBox::indicator:checked {{ background: {t.check_fill}; border-color: {t.check_border};
    image: url("{asset("check.svg", t)}"); }}
QCheckBox::indicator:indeterminate {{ background: {acc(0.35)}; border-color: {acc(0.7)}; }}
QCheckBox::indicator:disabled {{ border-color: {t.line}; background: {wash(0.03)}; }}

/* списки, таблицы, деревья */
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ border-radius: {min(10, rt)}px; margin: 1px 0; border: 1px solid transparent; padding: 3px 6px; }}
QListWidget::item:hover {{ background: {wash(0.04)}; }}
QListWidget::item:selected {{ background: {t.list_selected}; border-color: {acc(0.25)}; color: {t.text}; }}
QTableView, QTreeView {{ background: transparent; border: none; outline: 0;
    gridline-color: transparent; selection-background-color: {sel(0.14)};
    selection-color: {t.text}; alternate-background-color: {wash(0.022)}; }}
QTableView::item, QTreeView::item {{ padding: 3px 6px; border: none;
    border-bottom: 1px solid {wash(0.05)}; }}
QTableView::item:hover, QTreeView::item:hover {{ background: {wash(0.05)}; }}
QTableView::item:selected, QTreeView::item:selected {{ background: {sel(0.14)}; color: {t.text}; }}
QTreeView::indicator, QTableView::indicator, QListView::indicator {{ width: 15px; height: 15px;
    border-radius: {min(4, rf)}px; border: 1px solid {t.line_strong}; background: {field_bg}; }}
QTreeView::indicator:hover, QTableView::indicator:hover {{ border-color: {acc(0.6)}; }}
QTreeView::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked {{
    background: {t.check_fill}; border-color: {t.check_border}; image: url("{asset("check.svg", t)}"); }}
QTreeView::indicator:indeterminate {{ background: {acc(0.35)}; border-color: {acc(0.7)}; }}
QTreeView::indicator:disabled {{ border-color: {t.line}; background: {wash(0.03)}; }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:has-children:closed {{ image: url("{asset("branch-closed.svg", t)}"); }}
QTreeView::branch:has-children:open {{ image: url("{asset("branch-open.svg", t)}"); }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: transparent; color: {t.faint}; border: none;
    border-bottom: 1px solid {t.line}; padding: 4px 6px; font-size: 11px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {t.muted}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* прокрутка */
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {wash(0.16)}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t.scroll_hover}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {wash(0.16)}; border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.scroll_hover}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {t.popup}; border: 1px solid {t.line_strong}; border-radius: {rm}px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: {min(6, rm)}px; color: {t.text}; }}
QMenu::item:selected {{ background: {sel(0.18)}; }}
QMenu::item:disabled {{ color: {t.faint}; }}
QMenu::separator {{ height: 1px; background: {t.line}; margin: 4px 8px; }}

QProgressBar {{ background: {wash(0.06)}; border: none; border-radius: 4px; height: 8px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.progress[0]}, stop:1 {t.progress[1]}); }}

QStatusBar {{ background: transparent; color: {t.faint}; border-top: 1px solid {wash(0.07)};
    font-size: 12px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t.faint}; font-size: 12px; padding: 0 6px; }}

QMessageBox, QDialog {{ background: {t.popup}; }}
QMessageBox QLabel {{ color: {t.text}; font-size: 13px; }}
{_status_rules(t)}
"""


def apply(app):
    """Активная тема на всё приложение. Первый раз — до создания окон (Fusion честно красит
    палитрой нативные контролы); повторно — при смене темы на ходу. Цветовая схема (тёмная или
    светлая) заодно говорит Windows, какими рисовать заголовки диалогов."""
    t = _active
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark if t.dark else Qt.ColorScheme.Light)
    except AttributeError:  # Qt < 6.8 — заголовки по системной схеме
        pass
    app.setStyle("Fusion")
    app.setPalette(palette(t))
    app.setFont(font(13))
    app.setStyleSheet(stylesheet(t))


def style_titlebar(window, spec=None):
    """Заголовок окна Windows 11 в цвет темы (иначе светлая полоса над тёмным окном). Как
    получится: старый Windows оставит свой."""
    if sys.platform != "win32":
        return
    t = spec or _active
    caption, text, border = t.titlebar
    try:
        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi

        def set_attr(attr, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        def colorref(hex_color):
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            return (b << 16) | (g << 8) | r

        set_attr(20, 1 if t.dark else 0)   # DWMWA_USE_IMMERSIVE_DARK_MODE
        set_attr(35, colorref(caption))    # DWMWA_CAPTION_COLOR
        set_attr(36, colorref(text))       # DWMWA_TEXT_COLOR
        set_attr(34, colorref(border))     # DWMWA_BORDER_COLOR
    except Exception:  # noqa: S110 — старый Windows: заголовок останется системным
        pass
