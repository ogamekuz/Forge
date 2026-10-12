"""Темы пульта Forge: наборы токенов — цвета, шрифты, скругления.

Десять тем, восемь тёмных и две светлые:

- **Forge** — вид по умолчанию: глубокий космос EVE (#04070b → #0b1118), cyan интерфейса Photon,
  золото Omega/ISK, заголовки Bahnschrift, стеклянные панели с уголками-скобами, звёзды и
  туманности над шапкой, переливающееся название.
- **Graphite** — строгий графит: тушь #1D1D1B и графит #3A3A38, красный #DA291C только как
  акцент, серые, Arial; «готово» — белым по туши, ошибка — красным, зелёного нет, без скоб.
- **Studio** — нейтральный уголь в духе редакторов кода: плоские серые, синий Fluent, малые
  скругления.
- **Fjord** — полярная ночь палитры Nord: сланцево-синие, морозный cyan, мягкий контраст.
- **Solarized** — Solarized dark: глубокий бирюзовый фон, синий и жёлтый акценты.
- **Amethyst** — ночь Dracula: фиолетовый с розовым, мятным и cyan; скруглённая.
- **Ember** — факел в ночи: тёплый уголь, оранжевый акцент, стальной синий, прямые углы.
- **Contrast** — высокий контраст для проектора и уставших глаз: чёрный, белый, контуры вместо
  заливок, cyan и жёлтый.
- **Daylight** (светлая) — белые панели на холодном сером, тёмный текст, синий акцент.
- **Sandstone** (светлая) — тёплая бумага: песок, бирюза и охра, заголовки Georgia.

Эффекты (звёзды, туманности, перелив названия) — только у Forge; остальные статичны, но точки
статуса пульсируют везде: это сигнал, а не украшение. Forge и Graphite расписаны по токенам,
остальные восемь собирает ``_tinted()`` из нескольких цветов каждая (акцент красит выделение,
чипы и статусы — как у Forge).

Только строки, без Qt: палитру, stylesheet, иконки и логотип из активной темы строит
``theme.py``. Выбор темы — ``[ui] theme`` конфига (окно «Тема» в шапке пульта).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..i18n import N_
from ..planner.instructions import GANTT_COLORS

DEFAULT_THEME = "forge"

Rgb = tuple[int, int, int]


def rgba(rgb: Rgb, alpha: float) -> str:
    """``rgba(r,g,b,A)`` для QSS; ``alpha`` — доля 0..1."""
    r, g, b = rgb
    return f"rgba({r},{g},{b},{round(alpha * 255)})"


def hex_rgb(color: str) -> Rgb:
    """``#rrggbb`` → ``(r, g, b)``."""
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def rgb_hex(rgb: Rgb) -> str:
    """``(r, g, b)`` → ``#rrggbb``."""
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _mix(a: str, b: str, share: float) -> str:
    """Цвет на доле ``share`` (0..1) пути от ``a`` к ``b`` (оба ``#rrggbb``)."""
    return "#" + "".join(
        f"{round(x + (y - x) * share):02x}" for x, y in zip(hex_rgb(a), hex_rgb(b), strict=True)
    )


def _gradient(a: str, b: str, diagonal: bool = True) -> str:
    """Линейный градиент QSS от ``a`` к ``b``: по диагонали либо сверху вниз."""
    return f"qlineargradient(x1:0, y1:0, x2:{1 if diagonal else 0}, y2:1, stop:0 {a}, stop:1 {b})"


@dataclass(frozen=True, slots=True)
class Look:
    """Фон, текст и рамка чипа, «пилюли», строки статуса или кнопки."""

    bg: str
    fg: str
    border: str


