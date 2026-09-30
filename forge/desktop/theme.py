"""Тёмная тема пульта Forge в цветах EVE Online.

Платформа — Qt-шелл,
стеклянные панели, сегменты-вкладки, пульсирующие статусы, ночное небо со звёздами. Цвета —
EVE: глубокий космос (#04070b → #0b1118), неоновый cyan интерфейса Photon (#3fb8e0), золото
Omega/ISK (#e8a93a), зелень прибыли, красный убытков, пурпур науки. Заголовки — Bahnschrift
(DIN-подобный, ближе всех системных к шрифту клиента EVE), текст — Segoe UI.

Только представление: палитра, шрифты, stylesheet, иконки и логотип.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, QRectF, Qt
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

# ---------------------------------------------------------------------------
# Токены
# ---------------------------------------------------------------------------

BG0 = "#070a0e"
BG1 = "#0b1118"
BG_DEEP = "#04070b"
POPUP = "#0c131b"
TEXT = "#dde6ef"
MUTED = "#8c9cb0"
FAINT = "#5c6a7b"

CYAN = "#3fb8e0"      # Photon UI
GOLD = "#e8a93a"      # Omega / ISK
GREEN = "#4cc38a"
RED = "#e5534b"
VIOLET = "#a68bf0"

# тон -> (r, g, b базового цвета, светлый цвет текста/иконки на тонированном фоне)
TONES = {
    "cyan": ((63, 184, 224), "#8ad8f2"),
    "gold": ((232, 169, 58), "#f4cd7e"),
    "green": ((76, 195, 138), "#8ee0b6"),
    "red": ((229, 83, 75), "#f29b95"),
    "violet": ((166, 139, 240), "#cbbcf8"),
    "steel": ((111, 149, 232), "#a9c1f5"),
    "muted": ((140, 156, 176), MUTED),
}
# Имена тонов пульта-образца (Glide) → тона EVE: код виджетов переносится без правок.
TONES["sky"] = TONES["cyan"]
TONES["emerald"] = TONES["green"]
TONES["amber"] = TONES["gold"]
TONES["rose"] = TONES["red"]
TONES["indigo"] = TONES["steel"]

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


def tone_rgb(tone):
    return TONES.get(tone, TONES["muted"])[0]


def tone_text(tone):
    return TONES.get(tone, TONES["muted"])[1]


def rgba(tone_or_rgb, alpha):
    """'rgba(r,g,b,A)' для QSS; alpha — доля 0..1."""
    r, g, b = tone_rgb(tone_or_rgb) if isinstance(tone_or_rgb, str) else tone_or_rgb
    return f"rgba({r},{g},{b},{round(alpha * 255)})"


def qcolor(tone_or_rgb, alpha=1.0):
    r, g, b = tone_rgb(tone_or_rgb) if isinstance(tone_or_rgb, str) else tone_or_rgb
    return QColor(r, g, b, round(alpha * 255))


WHITE = (255, 255, 255)
COOL = (150, 200, 235)          # холодный оттенок линий — как рамки окон клиента EVE
LINE = rgba(COOL, 0.10)
LINE_STRONG = rgba(COOL, 0.20)

# ---------------------------------------------------------------------------
# Шрифты
# ---------------------------------------------------------------------------

_TEXT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter"]
_DISPLAY_FAMILIES = ["Bahnschrift", "Segoe UI Variable Display", "Segoe UI"]
_MONO_FAMILIES = ["Cascadia Mono", "Cascadia Code", "Consolas"]
_resolved: dict[tuple[str, ...], str] = {}


def _pick(candidates):
    key = tuple(candidates)
    if key not in _resolved:
        have = set(QFontDatabase.families())
        _resolved[key] = next((f for f in candidates if f in have), candidates[-1])
    return _resolved[key]


def text_family():
    return _pick(_TEXT_FAMILIES)


def display_family():
    return _pick(_DISPLAY_FAMILIES)


def mono_family():
    return _pick(_MONO_FAMILIES)


def font(px, weight=400, display=False, mono=False, spacing=None, num=False):
    """``num`` — табличные цифры (tnum): колонки ISK не «пляшут» по ширине."""
    f = QFont(_pick(_MONO_FAMILIES if mono else _DISPLAY_FAMILIES if display else _TEXT_FAMILIES))
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


def icon_pixmap(name, color=MUTED, size=16, dpr=2.0, stroke=1.7):
    key = (name, color, size, dpr, stroke)
    if key not in _pixmaps:
        body = _ICONS.get(name, _ICONS["info"]).replace("currentColor", color)
        _pixmaps[key] = _render(_SVG.format(c=color, w=stroke, b=body), size, size, dpr)
    return _pixmaps[key]


def icon(name, color=MUTED, size=16, disabled_color=FAINT):
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, color, size), QIcon.Mode.Normal)
    ic.addPixmap(icon_pixmap(name, color, size), QIcon.Mode.Active)
    ic.addPixmap(icon_pixmap(name, disabled_color, size), QIcon.Mode.Disabled)
    return ic


# Логотип: наковальня Forge в шестигранной рамке (мотив UI EVE) + искра; градиент cyan → золото.
_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none">
<defs><linearGradient id="g" x1="6" y1="4" x2="42" y2="44" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#3fb8e0"/><stop offset="0.55" stop-color="#7fd0ec"/>
<stop offset="1" stop-color="#e8a93a"/></linearGradient>
<radialGradient id="s" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#fff5d6"/>
<stop offset="1" stop-color="#e8a93a" stop-opacity="0"/></radialGradient></defs>
<path d="M24 3.5 41.8 13.75v20.5L24 44.5 6.2 34.25v-20.5Z" fill="{bg}" stroke="url(#g)" stroke-width="2.4"
 stroke-linejoin="round"/>
<path d="M24 8.6 37.4 16.3v15.4L24 39.4 10.6 31.7V16.3Z" stroke="url(#g)" stroke-width="0.9" opacity="0.35"/>
<path d="M12.5 20.5h15.2c0 3.2 2.4 5 7.3 5v1.7c-3.2 0-5 1-5.7 2.9H19.8c-.8-2.3-2.9-3.6-6.2-4Z"
 fill="url(#g)" opacity="0.95"/>
<path d="M19 30.4v3.4M28.4 30.4v3.4M16.6 35h14.2" stroke="url(#g)" stroke-width="2.2" stroke-linecap="round"/>
<circle cx="33.5" cy="15.5" r="4.6" fill="url(#s)"/>
<path d="M33.5 11.4c.3 1.8.8 2.6 1.2 3 .4.4 1.2.9 2.5.6-1.8.3-2.6.8-3 1.2-.4.4-.9 1.2-.6 2.5-.3-1.8-.8-2.6-1.2-3-.4-.4-1.2-.9-2.5-.6 1.8-.3 2.6-.8 3-1.2.4-.4.9-1.2.6-2.5Z"
 fill="#fff3d0"/>
</svg>"""


