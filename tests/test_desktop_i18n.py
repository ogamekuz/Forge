"""Пульт на английском: переключатель RUS/ENG в шапке пересобирает окно на месте, язык
сохраняется в ``[ui] lang``, и ни одна видимая надпись не остаётся русской.

Окна — как в ``test_desktop.py`` (offscreen, без сети, сервер отчётов не поднимается).
"""

from __future__ import annotations

import os
import re
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FORGE_NO_ICON_NET"] = "1"
pytest.importorskip("PySide6")

from test_desktop import qapp, wait_until, win  # noqa: F401 — фикстуры окна

from forge import config as config_mod
from forge import i18n

CYR = re.compile(r"[А-Яа-яЁё]")


def widget_texts(root) -> list[str]:
    """Все надписи окна: подписи, кнопки, подсказки, плейсхолдеры, заголовки и ячейки таблиц
    и деревьев, пункты списков — в т.ч. у скрытых сейчас виджетов (другие вкладки, статусы)."""
    from PySide6.QtWidgets import (
        QAbstractButton,
        QComboBox,
        QLabel,
        QLineEdit,
        QListWidget,
        QTableWidget,
        QTreeWidget,
        QWidget,
    )

    out: list[str] = [root.windowTitle()]
    for w in [root, *root.findChildren(QWidget)]:
        out.append(w.toolTip())
        if isinstance(w, (QLabel, QAbstractButton)):
            out.append(w.text())
        elif isinstance(w, QLineEdit):
            out.append(w.placeholderText())
        elif isinstance(w, QComboBox):
            out += [w.itemText(i) for i in range(w.count())]
        elif isinstance(w, QListWidget):
            out += [w.item(i).text() for i in range(w.count())]
        elif isinstance(w, QTableWidget):
            for c in range(w.columnCount()):
                h = w.horizontalHeaderItem(c)
                out += [h.text(), h.toolTip()] if h else []
                for r in range(w.rowCount()):
                    it = w.item(r, c)
                    out += [it.text(), it.toolTip()] if it else []
        elif isinstance(w, QTreeWidget):
            h = w.headerItem()
            out += [h.text(c) for c in range(w.columnCount())]
            stack = [w.topLevelItem(i) for i in range(w.topLevelItemCount())]
            while stack:
                it = stack.pop()
                out += [it.text(c) for c in range(w.columnCount())]
                out += [it.toolTip(c) for c in range(w.columnCount())]
                stack += [it.child(i) for i in range(it.childCount())]
    return [t for t in out if t]


def test_language_switch_rebuilds_window_in_english(win, qapp, tmp_path, monkeypatch):  # noqa: F811
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    from forge.desktop.main_window import TABS

    errors: list[str] = []
    monkeypatch.setattr(sys, "excepthook", lambda t, e, tb: errors.append(f"{t.__name__}: {e}"))
    win.go("calc")
    old_pages = dict(win.pages)
    assert win.tabs.button("dash").text() == "Обзор"

    win.lang.button("en").click()
    # deleteLater срабатывает в цикле событий; тест без exec() — удаляем прежние страницы явно
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert i18n.lang() == "en"
    assert win.pages["calc"] is not old_pages["calc"]                  # страницы собраны заново,
    assert not any(shiboken6.isValid(p) for p in old_pages.values())   # прежние удалены
    assert win.stack.currentWidget() is win.pages["calc"]              # и открыта та же вкладка
    assert win.tabs.button("dash").text() == "Overview" and win.tagline.text() == "EVE Online industry"
    assert win.lang.value() == "en"
    assert win.pages["dash"].btn_public.text() == "Jita market && indices"  # «&» — не метка клавиши
    path = tmp_path / "forge.toml"
    assert wait_until(qapp, lambda: config_mod.load(str(path)).ui.lang == "en", 10)

    for key, _t, _i in TABS:                                           # каждая вкладка грузит данные
        win.go(key)
        wait_until(qapp, lambda: False, 0.4)
    assert wait_until(qapp, lambda: bool(win.hub.snapshot), 5)
    win.hub.status.emit(win.hub.snapshot)
    wait_until(qapp, lambda: False, 0.3)
    russian = sorted({t for t in widget_texts(win) if CYR.search(t)})
    assert not russian, "\n".join(russian)

    # сигналы, на которые были подписаны прежние (удалённые) страницы, никого мёртвого не будят
    win.hub.data_changed.emit()
    win.hub.config_changed.emit()
    win.hub.reports_changed.emit()
    win.basket.changed.emit()
    wait_until(qapp, lambda: False, 0.5)
    assert not errors, "\n".join(errors)

    win.lang.button("ru").click()
    assert i18n.lang() == "ru" and win.tabs.button("dash").text() == "Обзор"
    assert wait_until(qapp, lambda: config_mod.load(str(path)).ui.lang == "ru", 10)


def test_window_starts_in_configured_language(tmp_path, qapp):  # noqa: F811
    from test_desktop import TOML, _seed

    from forge import storage
    from forge.desktop.main_window import MainWindow

    (tmp_path / "forge.toml").write_text(TOML + '\n[ui]\nlang = "en"\n', encoding="utf-8")
    conn = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(conn)
    _seed(conn)
    conn.close()
    w = MainWindow(str(tmp_path / "forge.toml"), start_server=False)
    try:
        assert i18n.lang() == "en" and w.lang.value() == "en"
        assert w.tabs.button("settings").text() == "Settings"
        index = (tmp_path / "reports" / "index.html").read_text(encoding="utf-8")
        assert not CYR.search(index)                                    # список отчётов — тоже
    finally:
        w.close()
        qapp.processEvents()