@dataclass(frozen=True, slots=True)
class Theme:
    """Все цвета, шрифты и скругления, которые пульт берёт из темы."""

    id: str
    title: str
    #: Ключ перевода (``N_``): показывать через ``tr()``.
    description: str
    #: Тёмная (светлый текст на тёмном) или светлая: цветовая схема Qt и заголовок окна Windows.
    dark: bool
    # -- поверхности --
    #: Под всем: фон окна, плашка иконки окна.
    deep: str
    #: Тело окон и диалогов (QPalette.Window), подложка логотипа.
    window: str
    #: Вертикальный градиент фона окна: верх, 60 % высоты, низ.
    backdrop: tuple[str, str, str]
    #: Фон полей и списков у нативной отрисовки Qt (QPalette.Base).
    base: str
    alt_base: str
    #: Меню, подсказки, диалоги.
    popup: str
    button: str
    #: QPalette.Highlight.
    highlight: str
    #: Фон ``card="panel"`` / ``card="tile"`` (любая кисть QSS).
    panel_bg: str
    tile_bg: str
    #: Вуаль (наведение, чередование строк, ручки прокрутки): светлая на тёмной теме, тёмная
    #: на светлой.
    wash: Rgb
    #: Фон полей ввода: ``rgba(field, .85)``.
    field: Rgb
    #: Фон вставки кода/пути (``role="code"``).
    code_bg: str
    # -- текст --
    text: str
    muted: str
    faint: str
    # -- линии --
    line: str
    line_strong: str
    # -- акцент --
    #: Выделенные строки, пункты меню; пусто — оттенок акцента, сила — по месту.
    selected: str
    #: Выбранный пункт списка (подсказки поиска).
    list_selected: str
    #: Рамка поля в фокусе.
    focus: str
    #: Выделение текста в поле.
    text_selection: str
    scroll_hover: str
    #: Полоса прогресса: начало и конец градиента (одинаковые — сплошная).
    progress: tuple[str, str]
    # -- смысл --
    #: Тон → (rgb, цвет текста/иконки на панели): ``cyan`` — акцент, ``gold`` — ISK и
    #: предупреждения, ``green`` — прибыль/готово, ``red`` — убыток/ошибка, ``violet`` — наука.
    tones: Mapping[str, tuple[Rgb, str]]
    #: Тон → вид чипа (``QLabel[chip="<тон>"]``).
    chips: Mapping[str, Look]
    #: ok / err / info / warn → вид строки статуса и «пилюли» в шапке.
    statuses: Mapping[str, Look]
    pills: Mapping[str, Look]
    #: ok / err / info / warn → точка «пилюли».
    dots: Mapping[str, str]
    #: Цвета рядов (персонажи на диаграмме Ганта) и текст на полосе такого цвета.
    series: tuple[str, ...]
    on_series: str
    # -- элементы --
    #: Выбранный сегмент (вкладка).
    tab_active: Look
    primary: Look
    primary_hover_bg: str
    #: Нажатая кнопка-переключатель.
    checked: Look
    danger: Look
    danger_hover_bg: str
    #: Главная кнопка страницы и её наведение (фон, рамка).
    hero: Look
    hero_hover: tuple[str, str]
    hero_gold: Look
    hero_gold_hover_border: str
    check_fill: str
    check_border: str
    check_mark: str
    radius_button: int
    radius_field: int
    radius_panel: int
    radius_tile: int
    radius_chip: int
    radius_menu: int
    # -- шрифты --
    text_fonts: tuple[str, ...]
    display_fonts: tuple[str, ...]
    mono_fonts: tuple[str, ...]
    # -- оформление --
    #: Звёзды и туманности на фоне, перелив названия в шапке.
    effects: bool
    #: Уголки-скобы на панелях (мотив окон клиента EVE).
    corner_brackets: bool
    #: Название в шапке: стопы градиента (один цвет — сплошное).
    brand: tuple[str, ...]
    #: Логотип: три стопа градиента рамки и наковальни; искра — центр и край свечения, звезда.
    logo_stops: tuple[str, str, str]
    logo_spark: tuple[str, str, str]
    #: Заголовок окна Windows: фон, текст, рамка.
    titlebar: tuple[str, str, str]

    def tone_rgb(self, tone: str) -> Rgb:
        return self.tones.get(tone, self.tones["muted"])[0]

    def tone_text(self, tone: str) -> str:
        return self.tones.get(tone, self.tones["muted"])[1]

    def tone_color(self, tone: str) -> str:
        """Базовый цвет тона как ``#rrggbb``."""
        return rgb_hex(self.tone_rgb(tone))

    def chip(self, tone: str) -> Look:
        return self.chips.get(tone, self.chips["muted"])

    @property
    def accent_rgb(self) -> Rgb:
        return self.tone_rgb("cyan")

    @property
    def accent(self) -> str:
        return self.tone_color("cyan")

    @property
    def accent_text(self) -> str:
        return self.tone_text("cyan")


#: Имена тонов из чужих палитр → тона Forge: код виджетов переносится без правок.
TONE_ALIASES = {"sky": "cyan", "emerald": "green", "amber": "gold", "rose": "red", "indigo": "steel"}
#: Вид статуса → тон, которым он красится.
_KIND_TONE = {"ok": "green", "err": "red", "info": "cyan", "warn": "gold"}


def _with_aliases(tones: dict[str, tuple[Rgb, str]]) -> dict[str, tuple[Rgb, str]]:
    return {**tones, **{alias: tones[tone] for alias, tone in TONE_ALIASES.items()}}


# ---------------------------------------------------------------------------
# Forge — глубокий космос EVE, cyan и золото
# ---------------------------------------------------------------------------

_COOL: Rgb = (150, 200, 235)  # холодный оттенок линий — как рамки окон клиента EVE
_CY: Rgb = (63, 184, 224)     # Photon UI
_ST: Rgb = (111, 149, 232)
_GOLD: Rgb = (232, 169, 58)   # Omega / ISK
_CORAL: Rgb = (229, 83, 75)
_FORGE_TONES: dict[str, tuple[Rgb, str]] = {
    "cyan": (_CY, "#8ad8f2"),
    "gold": (_GOLD, "#f4cd7e"),
    "green": ((76, 195, 138), "#8ee0b6"),
    "red": (_CORAL, "#f29b95"),
    "violet": ((166, 139, 240), "#cbbcf8"),
    "steel": (_ST, "#a9c1f5"),
    "muted": ((140, 156, 176), "#8c9cb0"),
}
_FORGE_SEGMENT = _gradient(rgba(_CY, 0.24), rgba(_ST, 0.14))