def logo_pixmap(size=40, dpr=2.0, bg=BG1):
    key = ("__logo__", size, dpr, bg)
    if key not in _pixmaps:
        _pixmaps[key] = _render(_LOGO.format(bg=bg), size, size, dpr)
    return _pixmaps[key]


def app_icon():
    """Иконка окна/панели задач: логотип на тёмной плашке."""
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

def _palette():
    pal = QPalette()
    for role, col in (
        (QPalette.ColorRole.Window, BG1),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, "#070b10"),
        (QPalette.ColorRole.AlternateBase, "#0c131b"),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, "#101a24"),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.BrightText, "#ffffff"),
        (QPalette.ColorRole.Highlight, "#17506a"),
        (QPalette.ColorRole.HighlightedText, TEXT),
        (QPalette.ColorRole.ToolTipBase, POPUP),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, FAINT),
        (QPalette.ColorRole.Link, tone_text("cyan")),
        (QPalette.ColorRole.LinkVisited, tone_text("cyan")),
    ):
        pal.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(FAINT))
    return pal


def asset(name):
    """Путь к файлу из forge/desktop/assets для url() в stylesheet (прямые слеши)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets",
                        name).replace("\\", "/")


def _status_rules():
    # радиусы — не больше половины высоты виджета: иначе Qt молча рисует прямые углы
    rules = []
    for kind, tone in (("ok", "green"), ("err", "red"), ("info", "cyan"), ("warn", "gold")):
        rules.append(f"""
