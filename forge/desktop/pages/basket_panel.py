"""Панель корзины — общая для Калькулятора и Расписания (одна модель ``ctx.basket``).

Поиск предмета, список с прогонами/потоками, «строить ↔ купить готовым», «точный ME/TE»,
вставка фита/списка из буфера EVE и общие настройки расчёта (срок на поток, ME/TE по
умолчанию, оптимизация, объединение общих компонентов, авто-потоки).
"""

from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..widgets import (
    Card,
    ElidedLabel,
    NumberEdit,
    StatusLine,
    Stepper,
    TypeIcon,
    button,
    check,
    clear_layout,
    err_text,
    form_row,
    item_picker,
    label,
    run_bg,
    section,
    set_prop,
    tr,
)


def parse_fit_text(text: str) -> list[tuple[str, int]]:
    """Разбор текста из клиента EVE — два формата:
    1) Ctrl+C в окне фита: «[Корабль, Имя фита]» + по строке на модуль, карго/дроны «Имя xN»;
    2) Ctrl+C по строкам груза/ангара: «Имя<TAB>Кол-во<TAB>Группа…» (кол-во может быть «1,134»).
    Возвращает [(имя, суммарное количество)] в порядке первого появления."""
    counts: dict[str, int] = {}
    first = True
    for raw in text.splitlines():
        line = raw.strip()
        if first:
            first = False
            m = re.match(r"^\[([^,\]]+)\s*,", line)
            if m:
                name = m.group(1).strip()
                if name:
                    counts[name] = counts.get(name, 0) + 1
                continue
        if not line:
            continue
        if "\t" in line:
            cols = line.split("\t")
            name = cols[0].strip()
            try:
                qty = int(re.sub(r"[,\s ]", "", cols[1] if len(cols) > 1 else "") or "1")
            except ValueError:
                qty = 1
            if name:
                counts[name] = counts.get(name, 0) + max(qty, 1)
            continue
        m = re.match(r"^(.*?)\s+x(\d+)$", line, re.IGNORECASE)
        name = (m.group(1) if m else line).strip()
        qty = int(m.group(2)) if m else 1
        if name:
            counts[name] = counts.get(name, 0) + qty
    return list(counts.items())


class PasteFitDialog(QDialog):
    def __init__(self, svc, parent=None):
        super().__init__(parent)
        self.svc = svc
        self.result_rows: list[tuple[int, str, int, bool]] = []
        self.setWindowTitle(tr("Вставить фит из буфера EVE"))
        self.resize(560, 420)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        v.addWidget(label(tr("Скопируй фит (Ctrl+C в окне фита) или список из груза/ангара (Ctrl+C по "
                             "выделенным строкам) и вставь сюда. Имена сопоставляются точно — как их "
                             "отдаёт клиент EVE."), "muted", wrap=True))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(tr("[Ishtar, мой фит]\nDrone Damage Amplifier II\n…\nHammerhead II x5"))
        v.addWidget(self.text, 1)
        self.status = StatusLine()
        v.addWidget(self.status)
        h = QHBoxLayout()
        h.addStretch(1)
        cancel = button(tr("Отмена"))
        cancel.clicked.connect(self.reject)
        self.ok = button(tr("Добавить в корзину"), "plus", "primary")
        self.ok.clicked.connect(self._submit)
        h.addWidget(cancel)
        h.addWidget(self.ok)
        v.addLayout(h)

    def _submit(self):
        parsed = parse_fit_text(self.text.toPlainText())
        if not parsed:
            self.status.show_msg("warn", tr("Не удалось распознать текст фита."))
            return
        self.ok.setEnabled(False)
        names = [n for n, _q in parsed]
        qty = dict(parsed)

        def done(res, err):
            self.ok.setEnabled(True)
            if err:
                self.status.show_msg("err", err_text(err))
                return
            found = [r for r in res if r["type_id"] is not None]
            missing = [r["name"] for r in res if r["type_id"] is None]
            self.result_rows = [(r["type_id"], r["name"], qty.get(r["name"], 1), r["buildable"]) for r in found]
            if missing:
                self.status.show_msg("warn", tr("Добавлено позиций: {n} · не найдено: {names}",
                                                n=len(found), names=", ".join(missing)))
                if found:
                    self.ok.setText(tr("Готово — добавить найденное"))
                    self.ok.clicked.disconnect()
                    self.ok.clicked.connect(self.accept)
                return
            self.accept()
        run_bg(lambda: self.svc.resolve_names(names), done)