FORGE = Theme(
    id="forge",
    title="Forge",
    description=N_("Космос EVE: cyan Photon, золото Omega, звёзды и уголки-скобы"),
    dark=True,
    deep="#04070b",
    window="#0b1118",
    backdrop=("#0b1118", "#070a0e", "#04070b"),
    base="#070b10",
    alt_base="#0c131b",
    popup="#0c131b",
    button="#101a24",
    highlight="#17506a",
    panel_bg=_gradient(rgba(_COOL, 0.050), rgba(_COOL, 0.018), diagonal=False),
    tile_bg=rgba(_COOL, 0.035),
    wash=_COOL,
    field=(5, 9, 14),
    code_bg=rgba((3, 6, 10), 0.75),
    text="#dde6ef",
    muted="#8c9cb0",
    faint="#5c6a7b",
    line=rgba(_COOL, 0.10),
    line_strong=rgba(_COOL, 0.20),
    selected="",
    list_selected=_FORGE_SEGMENT,
    focus=rgba(_CY, 0.55),
    text_selection=rgba(_CY, 0.35),
    scroll_hover=rgba(_CY, 0.40),
    progress=("#3fb8e0", "#e8a93a"),
    tones=_with_aliases(_FORGE_TONES),
    chips={
        tone: Look(rgba(rgb, 0.09), text, rgba(rgb, 0.28)) for tone, (rgb, text) in _FORGE_TONES.items()
    },
    statuses={
        kind: Look(rgba(_FORGE_TONES[t][0], 0.07), _FORGE_TONES[t][1], rgba(_FORGE_TONES[t][0], 0.30))
        for kind, t in _KIND_TONE.items()
    },
    pills={
        kind: Look(rgba(_FORGE_TONES[t][0], 0.08), _FORGE_TONES[t][1], rgba(_FORGE_TONES[t][0], 0.30))
        for kind, t in _KIND_TONE.items()
    },
    dots={"ok": "#4cc38a", "err": "#e5534b", "info": "#3fb8e0", "warn": "#e8a93a"},
    # те же цвета персонажей, что в HTML-инструкции расписания
    series=tuple(GANTT_COLORS),
    on_series="#061018",
    tab_active=Look(_FORGE_SEGMENT, "#eafaff", rgba(_CY, 0.32)),
    primary=Look(rgba(_CY, 0.10), "#8ad8f2", rgba(_CY, 0.42)),
    primary_hover_bg=rgba(_CY, 0.18),
    checked=Look(rgba(_CY, 0.10), "#8ad8f2", rgba(_CY, 0.45)),
    danger=Look("transparent", "#f29b95", rgba(_CORAL, 0.45)),
    danger_hover_bg=rgba(_CORAL, 0.08),
    hero=Look(_gradient(rgba(_CY, 0.28), rgba(_ST, 0.16)), "#eafaff", rgba(_CY, 0.40)),
    hero_hover=(_gradient(rgba(_CY, 0.38), rgba(_ST, 0.24)), rgba(_CY, 0.7)),
    hero_gold=Look(_gradient(rgba(_GOLD, 0.26), rgba(_CORAL, 0.10)), "#fff6e3", rgba(_GOLD, 0.45)),
    hero_gold_hover_border=rgba(_GOLD, 0.75),
    check_fill=rgba(_CY, 0.85),
    check_border=rgba(_CY, 0.95),
    check_mark="#04121a",
    radius_button=13,
    radius_field=10,
    radius_panel=16,
    radius_tile=12,
    radius_chip=9,
    radius_menu=10,
    # Bahnschrift — DIN-подобный, ближе всех системных к шрифту клиента EVE
    text_fonts=("Segoe UI Variable Text", "Segoe UI", "Inter"),
    display_fonts=("Bahnschrift", "Segoe UI Variable Display", "Segoe UI"),
    mono_fonts=("Cascadia Mono", "Cascadia Code", "Consolas"),
    effects=True,
    corner_brackets=True,
    brand=("#3fb8e0", "#a9e4f7", "#e8a93a", "#7fd0ec", "#3fb8e0"),
    logo_stops=("#3fb8e0", "#7fd0ec", "#e8a93a"),
    logo_spark=("#fff5d6", "#e8a93a", "#fff3d0"),
    titlebar=("#0b1118", "#dde6ef", "#18293a"),
)


# ---------------------------------------------------------------------------
# Graphite — тушь, графит и красный акцент
# ---------------------------------------------------------------------------