QLabel[status="{kind}"] {{
    color: {tone_text(tone)}; background: {rgba(tone, 0.07)};
    border: 1px solid {rgba(tone, 0.30)}; border-radius: 10px; padding: 8px 12px;
}}
QFrame[pill="{kind}"] {{ background: {rgba(tone, 0.08)}; border: 1px solid {rgba(tone, 0.30)};
    border-radius: 14px; }}
QFrame[pill="{kind}"] QLabel {{ color: {tone_text(tone)}; }}""")
    return "".join(rules)


def _chip_rules():
    rules = []
    for tone in ("cyan", "gold", "green", "red", "violet", "steel"):
        rules.append(f"""
QLabel[chip="{tone}"] {{ font-size: 11px; color: {tone_text(tone)}; background: {rgba(tone, 0.09)};
    border: 1px solid {rgba(tone, 0.28)}; border-radius: 9px; padding: 1px 8px; }}""")
    return "".join(rules)


def stylesheet():
    t = text_family()
    d = display_family()
    cy, st = "cyan", "steel"
    seg_on = (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(cy, 0.24)}, "
              f"stop:1 {rgba(st, 0.14)})")
    return f"""
QWidget {{ color: {TEXT}; font-family: "{t}"; font-size: 13px; }}
QMainWindow {{ background: {BG_DEEP}; }}
QStackedWidget, #Page, #ScrollBody {{ background: transparent; }}
QToolTip {{ background: {POPUP}; color: {TEXT}; border: 1px solid {LINE_STRONG};
    border-radius: 8px; padding: 6px 8px; }}
QLabel {{ background: transparent; }}

/* поверхности */
QFrame[card="panel"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {rgba(COOL, 0.050)},
                                stop:1 {rgba(COOL, 0.018)});
    border: 1px solid {LINE}; border-radius: 16px; }}
QFrame[card="panel"][hover="true"]:hover {{ border-color: {LINE_STRONG}; }}
QFrame[card="tile"] {{ background: {rgba(COOL, 0.035)}; border: 1px solid {LINE};
    border-radius: 12px; }}
QFrame[card="tile"][hover="true"]:hover {{ background: {rgba(COOL, 0.06)};
    border-color: {LINE_STRONG}; }}
QFrame[card="accent"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(cy, 0.14)},
                                stop:1 {rgba("gold", 0.06)});
    border: 1px solid {rgba(cy, 0.28)}; border-radius: 14px; }}
QFrame[card="host"] {{ background: {rgba(COOL, 0.02)}; border: 1px solid {LINE};
    border-radius: 12px; }}
QFrame[card="host"]:hover {{ background: {rgba(COOL, 0.045)}; border-color: {LINE_STRONG}; }}
QFrame[card="host"][on="true"] {{ border-color: {rgba(cy, 0.45)};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(cy, 0.12)},
                                stop:1 {rgba(st, 0.07)}); }}
QFrame[chip="true"] {{ background: {rgba(COOL, 0.05)}; border: 1px solid {LINE};
    border-radius: 13px; }}
QFrame[chip="true"]:hover {{ border-color: {LINE_STRONG}; }}
QFrame[seg="true"] {{ background: {rgba(COOL, 0.04)}; border: 1px solid {LINE};
    border-radius: 19px; }}
QFrame[hline="true"] {{ background: {LINE}; border: none; max-height: 1px; min-height: 1px; }}
QFrame[popup="true"] {{ background: {POPUP}; border: 1px solid {LINE_STRONG}; border-radius: 10px; }}

