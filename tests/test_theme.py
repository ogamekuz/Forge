"""Темы пульта (forge.desktop.themes / theme / theme_dialog): каждая тема полна и читаема, Forge —
тема по умолчанию в цветах EVE, смена темы на ходу пересобирает окно и сохраняется в
``[ui] theme``, окно «Тема» показывает любую тему при любой запущенной.

Окна — как в ``test_desktop.py`` (offscreen, без сети, сервер отчётов не поднимается).
"""

from __future__ import annotations

import dataclasses
import os
import re
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FORGE_NO_ICON_NET"] = "1"
pytest.importorskip("PySide6")

from test_desktop import qapp, wait_until, win  # noqa: F401 — фикстуры окна

from forge import config as config_mod
from forge.desktop import theme, themes
from forge.planner.instructions import GANTT_COLORS

THEME_IDS = list(themes.THEMES)
TONES = ("cyan", "gold", "green", "red", "violet", "steel", "muted")
KINDS = ("ok", "err", "info", "warn")
HEX = re.compile(r"#[0-9a-fA-F]{6}")


@pytest.fixture(autouse=True)
def _forge_theme_after(qapp):  # noqa: F811
    """Тема — на весь процесс: каждый тест оставляет после себя тему по умолчанию."""
    yield
    if theme.active().id != themes.DEFAULT_THEME:
        theme.activate(themes.DEFAULT_THEME)
        theme.apply(qapp)