_RED = "#DA291C"
_RED_RGB: Rgb = (218, 41, 28)
_INK = "#1D1D1B"
_INNER = "#3A3A38"
_WHITE = "#F4F4F4"     # серо-белый: мягче чистого белого на туши
_LIGHT = "#C8C8C8"     # второстепенный текст на тёмном
_MID = "#8A8A8A"
_GRAY = "#6B6B6B"
_RED_TEXT = "#F0A9A2"  # красный текст, читаемый на туши
_GRAPHITE_TONES: dict[str, tuple[Rgb, str]] = {
    "cyan": (_RED_RGB, _RED_TEXT),  # тон акцента здесь красный
    "gold": ((200, 200, 200), _LIGHT),
    "green": ((244, 244, 244), _WHITE),
    "red": (_RED_RGB, _RED_TEXT),
    "violet": ((189, 189, 189), _LIGHT),
    "steel": ((138, 138, 138), _LIGHT),
    "muted": ((138, 138, 138), _MID),
}
_GRAPHITE_STATUSES = {
    "ok": Look("#262624", _WHITE, _GRAY),
    "err": Look("#2B1A18", _RED_TEXT, _RED),
    "info": Look("#262624", _WHITE, "#4A4A48"),
    "warn": Look("#262624", _LIGHT, _GRAY),
}

GRAPHITE = Theme(
    id="graphite",
    title="Graphite",
    description=N_("Строгий графит: тушь, серые, красный акцент, Arial"),
    dark=True,
    deep="#121211",
    window=_INK,
    backdrop=("#121211", "#121211", "#121211"),
    base="#161615",
    alt_base="#262624",
    popup="#2A2A28",
    button=_INNER,
    highlight=_INNER,
    panel_bg=_INK,
    tile_bg="#262624",
    wash=(255, 255, 255),
    field=(22, 22, 21),
    code_bg=rgba((22, 22, 21), 0.75),
    text=_WHITE,
    muted=_LIGHT,
    faint=_MID,
    line=_INNER,
    line_strong=_GRAY,
    selected=_INNER,
    list_selected=_INNER,
    focus=_RED,
    text_selection=rgba(_RED_RGB, 0.45),
    scroll_hover=_MID,
    progress=(_RED, _RED),
    tones=_with_aliases(_GRAPHITE_TONES),
    chips={
        "green": Look(_WHITE, _INK, _WHITE),            # готово
        "red": Look(_RED, "#FFFFFF", _RED),             # ошибка / просрочено
        "cyan": Look("transparent", _RED_TEXT, _RED),   # в работе
        "gold": Look(_INNER, _LIGHT, _INNER),           # ждёт / запланировано
        "violet": Look(_INNER, _LIGHT, _GRAY),
        "steel": Look(_INNER, _LIGHT, _GRAY),
        "muted": Look("transparent", _MID, "#4A4A48"),
    },
    statuses=_GRAPHITE_STATUSES,
    pills=_GRAPHITE_STATUSES,
    dots={"ok": _WHITE, "err": _RED, "info": _LIGHT, "warn": _LIGHT},
    series=(
        _WHITE, _RED, _MID, "#5B8DB8", "#C9A227",
        "#4FA3A0", "#A06CB4", "#8FA14A", "#D9773A", "#BDBDBD",
    ),
    on_series=_INK,
    # вкладки — плашки, выбранная инвертирована
    tab_active=Look(_WHITE, _INK, _WHITE),
    primary=Look(_WHITE, _INK, _WHITE),
    primary_hover_bg="#FFFFFF",
    checked=Look(_WHITE, _INK, _WHITE),
    danger=Look("transparent", _RED_TEXT, _RED),
    danger_hover_bg=rgba(_RED_RGB, 0.15),
    # красный — только главное действие страницы
    hero=Look(_RED, "#FFFFFF", _RED),
    hero_hover=("#E24B40", "#E24B40"),
    hero_gold=Look(_WHITE, _INK, _WHITE),
    hero_gold_hover_border="#FFFFFF",
    check_fill=_RED,
    check_border=_RED,
    check_mark="#FFFFFF",
    radius_button=8,
    radius_field=5,
    radius_panel=11,
    radius_tile=8,
    radius_chip=5,
    radius_menu=8,
    text_fonts=("Arial", "Helvetica Neue", "Helvetica"),
    display_fonts=("Arial", "Helvetica Neue", "Helvetica"),
    mono_fonts=("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New"),
    effects=False,
    corner_brackets=False,
    brand=(_WHITE,),
    logo_stops=(_WHITE, _WHITE, _WHITE),
    logo_spark=(_RED, _RED, _WHITE),
    titlebar=(_INK, _WHITE, _INNER),
)


# ---------------------------------------------------------------------------
# Остальные восемь: по нескольку цветов, прочее выводится так же, как у Forge
# ---------------------------------------------------------------------------

_MONO = ("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New")
_SEGOE_TEXT = ("Segoe UI Variable Text", "Segoe UI", "Arial")
_SEGOE_DISPLAY = ("Segoe UI Variable Display", "Segoe UI", "Arial")