/* типографика */
QLabel[role="title"] {{ font-family: "{d}"; font-size: 15px; font-weight: 600; }}
QLabel[role="meta"] {{ font-size: 12px; color: {FAINT}; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="faint"] {{ color: {FAINT}; font-size: 12px; }}
QLabel[role="label"] {{ color: {MUTED}; font-size: 12px; }}
QLabel[role="section"] {{ font-family: "{d}"; color: {tone_text(cy)}; font-size: 11px;
    font-weight: 600; }}
QLabel[role="stat-value"] {{ font-family: "{d}"; font-size: 24px; font-weight: 600; }}
QLabel[role="stat-sub"] {{ font-size: 12px; color: {FAINT}; }}
QLabel[role="mono"] {{ font-family: "{mono_family()}"; font-size: 12px; color: {FAINT}; }}
QLabel[role="tagline"] {{ font-size: 13px; color: {MUTED}; }}
QLabel[role="empty-title"] {{ font-family: "{d}"; font-size: 20px; font-weight: 600; }}
QLabel[role="body"] {{ font-size: 14px; }}
QLabel[role="good"] {{ color: {tone_text("green")}; }}
QLabel[role="bad"] {{ color: {tone_text("red")}; }}
QLabel[role="warn"] {{ color: {tone_text("gold")}; }}
QLabel[role="isk"] {{ color: {tone_text("gold")}; }}
QLabel[role="code"] {{ font-family: "{mono_family()}"; font-size: 12px; color: {tone_text(cy)};
    background: {rgba((3, 6, 10), 0.75)}; border: 1px solid {LINE}; border-radius: 10px;
    padding: 8px 12px; }}
{_chip_rules()}

/* кнопки */
QPushButton {{ background: {rgba(COOL, 0.05)}; color: {MUTED}; border: 1px solid {LINE};
    border-radius: 13px; padding: 6px 14px; font-size: 13px; min-height: 16px; }}
QPushButton:hover {{ color: {TEXT}; border-color: {LINE_STRONG}; }}
QPushButton:pressed {{ background: {rgba(COOL, 0.02)}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: {rgba(COOL, 0.06)}; }}
QPushButton:checked {{ color: {tone_text(cy)}; border-color: {rgba(cy, 0.45)};
    background: {rgba(cy, 0.10)}; }}
QPushButton[variant="primary"] {{ color: {tone_text(cy)}; border-color: {rgba(cy, 0.42)};
    background: {rgba(cy, 0.10)}; }}
QPushButton[variant="primary"]:hover {{ background: {rgba(cy, 0.18)}; }}
QPushButton[variant="primary"]:disabled {{ color: {FAINT}; border-color: {LINE};
    background: {rgba(COOL, 0.03)}; }}
QPushButton[variant="gold"] {{ color: {tone_text("gold")}; border-color: {rgba("gold", 0.42)};
    background: {rgba("gold", 0.08)}; }}
QPushButton[variant="gold"]:hover {{ background: {rgba("gold", 0.16)}; }}
QPushButton[variant="good"] {{ color: {tone_text("green")}; border-color: {rgba("green", 0.42)};
    background: {rgba("green", 0.08)}; }}
QPushButton[variant="good"]:hover {{ background: {rgba("green", 0.16)}; }}
QPushButton[variant="gold"]:disabled, QPushButton[variant="good"]:disabled {{ color: {FAINT};
    border-color: {LINE}; background: {rgba(COOL, 0.03)}; }}
QPushButton[variant="danger"] {{ color: {tone_text("red")}; border-color: {rgba("red", 0.45)};
    background: transparent; }}
QPushButton[variant="danger"]:hover {{ border-color: {rgba("red", 0.7)};
    background: {rgba("red", 0.08)}; }}
QPushButton[variant="hero"] {{ color: #eafaff; font-family: "{d}"; font-size: 14px; font-weight: 600;
    border: 1px solid {rgba(cy, 0.40)}; border-radius: 13px; padding: 10px 18px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(cy, 0.28)},
                                stop:1 {rgba(st, 0.16)}); }}
QPushButton[variant="hero"]:hover {{ border-color: {rgba(cy, 0.7)};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba(cy, 0.38)},
                                stop:1 {rgba(st, 0.24)}); }}
QPushButton[variant="hero"]:disabled {{ color: {FAINT}; border-color: {LINE};
    background: {rgba(COOL, 0.04)}; }}