class BasketRow(QFrame):
    """Строка корзины: иконка, имя, строить/купить, прогоны, потоки, 🎯, ×; под ней — ME/TE."""

    def __init__(self, panel: BasketPanel, item, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.tid = item.type_id
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(3)
        h = QHBoxLayout()
        h.setSpacing(6)
        h.addWidget(TypeIcon(item.type_id, 22))
        self.name = ElidedLabel(item.name)
        self.name.setFont(theme.font(13, 500))
        h.addWidget(self.name, 1)
        self.mode = button("", variant="toggle")
        self.mode.setFixedWidth(64)
        self.mode.clicked.connect(self._toggle_buy)
        h.addWidget(self.mode)
        self.runs = NumberEdit(item.runs, integer=True, width=54, compact=True)
        self.runs.editingFinished.connect(self._runs)
        h.addWidget(self.runs)
        self.streams = NumberEdit(item.streams, integer=True, width=44, compact=True)
        self.streams.editingFinished.connect(self._streams)
        h.addWidget(self.streams)
        self.exact = button("ME", variant="toggle")
        self.exact.setFixedWidth(34)
        self.exact.clicked.connect(self._toggle_exact)
        h.addWidget(self.exact)
        rm = button("", "x", "ghost", tip=tr("Убрать из корзины"), icon_size=12)
        rm.setFixedSize(24, 24)
        rm.clicked.connect(lambda: self.panel.basket.remove(self.tid))
        h.addWidget(rm)
        v.addLayout(h)
        self.me_row = QWidget()
        mh = QHBoxLayout(self.me_row)
        mh.setContentsMargins(28, 0, 0, 0)
        mh.setSpacing(6)
        mh.addWidget(label(tr("точный ME"), "faint"))
        self.me = Stepper(0, 10, 0, width=40)
        self.me.changed.connect(lambda val: self.panel.basket.update(self.tid, me=val))
        mh.addWidget(self.me)
        mh.addWidget(label("TE", "faint"))
        self.te = Stepper(0, 20, 0, width=40)
        self.te.changed.connect(lambda val: self.panel.basket.update(self.tid, te=val))
        mh.addWidget(self.te)
        mh.addStretch(1)
        v.addWidget(self.me_row)
        self.sync(item)

    def sync(self, item):
        buy_only = item.buildable is False
        self.mode.setEnabled(not buy_only)
        if buy_only:
            self.mode.setText(tr("покупка"))
            self.mode.setToolTip(tr("Нет чертежа — только покупка (считается как материал: где дешевле)"))
            set_prop(self.mode, "state", "buy")
        elif item.buy:
            self.mode.setText(tr("купить"))
            self.mode.setToolTip(tr("Решение: КУПИТЬ готовым (в отчёте уйдёт в закупку). Клик — строить."))
            set_prop(self.mode, "state", "buy")
        else:
            self.mode.setText(tr("строить"))
            self.mode.setToolTip(tr("Решение: СТРОИТЬ. Клик — купить готовым."))
            set_prop(self.mode, "state", "build")
        no_bp = buy_only or item.buy
        self.streams.setEnabled(not no_bp)
        self.runs.setToolTip(tr("Количество к покупке") if no_bp else tr("Прогонов на этот предмет"))
        self.streams.setToolTip(tr("Покупка — потоки не применяются") if no_bp
                                else tr("Параллельных потоков (джобов) для этого предмета"))
        self.exact.setEnabled(not no_bp)
        set_prop(self.exact, "state", "exact" if item.exact else "")
        self.exact.setToolTip(tr("Точный ME/TE включён — клик вернёт авто (свой чертёж/дефолт)") if item.exact
                              else tr("Задать точный ME/TE этого товара (вместо своего чертежа/дефолта). "
                                      "Не действует на Т2 из инвенты — там ME/TE задаёт декриптор."))
        self.me_row.setVisible(bool(item.exact) and not no_bp)
        if not self.runs.hasFocus():
            self.runs.setValue(item.runs)
        if not self.streams.hasFocus():
            self.streams.setValue(item.streams)
        self.me.spin.blockSignals(True)
        self.te.spin.blockSignals(True)
        self.me.setValue(item.me if item.me is not None else self.panel.basket.opts.me)
        self.te.setValue(item.te if item.te is not None else self.panel.basket.opts.te)
        self.me.spin.blockSignals(False)
        self.te.spin.blockSignals(False)

    def _toggle_buy(self):
        it = self.panel.basket.find(self.tid)
        if it is not None:
            self.panel.basket.update(self.tid, buy=not it.buy)

    def _toggle_exact(self):
        it = self.panel.basket.find(self.tid)
        if it is None:
            return
        o = self.panel.basket.opts
        self.panel.basket.update(self.tid, exact=not it.exact,
                                 me=it.me if it.me is not None else o.me,
                                 te=it.te if it.te is not None else o.te)

    def _runs(self):
        self.panel.basket.update(self.tid, runs=max(1, self.runs.int_value(1)))

    def _streams(self):
        self.panel.basket.update(self.tid, streams=max(1, self.streams.int_value(1)))


class BasketPanel(Card):
    """Карточка «Корзина» + настройки расчёта. Кнопки действий добавляет вкладка в ``action_box``."""

    options_changed = Signal()

    def __init__(self, ctx, show_build_toggle: bool = True, parent=None):
        super().__init__(tr("Корзина"), "cart", "cyan")
        self.ctx = ctx
        self.basket = ctx.basket
        self.setFixedWidth(410)
        self.count = label("", "meta")
        self.head_right.addWidget(self.count)
        clear = button("", "trash", "ghost", tip=tr("Очистить корзину"), icon_size=13)
        clear.clicked.connect(self._clear)
        self.head_right.addWidget(clear)

        self.search = item_picker(ctx.svc, tr("Добавить предмет в корзину…"))
        self.search.picked.connect(lambda r: self.basket.add(r["type_id"], r["name"], 1, r.get("buildable", True)))
        self.body.addWidget(self.search)
        paste = button(tr("Вставить фит / список из буфера EVE"), "copy")
        paste.clicked.connect(self._paste)
        self.body.addWidget(paste)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        hdr.addSpacing(28)
        hdr.addWidget(label(tr("ПРЕДМЕТ"), "faint"), 1)
        for text, w in (("", 64), (tr("ПРОГ."), 54), (tr("ПОТ."), 44), ("", 34), ("", 24)):
            lb = label(text, "faint")
            lb.setFixedWidth(w)
            hdr.addWidget(lb)
        self.hdr = QWidget()
        self.hdr.setLayout(hdr)
        self.body.addWidget(self.hdr)
        self.list_host = QWidget()
        self.list = QVBoxLayout(self.list_host)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(2)
        self.body.addWidget(self.list_host)
        self.empty = label(tr("Корзина пуста — найди корабль/модуль выше или вставь фит."), "faint", wrap=True)
        self.body.addWidget(self.empty)

        self.body.addWidget(section(tr("Настройки расчёта")))
        self.max_days = NumberEdit(None, tr("нет"), width=80, compact=True)
        self.max_days.editingFinished.connect(self._opts)
        self.body.addLayout(form_row(
            tr("Макс. дней на поток (компоненты)"), self.max_days,
            tr("Дробит ПОД-компоненты (реакции, детали) так, чтобы каждый их джоб был ≤ N дней — "
               "волнами. Меняет расход материалов (ceil на каждый джоб). Верхний продукт дробится "
               "полем «потоки». Пусто — под-компоненты в 1 поток.")))
        self.me = Stepper(0, 10, 0)
        self.me.changed.connect(lambda _v: self._opts())
        self.body.addLayout(form_row(tr("ME, если нет своего чертежа"), self.me))
        self.te = Stepper(0, 20, 0)
        self.te.changed.connect(lambda _v: self._opts())
        self.body.addLayout(form_row(
            tr("TE, если нет своего чертежа"), self.te,
            tr("Срок для чертежей, которых нет ни у одного персонажа И которые не добываются "
               "инвентой. Свой чертёж — TE берётся с копии; инвента — TE итоговой BPC от декриптора.")))
        self.build = check(tr("Оптимизация: сам решает строить/купить"), True,
                           tr("По каждому компоненту: строить или купить — что дешевле"))
        self.build.toggled.connect(lambda _v: self._opts())
        self.body.addWidget(self.build)
        if not show_build_toggle:
            self.build.hide()
        self.consolidate = check(tr("Объединять общие компоненты"), True,
                                 tr("Одинаковый под-компонент в разных ветках (в т.ч. в разных товарах "
                                    "корзины) строить ОДНОЙ общей постройкой — экономия слотов и "
                                    "материалов (округление считается один раз на суммарный спрос)"))
        self.consolidate.toggled.connect(lambda _v: self._opts())
        self.body.addWidget(self.consolidate)
        self.auto = check(tr("Авто-потоки для объединённых"), False,
                          tr("Объединение теряет параллелизм: общая постройка идёт одним джобом. Эта "
                             "галка даёт ей столько потоков, сколько веток в неё слито."))
        self.auto.toggled.connect(lambda _v: self._opts())
        self.body.addWidget(self.auto)
        self.comp_note = label("", "faint", wrap=True)
        self.body.addWidget(self.comp_note)

        self.action_box = QVBoxLayout()
        self.action_box.setSpacing(8)
        self.body.addLayout(self.action_box)
        self.status = StatusLine()
        self.body.addWidget(self.status)
        self.body.addStretch(1)

        self._rows: dict[int, BasketRow] = {}
        self._ids: list[int] = []
        self.basket.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self):
        items = self.basket.items
        ids = [i.type_id for i in items]
        if ids != self._ids:
            clear_layout(self.list)
            self._rows = {}
            for it in items:
                row = BasketRow(self, it)
                self._rows[it.type_id] = row
                self.list.addWidget(row)
            self._ids = ids
        else:
            for it in items:
                self._rows[it.type_id].sync(it)
        self.empty.setVisible(not items)
        self.hdr.setVisible(bool(items))
        self.count.setText(tr("{n} поз.", n=len(items)) if items else "")
        o = self.basket.opts
        for w in (self.me.spin, self.te.spin, self.build, self.consolidate, self.auto):
            w.blockSignals(True)
        if not self.max_days.hasFocus():
            self.max_days.setValue(o.max_days)
        self.me.setValue(o.me)
        self.te.setValue(o.te)
        self.build.setChecked(o.build)
        self.consolidate.setChecked(o.consolidate)
        self.auto.setChecked(o.auto_streams)
        self.auto.setEnabled(o.consolidate)
        for w in (self.me.spin, self.te.spin, self.build, self.consolidate, self.auto):
            w.blockSignals(False)
        n = len(o.buy_components)
        self.comp_note.setText(tr("Под-компонентов переключено «строить → купить»: {n} (клик по "
                                  "«источник» в дереве Калькулятора).", n=n) if n else "")
        self.comp_note.setVisible(bool(n))

    def _opts(self):
        md = self.max_days.value()
        self.basket.set_opts(max_days=md if md and md > 0 else None, me=self.me.value(),
                             te=self.te.value(), build=self.build.isChecked(),
                             consolidate=self.consolidate.isChecked(),
                             auto_streams=self.auto.isChecked())
        self.options_changed.emit()

    def _clear(self):
        if self.basket.items:
            self.basket.clear()

    def _paste(self):
        dlg = PasteFitDialog(self.ctx.svc, self)
        if dlg.exec() and dlg.result_rows:
            self.basket.add_many(dlg.result_rows)
            self.status.show_msg("ok", tr("Добавлено из буфера: {n} поз.", n=len(dlg.result_rows)), 5000)

    def add_action(self, btn):
        self.action_box.addWidget(btn)
        return btn