def _tinted(
    *,
    theme_id: str,
    title: str,
    description: str,
    dark: bool,
    deep: str,
    window: str,
    base: str,
    alt_base: str,
    popup: str,
    button: str,
    text: str,
    muted: str,
    faint: str,
    line: str,
    line_strong: str,
    accent: tuple[str, str],
    second: str,
    good: tuple[str, str],
    bad: tuple[str, str],
    warn: tuple[str, str],
    science: tuple[str, str],
    steel: tuple[str, str],
    series: tuple[str, ...],
    tab_active: tuple[str, str, str],
    filled: bool,
    on_accent: str,
    radii: tuple[int, int, int, int, int, int],
    display_fonts: tuple[str, ...],
    logo: tuple[str, str, str, str],
    text_fonts: tuple[str, ...] = _SEGOE_TEXT,
    chip_alpha: tuple[float, float] | None = None,
    status_alpha: tuple[float, float] | None = None,
    **overrides: Any,
) -> Theme:
    """Тема в манере Forge из нескольких цветов.

    ``accent``, ``good``, ``bad``, ``warn``, ``science`` и ``steel`` — (цвет, его текст на
    панели): тона ``cyan``, ``green``, ``red``, ``gold``, ``violet`` и ``steel`` чипов и статусов.
    Акцент красит выделение, фокус, чипы и статусы; ``filled`` заливает им главные кнопки и
    галки (иначе они тонированы, как у Forge). ``second`` — второй акцент (свечение искры
    логотипа). ``tab_active`` — выбранный сегмент (фон, рамка, текст); ``radii`` — кнопка, поле,
    панель, плитка, чип, меню; ``logo`` — три стопа градиента логотипа и цвет его звезды;
    ``chip_alpha``/``status_alpha`` — прозрачность (фон, рамка) тона. ``overrides`` заменяют
    любой выведенный токен.
    """
    acc = hex_rgb(accent[0])
    tones: dict[str, tuple[Rgb, str]] = {
        "cyan": (acc, accent[1]),
        "gold": (hex_rgb(warn[0]), warn[1]),
        "green": (hex_rgb(good[0]), good[1]),
        "red": (hex_rgb(bad[0]), bad[1]),
        "violet": (hex_rgb(science[0]), science[1]),
        "steel": (hex_rgb(steel[0]), steel[1]),
        "muted": (hex_rgb(muted), muted),
    }
    chip_bg, chip_border = chip_alpha or ((0.10, 0.34) if dark else (0.10, 0.42))
    status_bg, status_border = status_alpha or ((0.08, 0.30) if dark else (0.07, 0.40))
    # вуаль осветляет тёмную тему и затемняет светлую
    wash: Rgb = (255, 255, 255) if dark else hex_rgb(text)
    field: Rgb = hex_rgb(deep) if dark else hex_rgb(base)
    bad_rgb, warn_rgb = hex_rgb(bad[0]), hex_rgb(warn[0])
    if filled:
        hover = _mix(accent[0], "#ffffff" if dark else "#000000", 0.12)
        primary = Look(accent[0], on_accent, accent[0])
        primary_hover = hover
        hero, hero_hover = primary, (hover, hover)
        check_fill = check_border = accent[0]
    else:
        primary = Look(rgba(acc, 0.12), accent[1], rgba(acc, 0.45))
        primary_hover = rgba(acc, 0.20)
        hero, hero_hover = Look(rgba(acc, 0.26), on_accent, rgba(acc, 0.55)), (rgba(acc, 0.36), rgba(acc, 0.85))
        check_fill, check_border = rgba(acc, 0.85), accent[0]
    statuses = {
        kind: Look(rgba(tones[t][0], status_bg), tones[t][1], rgba(tones[t][0], status_border))
        for kind, t in _KIND_TONE.items()
    }
    rb, rf, rp, rt, rc, rm = radii
    theme = Theme(
        id=theme_id,
        title=title,
        description=description,
        dark=dark,
        deep=deep,
        window=window,
        backdrop=(deep, deep, deep),
        base=base,
        alt_base=alt_base,
        popup=popup,
        button=button,
        highlight=_mix(window, accent[0], 0.35 if dark else 0.22),
        # панели: тело окна на тёмной теме, белая бумага на светлой
        panel_bg=window if dark else base,
        tile_bg=rgba(wash, 0.04 if dark else 0.035),
        wash=wash,
        field=field,
        code_bg=rgba(field, 0.75) if dark else rgba(wash, 0.05),
        text=text,
        muted=muted,
        faint=faint,
        line=line,
        line_strong=line_strong,
        selected="",
        list_selected=rgba(acc, 0.18 if dark else 0.12),
        focus=rgba(acc, 0.65) if dark else accent[0],
        text_selection=rgba(acc, 0.35 if dark else 0.25),
        scroll_hover=rgba(acc, 0.45 if dark else 0.55),
        progress=(accent[0], accent[0]),
        tones=_with_aliases(tones),
        chips={
            tone: Look(rgba(rgb, chip_bg), tone_text, rgba(rgb, chip_border))
            for tone, (rgb, tone_text) in tones.items()
        },
        statuses=statuses,
        pills=statuses,
        dots={"ok": good[0], "err": bad[0], "info": accent[0], "warn": warn[0]},
        series=series,
        # ряды яркие на тёмной теме и насыщенные на светлой
        on_series=deep if dark else "#ffffff",
        tab_active=Look(tab_active[0], tab_active[2], tab_active[1]),
        primary=primary,
        primary_hover_bg=primary_hover,
        checked=Look(rgba(acc, 0.14), accent[1], rgba(acc, 0.50)),
        danger=Look("transparent", bad[1], rgba(bad_rgb, 0.50)),
        danger_hover_bg=rgba(bad_rgb, 0.10),
        hero=hero,
        hero_hover=hero_hover,
        hero_gold=Look(rgba(warn_rgb, 0.20 if dark else 0.14), warn[1], rgba(warn_rgb, 0.55)),
        hero_gold_hover_border=rgba(warn_rgb, 0.90),
        check_fill=check_fill,
        check_border=check_border,
        check_mark=on_accent,
        radius_button=rb,
        radius_field=rf,
        radius_panel=rp,
        radius_tile=rt,
        radius_chip=rc,
        radius_menu=rm,
        text_fonts=text_fonts,
        display_fonts=display_fonts,
        mono_fonts=_MONO,
        effects=False,
        corner_brackets=False,
        brand=(text,),
        logo_stops=(logo[0], logo[1], logo[2]),
        logo_spark=(second, second, logo[3] if dark else second),
        titlebar=(deep, text, _mix(deep, text, 0.15)),
    )
    return replace(theme, **overrides) if overrides else theme