QPushButton[variant="hero-gold"] {{ color: #fff6e3; font-family: "{d}"; font-size: 14px; font-weight: 600;
    border: 1px solid {rgba("gold", 0.45)}; border-radius: 13px; padding: 10px 18px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {rgba("gold", 0.26)},
                                stop:1 {rgba("red", 0.10)}); }}
QPushButton[variant="hero-gold"]:hover {{ border-color: {rgba("gold", 0.75)}; }}
QPushButton[variant="hero-gold"]:disabled {{ color: {FAINT}; border-color: {LINE};
    background: {rgba(COOL, 0.04)}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent;
    padding: 5px 9px; border-radius: 10px; }}
QPushButton[variant="ghost"]:hover {{ background: {rgba(COOL, 0.06)}; }}
QPushButton[variant="link"] {{ background: transparent; border: none; padding: 2px 0;
    color: {tone_text(cy)}; text-align: left; }}
QPushButton[variant="link"]:hover {{ color: {TEXT}; }}
QPushButton[variant="row"] {{ text-align: left; padding: 8px 10px; border-radius: 10px;
    border: 1px solid transparent; background: transparent; color: {TEXT}; }}
QPushButton[variant="row"]:hover {{ background: {rgba(COOL, 0.04)}; border-color: {LINE}; }}
QPushButton[variant="toggle"] {{ padding: 2px 8px; border-radius: 9px; font-size: 11px; min-height: 12px; }}
QPushButton[variant="toggle"][state="build"] {{ color: {tone_text("green")};
    border-color: {rgba("green", 0.35)}; background: {rgba("green", 0.08)}; }}
QPushButton[variant="toggle"][state="buy"] {{ color: {tone_text("gold")};
    border-color: {rgba("gold", 0.38)}; background: {rgba("gold", 0.08)}; }}
QPushButton[variant="toggle"][state="exact"] {{ color: {tone_text("violet")};
    border-color: {rgba("violet", 0.40)}; background: {rgba("violet", 0.10)}; }}

/* сегменты */
QPushButton[segbtn="true"] {{ background: transparent; border: 1px solid transparent;
    border-radius: 15px; padding: 7px 14px; color: {MUTED}; font-weight: 500; }}
QPushButton[segbtn="true"]:hover {{ color: {TEXT}; }}
QPushButton[segbtn="true"]:checked {{ color: #eafaff; background: {seg_on};
    border-color: {rgba(cy, 0.32)}; }}
QPushButton[segbtn="true"]:disabled {{ color: {FAINT}; }}
QFrame[seg="true"][small="true"] {{ border-radius: 15px; }}
QFrame[seg="true"][small="true"] QPushButton[segbtn="true"] {{ padding: 5px 12px; font-size: 12px;
    border-radius: 11px; }}

/* поля ввода */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {rgba((5, 9, 14), 0.85)}; color: {TEXT}; border: 1px solid {LINE};
    border-radius: 10px; padding: 6px 10px; font-size: 13px;
    selection-background-color: {rgba(cy, 0.35)}; selection-color: {TEXT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {rgba(cy, 0.55)}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {FAINT}; }}
QLineEdit[mono="true"] {{ font-family: "{mono_family()}"; font-size: 12px; }}
QLineEdit[compact="true"], QSpinBox[compact="true"], QDoubleSpinBox[compact="true"] {{
    padding: 3px 6px; border-radius: 8px; font-size: 12px; }}
QSpinBox, QDoubleSpinBox {{ padding: 5px 8px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0; border: none; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
    width: 28px; border: none; }}
QComboBox::down-arrow {{ image: url("{asset("chevron-down.svg")}"); width: 14px; height: 14px;
    margin-right: 10px; }}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{
    image: url("{asset("chevron-down-hover.svg")}"); }}
QComboBox QAbstractItemView {{ background: {POPUP}; color: {TEXT}; border: 1px solid {LINE_STRONG};
    border-radius: 10px; padding: 4px; outline: 0;
    selection-background-color: {rgba(cy, 0.20)}; selection-color: {TEXT}; }}