def _luminance(color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = themes.hex_rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """Контраст двух цветов по WCAG (1..21)."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_ten_themes_forge_first_and_default():
    assert THEME_IDS == ["forge", "graphite", "studio", "fjord", "solarized", "amethyst", "ember",
                         "contrast", "daylight", "sandstone"]
    assert themes.DEFAULT_THEME == "forge" and theme.active().id == "forge"
    assert [t.id for t in themes.THEMES.values() if not t.dark] == ["daylight", "sandstone"]
    assert themes.normalize(" Graphite ") == "graphite"
    assert themes.normalize("нет такой") == "forge" and themes.normalize(None) == "forge"


def test_forge_theme_is_eve_space():
    """Тема по умолчанию — Forge: цвета EVE, эффекты, скобы, цвета Ганта как в
    HTML-инструкции расписания."""
    t = themes.FORGE
    assert (t.deep, t.window, t.text) == ("#04070b", "#0b1118", "#dde6ef")
    assert (t.accent, t.tone_color("gold"), t.tone_color("green"), t.tone_color("red")) == (
        "#3fb8e0", "#e8a93a", "#4cc38a", "#e5534b")
    assert t.effects and t.corner_brackets and len(t.brand) == 5
    assert list(t.series) == GANTT_COLORS
    assert (t.radius_button, t.radius_field, t.radius_panel) == (13, 10, 16)
    assert [x.id for x in themes.THEMES.values() if x.effects or x.corner_brackets] == ["forge"]


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_theme_is_complete(theme_id, qapp):  # noqa: F811
    from PySide6.QtGui import QColor

    t = themes.THEMES[theme_id]
    assert t.id == theme_id and t.title and t.description
    for tone in (*TONES, *themes.TONE_ALIASES):
        rgb, text = t.tones[tone]
        assert len(rgb) == 3 and HEX.fullmatch(text)
    assert set(TONES) <= set(t.chips)
    for kind in KINDS:
        assert t.statuses[kind].fg and t.pills[kind].fg and HEX.fullmatch(t.dots[kind])
    assert len(t.series) >= 8 and all(HEX.fullmatch(c) for c in t.series)
    assert t.text_fonts and t.display_fonts and t.mono_fonts
    # простые цвета — настоящие #rrggbb: их берут QColor и заголовок окна Windows
    plain = [t.deep, t.window, *t.backdrop, t.base, t.alt_base, t.popup, t.button, t.highlight, t.text,
             t.muted, t.faint, t.on_series, t.check_mark, *t.progress, *t.brand, *t.logo_stops,
             *t.logo_spark, *t.titlebar]
    assert all(HEX.fullmatch(c) for c in plain), [c for c in plain if not HEX.fullmatch(c)]
    # кисти QSS (rgba/градиент/имя) — те, что рисуются своей отрисовкой, разбираются в цвет
    for brush in (t.panel_bg, t.tile_bg, t.line, t.line_strong, t.list_selected, t.tab_active.bg,
                  t.hero.bg, t.hero_gold.bg, t.primary.bg):
        assert theme.brush_color(brush).isValid(), brush
    assert all(QColor(c).isValid() for c in plain)

    qss = theme.stylesheet(t)
    assert "None" not in qss and "{" in qss and "url(" in qss
    assert qss.count("{") == qss.count("}")
    assert f"color: {t.text};" in qss and f'font-family: "{theme.text_family(t)}"' in qss
    for name in ("check.svg", "chevron-down.svg", "branch-open.svg"):  # картинки — в цветах темы
        assert os.path.exists(theme.asset(name, t))
    assert t.check_mark in open(theme.asset("check.svg", t), encoding="utf-8").read()
    pal = theme.palette(t)
    assert pal.color(pal.ColorRole.Window).name() == t.window.lower()
    assert pal.color(pal.ColorRole.Text).name() == t.text.lower()
    assert not theme.logo_pixmap(40, 1.0, spec=t).isNull()


@pytest.mark.parametrize("theme_id", THEME_IDS)
def test_theme_text_is_readable(theme_id):
    """Текст читается на фоне окна, панели-подложки и всплывающих окон; подписи на полосах Ганта
    — на цветах рядов."""
    t = themes.THEMES[theme_id]
    grounds = (t.window, t.deep, t.popup)
    assert min(contrast(t.text, g) for g in grounds) >= 7
    assert min(contrast(t.muted, g) for g in grounds) >= 4.5
    assert min(contrast(t.faint, g) for g in grounds) >= 2.8
    for tone in TONES[:-1]:
        assert min(contrast(t.tone_text(tone), g) for g in grounds) >= 4.5, tone
    assert min(contrast(c, t.on_series) for c in t.series) >= 3


def test_activate_rebinds_module_tokens(qapp):  # noqa: F811
    daylight = themes.DAYLIGHT
    assert theme.activate("daylight") is daylight and theme.active() is daylight
    assert (theme.TEXT, theme.BG1, theme.BG_DEEP, theme.CYAN) == (
        daylight.text, daylight.window, daylight.deep, daylight.accent)
    assert theme.SERIES == daylight.series and theme.COOL == daylight.wash
    assert theme.tone_text("green") == daylight.tone_text("green")
    assert theme.tone_text("green", themes.FORGE) == "#8ee0b6"           # spec — тема вместо активной
    assert theme.rgba("cyan", 0.5) == themes.rgba(daylight.accent_rgb, 0.5)
    assert theme.font(13).family() == theme.text_family(daylight)
    assert theme.activate("нет такой").id == "forge" and theme.TEXT == "#dde6ef"


def test_themes_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        themes.FORGE.text = "#ffffff"  # type: ignore[misc]


def test_window_switches_theme_on_the_spot_and_saves_it(win, qapp, tmp_path, monkeypatch):  # noqa: F811
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    from forge.desktop.main_window import TABS

    errors: list[str] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, e, tb: errors.append(f"{t.__name__}: {e}"))
    path = tmp_path / "forge.toml"
    assert theme.active().id == "forge" and config_mod.load(str(path)).ui.theme == "forge"
    win.go("plan")
    old_pages = dict(win.pages)

    win.set_theme("daylight")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert theme.active().id == "daylight"
    assert f"color: {themes.DAYLIGHT.text};" in qapp.styleSheet()
    assert qapp.palette().color(qapp.palette().ColorRole.Window).name() == themes.DAYLIGHT.window
    assert win.pages["plan"] is not old_pages["plan"]                  # страницы собраны заново,
    assert not any(shiboken6.isValid(p) for p in old_pages.values())   # прежние удалены
    assert win.stack.currentWidget() is win.pages["plan"]              # и открыта та же вкладка
    assert wait_until(qapp, lambda: config_mod.load(str(path)).ui.theme == "daylight", 10)

    win.basket.clear()
    win.basket.add(2000, "Widget", 2, True)
    for key, _t, _i in TABS:                                           # каждая вкладка живёт в новой теме
        win.go(key)
        wait_until(qapp, lambda: False, 0.3)
    win.go("plan")
    plan = win.pages["plan"]
    plan.run()
    assert wait_until(qapp, lambda: bool(plan.gantt.colors()), 20)
    assert [c.name() for c in plan.gantt.colors().values()] == [themes.DAYLIGHT.series[0]]  # ряды темы
    assert not win.grab().isNull()

    win.set_theme("daylight")                                          # та же тема — ничего не делает
    assert win.pages["plan"] is plan
    win.set_theme("graphite")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert theme.active().id == "graphite" and win.pages["plan"] is not plan
    win.hub.config_changed.emit()                                      # прежние страницы никого не будят
    win.basket.changed.emit()
    wait_until(qapp, lambda: False, 0.4)
    assert wait_until(qapp, lambda: config_mod.load(str(path)).ui.theme == "graphite", 10)
    assert not errors, "\n".join(errors)


def test_window_starts_in_configured_theme(tmp_path, qapp):  # noqa: F811
    from test_desktop import TOML, _seed

    from forge import storage
    from forge.desktop.main_window import MainWindow, config_ui

    path = tmp_path / "forge.toml"
    path.write_text(TOML + '\n[ui]\ntheme = "Ember"\n', encoding="utf-8")
    conn = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(conn)
    _seed(conn)
    conn.close()
    assert config_ui(str(path)) == ("ru", "Ember")
    w = MainWindow(str(path), start_server=False)
    try:
        assert theme.active().id == "ember"
        assert f"color: {themes.EMBER.text};" in qapp.styleSheet()
    finally:
        w.close()
        qapp.processEvents()
    assert config_ui(str(tmp_path / "нет.toml")) == ("ru", "forge")    # нет конфига — по умолчанию


def test_theme_dialog_previews_any_theme_and_returns_the_choice(win, qapp):  # noqa: F811
    import shiboken6

    from forge.desktop.theme_dialog import ThemeDialog, ThemePreview

    win.btn_theme.click()                                              # кнопка-палитра в шапке
    dlg = next(d for d in win.findChildren(ThemeDialog))
    assert list(dlg.rows) == THEME_IDS and dlg.chosen_id() == "forge"
    assert dlg.rows["forge"].badge is not None and dlg.rows["studio"].badge is None
    assert isinstance(dlg.preview, ThemePreview) and dlg.preview.spec() is themes.FORGE
    assert dlg.apply_btn.text() == "Оставить эту тему"
    for theme_id in THEME_IDS:                                         # каждая тема — в предпросмотре
        dlg.select(theme_id)
        qapp.processEvents()
        spec = themes.THEMES[theme_id]
        assert dlg.preview.spec() is spec and dlg.title.text() == spec.title
        assert f"color: {spec.text};" in dlg.preview.styleSheet()
        assert not dlg.preview.grab().isNull()
    assert theme.active().id == "forge"                                # приложение осталось в своей теме
    assert f"color: {themes.FORGE.text};" in qapp.styleSheet()
    assert dlg.kind.text() == "Светлая" and dlg.apply_btn.text() == "Применить"

    dlg.select("fjord")
    dlg.apply_btn.click()                                              # «Применить» → окно в новой теме
    qapp.processEvents()
    assert theme.active().id == "fjord"
    assert not shiboken6.isValid(dlg) or not dlg.isVisible()           # окно «Тема» закрылось

    chosen: list[str] = []
    again = ThemeDialog(theme.active().id, win)
    again.chosen.connect(chosen.append)
    again.accept()                                                     # выбрана текущая — менять нечего
    assert chosen == [] and theme.active().id == "fjord"