STUDIO = _tinted(
    theme_id="studio",
    title="Studio",
    description=N_("Нейтральный уголь, как редактор кода: плоско, спокойно, синий Fluent"),
    dark=True,
    deep="#181818",
    window="#1f1f1f",
    base="#1a1a1a",
    alt_base="#262626",
    popup="#252526",
    button="#2b2b2b",
    text="#e0e0e0",
    muted="#a8a8a8",
    faint="#767676",
    line="#2e2e2e",
    line_strong="#454545",
    accent=("#0078d4", "#4daafc"),
    second="#e2c08d",
    good=("#73c991", "#93dbab"),
    bad=("#f14c4c", "#ff8f8f"),
    warn=("#cca700", "#e6c75a"),
    science=("#b180d7", "#cfaaeb"),
    steel=("#8a9bb0", "#b4c1d1"),
    series=(
        "#4daafc", "#e2c08d", "#73c991", "#b180d7", "#f14c4c",
        "#4ec9b0", "#ce9178", "#f78fb3", "#dcdcaa", "#9d9d9d",
    ),
    tab_active=("#2b2b2b", "#3c3c3c", "#ffffff"),
    filled=True,
    on_accent="#ffffff",
    radii=(4, 4, 6, 6, 4, 6),
    display_fonts=_SEGOE_DISPLAY,
    logo=("#4daafc", "#0078d4", "#005a9e", "#e0e0e0"),
)

FJORD = _tinted(
    theme_id="fjord",
    title="Fjord",
    description=N_("Полярная ночь: сланцево-синие тона, морозный cyan, мягкий контраст"),
    dark=True,
    deep="#242933",
    window="#2e3440",
    base="#272c36",
    alt_base="#353c4a",
    popup="#3b4252",
    button="#3b4252",
    text="#eceff4",
    muted="#b3bccb",
    faint="#808b9f",
    line="#3b4252",
    line_strong="#4c566a",
    accent=("#88c0d0", "#9fd3e1"),
    second="#ebcb8b",
    good=("#a3be8c", "#bfd6ab"),
    bad=("#bf616a", "#e3a1a7"),
    warn=("#ebcb8b", "#f2dcae"),
    science=("#b48ead", "#d4b9cf"),
    steel=("#81a1c1", "#a9c1d9"),
    series=(
        "#88c0d0", "#ebcb8b", "#a3be8c", "#b48ead", "#bf616a",
        "#81a1c1", "#d08770", "#8fbcbb", "#5e81ac", "#e5e9f0",
    ),
    tab_active=(rgba((136, 192, 208), 0.16), rgba((136, 192, 208), 0.42), "#eceff4"),
    filled=True,
    on_accent="#2e3440",
    radii=(6, 5, 10, 8, 10, 8),
    display_fonts=("Corbel", "Segoe UI Variable Display", "Segoe UI"),
    logo=("#8fbcbb", "#88c0d0", "#81a1c1", "#eceff4"),
)