QCheckBox {{ spacing: 8px; color: {TEXT}; background: transparent; }}
QCheckBox:disabled {{ color: {FAINT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {LINE_STRONG};
    background: {rgba((5, 9, 14), 0.85)}; }}
QCheckBox::indicator:hover {{ border-color: {rgba(cy, 0.6)}; }}
QCheckBox::indicator:checked {{ background: {rgba(cy, 0.85)}; border-color: {rgba(cy, 0.95)};
    image: url("{asset("check.svg")}"); }}
QCheckBox::indicator:indeterminate {{ background: {rgba(cy, 0.35)}; border-color: {rgba(cy, 0.7)}; }}
QCheckBox::indicator:disabled {{ border-color: {LINE}; background: {rgba(COOL, 0.03)}; }}

/* списки, таблицы, деревья */
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ border-radius: 10px; margin: 1px 0; border: 1px solid transparent; padding: 3px 6px; }}
QListWidget::item:hover {{ background: {rgba(COOL, 0.04)}; }}
QListWidget::item:selected {{ background: {seg_on}; border-color: {rgba(cy, 0.25)}; color: {TEXT}; }}
QTableView, QTreeView {{ background: transparent; border: none; outline: 0;
    gridline-color: transparent; selection-background-color: {rgba(cy, 0.14)};
    selection-color: {TEXT}; alternate-background-color: {rgba(COOL, 0.022)}; }}
QTableView::item, QTreeView::item {{ padding: 3px 6px; border: none;
    border-bottom: 1px solid {rgba(COOL, 0.05)}; }}
QTableView::item:hover, QTreeView::item:hover {{ background: {rgba(COOL, 0.05)}; }}
QTableView::item:selected, QTreeView::item:selected {{ background: {rgba(cy, 0.14)}; color: {TEXT}; }}
QTreeView::indicator, QTableView::indicator, QListView::indicator {{ width: 15px; height: 15px;
    border-radius: 4px; border: 1px solid {LINE_STRONG}; background: {rgba((5, 9, 14), 0.85)}; }}
QTreeView::indicator:hover, QTableView::indicator:hover {{ border-color: {rgba(cy, 0.6)}; }}
QTreeView::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked {{
    background: {rgba(cy, 0.85)}; border-color: {rgba(cy, 0.95)}; image: url("{asset("check.svg")}"); }}
QTreeView::indicator:indeterminate {{ background: {rgba(cy, 0.35)}; border-color: {rgba(cy, 0.7)}; }}
QTreeView::indicator:disabled {{ border-color: {LINE}; background: {rgba(COOL, 0.03)}; }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:has-children:closed {{ image: url("{asset("branch-closed.svg")}"); }}
QTreeView::branch:has-children:open {{ image: url("{asset("branch-open.svg")}"); }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{ background: transparent; color: {FAINT}; border: none;
    border-bottom: 1px solid {LINE}; padding: 4px 6px; font-size: 11px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {MUTED}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* прокрутка */
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {rgba(COOL, 0.16)}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {rgba(cy, 0.40)}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {rgba(COOL, 0.16)}; border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {rgba(cy, 0.40)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {POPUP}; border: 1px solid {LINE_STRONG}; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; color: {TEXT}; }}
QMenu::item:selected {{ background: {rgba(cy, 0.18)}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}

QProgressBar {{ background: {rgba(COOL, 0.06)}; border: none; border-radius: 4px; height: 8px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {CYAN}, stop:1 {GOLD}); }}

QStatusBar {{ background: transparent; color: {FAINT}; border-top: 1px solid {rgba(COOL, 0.07)};
    font-size: 12px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {FAINT}; font-size: 12px; padding: 0 6px; }}

QMessageBox, QDialog {{ background: {POPUP}; }}
QMessageBox QLabel {{ color: {TEXT}; font-size: 13px; }}
{_status_rules()}
"""


def apply(app):
    """Тема на всё приложение. Вызывать до создания окон (Fusion честно красит палитрой
    нативные контролы)."""
    app.setStyle("Fusion")
    app.setPalette(_palette())
    app.setFont(font(13))
    app.setStyleSheet(stylesheet())