SOLARIZED = _tinted(
    theme_id="solarized",
    title="Solarized",
    description=N_("Solarized dark: глубокий бирюзовый фон, синий и жёлтый акценты"),
    dark=True,
    deep="#00212b",
    window="#002b36",
    base="#00232c",
    alt_base="#073642",
    popup="#073642",
    button="#073642",
    # на шаг ярче base1 (#93a1a1): тексту в 13 px нужен контраст 7:1
    text="#c3cdcd",
    muted="#93a1a1",
    faint="#6a8088",
    line="#0b3d4a",
    line_strong="#1d5563",
    accent=("#268bd2", "#5aa8e6"),
    second="#b58900",
    good=("#859900", "#a8bc2e"),
    bad=("#dc322f", "#f07c79"),
    warn=("#b58900", "#d9b13d"),
    science=("#6c71c4", "#a0a4e3"),
    steel=("#2aa198", "#53c2b9"),
    series=(
        "#268bd2", "#b58900", "#859900", "#6c71c4", "#dc322f",
        "#2aa198", "#cb4b16", "#d33682", "#93a1a1", "#eee8d5",
    ),
    tab_active=(rgba((38, 139, 210), 0.22), rgba((38, 139, 210), 0.55), "#eee8d5"),
    filled=False,
    on_accent="#fdf6e3",
    radii=(5, 4, 8, 6, 4, 6),
    display_fonts=_SEGOE_DISPLAY,
    logo=("#2aa198", "#268bd2", "#6c71c4", "#eee8d5"),
)

AMETHYST = _tinted(
    theme_id="amethyst",
    title="Amethyst",
    description=N_("Ночь в стиле Dracula: фиолетовый с розовым, мятным и cyan"),
    dark=True,
    deep="#1e1f29",
    window="#282a36",
    base="#22232e",
    alt_base="#30323f",
    popup="#343746",
    button="#343746",
    text="#f8f8f2",
    muted="#b9bcd0",
    faint="#7a83a8",
    line="#383a4a",
    line_strong="#4d5066",
    accent=("#bd93f9", "#d2b5fb"),
    second="#ff79c6",
    good=("#50fa7b", "#8afba5"),
    bad=("#ff5555", "#ff9a9a"),
    warn=("#ffb86c", "#ffd3a3"),
    # фиолетовый здесь акцент: наука берёт cyan Dracula
    science=("#8be9fd", "#b5f1fe"),
    steel=("#6272a4", "#a3add2"),
    series=(
        "#bd93f9", "#ff79c6", "#50fa7b", "#8be9fd", "#ffb86c",
        "#ff5555", "#f1fa8c", "#8aa1e0", "#5fd7c4", "#f8f8f2",
    ),
    tab_active=(rgba((189, 147, 249), 0.20), rgba((189, 147, 249), 0.50), "#f8f8f2"),
    filled=True,
    on_accent="#282a36",
    radii=(10, 8, 12, 10, 10, 10),
    display_fonts=("Trebuchet MS", "Segoe UI Variable Display", "Segoe UI"),
    logo=("#ff79c6", "#bd93f9", "#8be9fd", "#f8f8f2"),
)

EMBER = _tinted(
    theme_id="ember",
    title="Ember",
    description=N_("Факел в ночи: тёплый уголь, оранжевый акцент, стальной синий"),
    dark=True,
    deep="#120f0d",
    window="#1b1714",
    base="#151210",
    alt_base="#231e1a",
    popup="#26201c",
    button="#2b2420",
    text="#f0e7de",
    muted="#b8aa9c",
    faint="#85776a",
    line="#2f2823",
    line_strong="#463c34",
    accent=("#ff7a1a", "#ffa257"),
    second="#55b4d9",
    good=("#7cc96f", "#a6dd9c"),
    bad=("#e5484d", "#f59a9d"),
    # оранжевый здесь акцент: предупреждения — жёлтые
    warn=("#f2c94c", "#f7dc8a"),
    science=("#b48ef0", "#d2bbf7"),
    steel=("#7f95ad", "#afbfcf"),
    series=(
        "#ff7a1a", "#55b4d9", "#7cc96f", "#f2c94c", "#b48ef0",
        "#e5484d", "#4fd1b5", "#d99a6c", "#f07fb1", "#c9bfb4",
    ),
    tab_active=(rgba((255, 122, 26), 0.16), rgba((255, 122, 26), 0.50), "#fff3e8"),
    filled=True,
    on_accent="#1b1714",
    radii=(3, 3, 4, 4, 3, 4),
    display_fonts=("Franklin Gothic Medium", "Bahnschrift", "Segoe UI"),
    logo=("#ffc27a", "#ff7a1a", "#e5484d", "#fff3e8"),
)

_HC_CYAN = "#1aebff"  # «выбранный» cyan высококонтрастной темы Windows
_HC_CYAN_RGB = hex_rgb(_HC_CYAN)
CONTRAST = _tinted(
    theme_id="contrast",
    title="Contrast",
    description=N_("Высокий контраст для проектора и уставших глаз: чёрный, белый, cyan"),
    dark=True,
    deep="#000000",
    window="#000000",
    base="#000000",
    alt_base="#141414",
    popup="#0d0d0d",
    button="#000000",
    text="#ffffff",
    muted="#e6e6e6",
    faint="#bdbdbd",
    line="#7a7a7a",
    line_strong="#d0d0d0",
    accent=(_HC_CYAN, _HC_CYAN),
    second="#ffff00",
    good=("#3ff23f", "#3ff23f"),
    bad=("#ff5c5c", "#ff7a7a"),
    warn=("#ffb000", "#ffc23d"),
    science=("#d68cff", "#e2b0ff"),
    steel=("#8fb3ff", "#b3cbff"),
    series=(
        _HC_CYAN, "#ffff00", "#3ff23f", "#ff5c5c", "#d68cff",
        "#ffffff", "#ffb000", "#8fb3ff", "#ff8ad8", "#00ffa8",
    ),
    # контуры вместо заливок: выбранный сегмент, чипы и статусы
    tab_active=("#000000", _HC_CYAN, _HC_CYAN),
    filled=True,
    on_accent="#000000",
    radii=(3, 2, 4, 3, 2, 3),
    text_fonts=("Segoe UI", "Arial"),
    display_fonts=("Segoe UI", "Arial"),
    chip_alpha=(0.0, 1.0),
    status_alpha=(0.0, 1.0),
    logo=(_HC_CYAN, _HC_CYAN, _HC_CYAN, "#ffffff"),
    focus="#ffff00",
    selected=rgba(_HC_CYAN_RGB, 0.30),
    list_selected=rgba(_HC_CYAN_RGB, 0.30),
    text_selection=rgba(_HC_CYAN_RGB, 0.45),
    scroll_hover=_HC_CYAN,
    checked=Look("transparent", _HC_CYAN, _HC_CYAN),
    danger=Look("transparent", "#ff7a7a", "#ff5c5c"),
    hero_gold=Look("transparent", "#ffc23d", "#ffb000"),
    tile_bg="#000000",
)

DAYLIGHT = _tinted(
    theme_id="daylight",
    title="Daylight",
    description=N_("Светлая: белые панели, тёмный текст, синий акцент — для ярких комнат"),
    dark=False,
    deep="#e6eaf0",
    window="#f6f8fa",
    base="#ffffff",
    alt_base="#f0f3f7",
    popup="#ffffff",
    button="#f3f5f8",
    text="#1b2430",
    muted="#4e5a6a",
    faint="#7b8594",
    line="#d5dbe3",
    line_strong="#b4bdc9",
    accent=("#0a66c2", "#0a5bab"),
    second="#d97706",
    good=("#1a7f37", "#17702f"),
    bad=("#cf222e", "#b3121e"),
    warn=("#bf8700", "#875c00"),
    science=("#8250df", "#6b3fc4"),
    steel=("#5a6b80", "#47566a"),
    series=(
        "#0a66c2", "#d97706", "#1a7f37", "#8250df", "#cf222e",
        "#0e8a9c", "#bf3989", "#6e7781", "#8a5a00", "#5b8a00",
    ),
    tab_active=("#ffffff", "#c5cdd8", "#1b2430"),
    filled=True,
    on_accent="#ffffff",
    radii=(6, 4, 8, 6, 4, 8),
    display_fonts=_SEGOE_DISPLAY,
    logo=("#4a94dd", "#0a66c2", "#084a8c", "#1b2430"),
)

SANDSTONE = _tinted(
    theme_id="sandstone",
    title="Sandstone",
    description=N_("Светлая и тёплая, как бумага: песок, бирюза и охра"),
    dark=False,
    deep="#e7dfd0",
    window="#f5f0e6",
    base="#fffcf6",
    alt_base="#f1ebdf",
    popup="#fffcf6",
    button="#efe8db",
    text="#2b2620",
    muted="#5e554a",
    faint="#8a7f71",
    line="#ddd3c2",
    line_strong="#c2b6a1",
    accent=("#0f6e6e", "#0c5e5e"),
    second="#b5651d",
    good=("#4d7c0f", "#3f6a0a"),
    bad=("#b42318", "#9f1d14"),
    warn=("#b7791f", "#7f520f"),
    science=("#7a4fa0", "#673d8c"),
    steel=("#4f6d7a", "#3f5a66"),
    series=(
        "#0f6e6e", "#b5651d", "#4d7c0f", "#7a4fa0", "#b42318",
        "#2f5f9e", "#a67c00", "#8a6d5a", "#b0477a", "#5c5c5c",
    ),
    tab_active=("#fffcf6", "#d3c7b2", "#2b2620"),
    filled=True,
    on_accent="#fffcf6",
    radii=(6, 4, 10, 8, 4, 8),
    display_fonts=("Georgia", "Cambria", "Segoe UI"),
    logo=("#0f6e6e", "#0f6e6e", "#0f6e6e", "#2b2620"),
)

#: Все темы в порядке показа в окне «Тема»: тёмные, затем светлые.
THEMES: dict[str, Theme] = {
    t.id: t
    for t in (
        FORGE, GRAPHITE, STUDIO, FJORD, SOLARIZED, AMETHYST, EMBER, CONTRAST, DAYLIGHT, SANDSTONE,
    )
}


def normalize(theme_id: str | None) -> str:
    """Имя темы из конфига → id из ``THEMES`` (пусто/неизвестное — тема по умолчанию)."""
    wanted = (theme_id or "").strip().lower()
    return wanted if wanted in THEMES else DEFAULT_THEME


def get(theme_id: str | None) -> Theme:
    return THEMES[normalize(theme_id)]
