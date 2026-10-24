"""Вкладка «Что строить»: ТОП по ROI/ISK-час/ликвидности (общий, по разделам рынка и по группам
из настроек), «Дешевле купить, чем строить», «Что построить из остатков склада». Слева — дерево
«Что строю» (разделы рынка с галками, ``market_tree.MarketTreePanel``): снятые разделы не
предлагаются ни в одном из трёх расчётов. «Запреты» (исключённые предметы/группы/категории) —
в «Настройки → Рекомендации»."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..widgets import (
    N_,
    Busy,
    Card,
    ChipList,
    Col,
    DataTable,
    NumberEdit,
    Page,
    Segmented,
    StatusLine,
    button,
    check,
    clear_layout,
    err_text,
    fmt_isk_short,
    fmt_pct,
    group_picker,
    label,
    run_bg,
    scroll_page,
    section,
    tr,
)
from .market_tree import MarketTreePanel

# «Разбивка» ТОП по разделам рынка: уровень дерева → подпись (пример — в подсказке).
_SPLITS = ((0, N_("только общий ТОП")), (1, N_("по разделам — Ships, Drones…")),
           (2, N_("по подразделам — Frigates, Hybrid Charges…")),
           (3, N_("по группам — Standard / Advanced Frigates, Rigs…")))
# Автопересчёт после смены галок «Что строю» — только если прошлый расчёт был быстрым, сек.
_AUTO_RERUN_MAX_S = 15.0


def _section_col() -> Col:
    # Фиксированная ширина (тянется мышью): длинный путь не отъедает место у названия предмета —
    # обрезается «…», целиком — в подсказке.
    return Col("section", tr("Раздел"), width=180, tip=lambda _v, r: r.get("section_path") or "")


def _field(title: str, widget) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(3)
    v.addWidget(label(title, "label"))
    v.addWidget(widget)
    return w


def _tone_sign(v, _r=None):
    return None if v is None else ("green" if v >= 0 else "red")


# «Из остатков», колонка «Чертёж»: источник чертежа продукта → (подпись, тон, подсказка).
_BP_OWN = (N_("свой"), "green", N_("Чертёж есть у персонажей — можно строить сейчас."))
_BP_NONE = (N_("нет"), "red", N_("Чертежа нет, и его цена в расчёте — 0: сначала добудь чертёж (или задай "
                                 "цену в «Настройки → Чертежи»)."))
_BP_LABEL = {
    "owned_bpo": _BP_OWN,
    "owned_bpc": _BP_OWN,
    "owned_bpc_insufficient": (N_("свой"), "gold", N_("Своя копия есть, но её прогонов на всю партию не "
                                                      "хватит.")),
    "invention": (N_("инвента"), "gold", N_("Чертежа нет: в себестоимости — цена инвенты.")),
    "manual": (N_("вручную"), "gold", N_("Цена чертежа задана вручную («Настройки → Чертежи»).")),
}


class GroupFilter(QWidget):
    """Фильтр по EVE-группам: чипы + поиск. ``ids()`` — выбранные group_id."""

    def __init__(self, svc, placeholder: str, empty: str, parent=None):
        super().__init__(parent)
        self._groups: dict[int, str] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.chips = ChipList(empty, icon="filter")
        self.chips.removed.connect(self._remove)
        v.addWidget(self.chips)
        self.picker = group_picker(svc, placeholder)
        self.picker.picked.connect(self._add)
        v.addWidget(self.picker)

    def _add(self, g):
        self._groups[int(g["group_id"])] = g["name"]
        self._render()

    def _remove(self, gid):
        self._groups.pop(int(gid), None)
        self._render()

    def _render(self):
        self.chips.set_items([(gid, name) for gid, name in self._groups.items()])

    def ids(self) -> list[int]:
        return list(self._groups)


class RecommendPage(Page):
    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        top = QHBoxLayout()
        self.seg = Segmented([("top", tr("ТОП «что строить»"), "star"), ("cheaper", tr("Дешевле купить"), "cart"),
                              ("stock", tr("Из остатков склада"), "boxes")], small=True)
        self.seg.changed.connect(self._switch)
        top.addWidget(self.seg)
        top.addStretch(1)
        self.basket_note = label("", "faint")
        top.addWidget(self.basket_note)
        v.addLayout(top)
        self.sections = MarketTreePanel(ctx)
        self.sections.setMinimumWidth(300)
        self.sections.changed.connect(self._sections_changed)
        self.stack = QStackedWidget()
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)
        split.addWidget(self.sections)
        split.addWidget(self.stack)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 1040])
        v.addWidget(split, 1)
        self._liq: dict = {}   # откуда «Объём/сут» (svc.liquidity_info) — подсказки колонок
        # Расчёты вкладок: сколько длился последний (None — ещё не считали), какие идут сейчас и
        # какие пересчитать сразу после текущего (галки «Что строю» сменились во время расчёта).
        self._took: dict[str, float | None] = {"top": None, "cheaper": None, "stock": None}
        self._running: set[str] = set()
        self._rerun: set[str] = set()
        cfg = self.svc.load_cfg()
        self.stack.addWidget(self._build_top(cfg))
        self.stack.addWidget(self._build_cheaper(cfg))
        self.stack.addWidget(self._build_stock(cfg))
        ctx.basket.changed.connect(self._basket_note)
        ctx.hub.config_changed.connect(self._load_liquidity)
        ctx.hub.data_changed.connect(self._load_liquidity)
        self._basket_note()

    def on_show(self):
        self._load_liquidity()
        self.sections.ensure_loaded()

    def _switch(self, key):
        self.stack.setCurrentIndex({"top": 0, "cheaper": 1, "stock": 2}[key])

    # ------------------------------------------------------------ «Что строю»
    def _runner(self, key):
        return {"top": self._run_top, "cheaper": self._run_cheaper, "stock": self._run_stock}[key]

    def _sections_changed(self):
        """Галки разделов сохранены: открытый расчёт, если он был быстрым, — пересчитать сразу;
        остальные — пометить устаревшими (кнопка расчёта пересчитает)."""
        current = self.seg.value()
        for key, took in self._took.items():
            if took is None:
                continue
            if key == current and took <= _AUTO_RERUN_MAX_S:
                if key in self._running:
                    self._rerun.add(key)
                else:
                    self._runner(key)()
            else:
                self._status_of(key).show_msg("info", tr("Разделы «Что строю» изменились — результат ниже "
                                                         "устарел, пересчитай."))

    def _status_of(self, key) -> StatusLine:
        return {"top": self.t_err, "cheaper": self.c_err, "stock": self.s_err}[key]

    def _started(self, key) -> float:
        self._running.add(key)
        return time.monotonic()

    def _finished(self, key, t0: float, ok: bool):
        self._running.discard(key)
        if ok:
            self._took[key] = time.monotonic() - t0
        if key in self._rerun:
            self._rerun.discard(key)
            self._runner(key)()

    # ------------------------------------------------------------ ликвидность
    def _load_liquidity(self):
        def done(res, err):
            if err or not res:
                return
            self._liq = res
            text = res["text"] + (f"\n⚠ {res['warning']}" if res.get("warning") else "")
            for lb in (self.t_liq, self.s_liq):
                lb.setText(text)
            self.c_liq.setText(res["text"] + " " + res["cheaper_note"]
                               + (f"\n⚠ {res['warning']}" if res.get("warning") else ""))
            self._tip_volume_header(self.c_table, "daily_volume")
        run_bg(self.svc.liquidity_info, done)

    def _liq_tip(self) -> str:
        return (self._liq.get("text") or "") + " " + tr("Источник — «Настройки → Рекомендации».")

    def _tip_volume_header(self, table: DataTable, key: str):
        """Подсказка у заголовка колонки ``key`` («Объём/сут»): откуда цифра."""
        table.set_header_tip(key, self._liq_tip())

    def _basket_note(self):
        n = len(self.ctx.basket.items)
        self.basket_note.setText(tr("В корзине: {n} — «Калькулятор» / «Расписание»", n=n) if n else "")

    def _add(self, row):
        name = row.get("name", "")
        if self.ctx.basket.add(row["product_type_id"], name, row.get("runs") or 1, True):
            self.ctx.flash(tr("В корзину: {name} ×{runs}", name=name, runs=row.get("runs") or 1), ms=4000)
        else:
            self.ctx.flash(tr("{name} уже в корзине", name=name), ms=3000)

    # ------------------------------------------------------------------ ТОП
    def _reco_cols(self, with_section: bool = True):
        cols = [
            Col("name", tr("Предмет"), icon_key="product_type_id", stretch=True),
            Col("roi", "ROI", lambda v, _r: fmt_pct(v), "right", tone=_tone_sign),
            Col("isk_per_hour", tr("ISK/час"), lambda v, _r: fmt_isk_short(v), "right"),
            Col("daily_volume", tr("Объём/сут"), lambda v, _r: fmt_isk_short(v), "right",
                tip=lambda _v, _r: self._liq_tip()),
            Col("capital", tr("Вложение"), lambda v, _r: fmt_isk_short(v), "right"),
            Col("score", "Score", lambda v, _r: f"{v:.2f}", "right", tone=lambda _v, _r: "cyan"),
        ]
        return [*cols, _section_col()] if with_section else cols

    def _build_top(self, cfg):
        scroll, body = scroll_page()
        f = Card(tr("Фильтры"), "filter", "cyan")
        row = QHBoxLayout()
        row.setSpacing(14)
        self.t_owned = check(tr("Только свои чертежи"), True, tr("Кандидаты — из чертежей персонажей (в локациях "
                                                                 "«Где искать чертежи»); иначе — все производимые"))
        row.addWidget(self.t_owned, 0, Qt.AlignmentFlag.AlignBottom)
        self.t_top = NumberEdit(cfg.ui.recommend_top, integer=True, width=70)
        self.t_runs = NumberEdit(cfg.recommend.runs, integer=True, width=70)
        self.t_budget = NumberEdit(None, tr("нет"), width=130)
        self.t_minvol = NumberEdit(None, tr("нет"), width=100)
        self.t_limit = NumberEdit(None, tr("все"), integer=True, width=90)
        for title, w in ((tr("Топ"), self.t_top), (tr("Прогонов"), self.t_runs), (tr("Бюджет, ISK"), self.t_budget),
                         (tr("Мин. объём/сут"), self.t_minvol), (tr("Лимит кандидатов"), self.t_limit)):
            row.addWidget(_field(title, w))
        row.addStretch(1)
        self.t_go = button(tr("Показать"), "star", "hero", icon_size=14)
        self.t_go.clicked.connect(self._run_top)
        row.addWidget(self.t_go, 0, Qt.AlignmentFlag.AlignBottom)
        f.body.addLayout(row)
        srow = QHBoxLayout()
        srow.setSpacing(10)
        srow.addWidget(label(tr("Разбивка"), "label"))
        self.t_split = QComboBox()
        for level, text in _SPLITS:
            self.t_split.addItem(tr(text), level)
        self.t_split.setCurrentIndex(max(0, self.t_split.findData(cfg.ui.recommend_split)))
        self.t_split.setToolTip(tr("Кроме общего ТОП — свой ТОП на каждый раздел рынка выбранного уровня (как на "
                                   "маркете: Ships › Frigates › Standard Frigates). Берутся только разделы, "
                                   "отмеченные в «Что строю»; размер — «ТОП на группу» из настроек."))
        self.t_split.currentIndexChanged.connect(self._split_changed)
        srow.addWidget(self.t_split)
        srow.addStretch(1)
        f.body.addLayout(srow)
        f.body.addWidget(label(tr("Что строить — галки «Что строю» слева. Группы ТОП, веса и «запреты» — «Настройки "
                                  "→ Рекомендации». Двойной клик по строке или «+ в корзину» — добавить в "
                                  "корзину."), "faint", wrap=True))
        self.t_liq = label("", "faint", wrap=True)
        f.body.addWidget(self.t_liq)
        body.addWidget(f)
        self.t_busy = Busy()
        body.addWidget(self.t_busy)
        self.t_err = StatusLine()
        body.addWidget(self.t_err)
        self.t_results = QVBoxLayout()
        self.t_results.setSpacing(14)
        body.addLayout(self.t_results)
        body.addStretch(1)
        return scroll

    def _split_changed(self, _i):
        level = int(self.t_split.currentData() or 0)
        run_bg(lambda: self.svc.put_config({"ui": {"recommend_split": level}}))
        if self._took["top"] is not None and self._took["top"] <= _AUTO_RERUN_MAX_S:
            if "top" in self._running:
                self._rerun.add("top")
            else:
                self._run_top()

    def _run_top(self):
        owned = self.t_owned.isChecked()
        top = self.t_top.int_value(30)
        runs = self.t_runs.int_value(1) or None
        budget, minvol = self.t_budget.value(), self.t_minvol.value()
        limit = self.t_limit.int_value(0) or None
        split = int(self.t_split.currentData() or 0)
        self.t_busy.start(tr("Считаю кандидатов (себестоимость каждого — полный расчёт)…"))
        self.t_go.setEnabled(False)
        self.t_err.clear_msg()
        svc = self.svc
        t0 = self._started("top")

        def work():
            res = svc.recommend_top(owned, top, runs, budget, minvol, limit, split)
            grouped = svc.recommend_grouped(owned, runs, budget, minvol, limit)
            return res, grouped

        def done(res, err):
            self.t_busy.stop()
            self.t_go.setEnabled(True)
            clear_layout(self.t_results)
            if err:
                self.t_err.show_msg("err", err_text(err))
                self._finished("top", t0, False)
                return
            res, grouped = res
            self.t_results.addWidget(self._table_card(tr("Общий ТОП"), res["flat"]))
            if split:
                self.t_results.addWidget(section(tr("ТОП по разделам рынка")))
                if not res["sections"]:
                    self.t_results.addWidget(label(tr("Ни в одном отмеченном разделе нет подходящих позиций."),
                                                   "muted", wrap=True))
                for s in res["sections"]:
                    # на уровне «группа» колонка «Раздел» повторила бы заголовок карточки
                    self.t_results.addWidget(self._table_card(s["name"], s["items"], icon="tag", tone="cyan",
                                                              with_section=split < 3))
            if grouped:
                if split:
                    self.t_results.addWidget(section(tr("Группы ТОП из настроек")))
                for g in grouped:
                    self.t_results.addWidget(self._table_card(tr("ТОП · {name}", name=g["name"]), g["items"],
                                                              tr("Нет кандидатов в этой группе (проверь свои "
                                                                 "чертежи / состав группы / «Что строю»).")))
            self._finished("top", t0, True)
        run_bg(work, done)

    def _table_card(self, title, rows,
                    empty=N_("Нет подходящих позиций (проверь данные рынка/чертежей, фильтры и «Что строю»)."),
                    icon="star", tone="gold", with_section=True):
        card = Card(title, icon, tone)
        if not rows:
            card.body.addWidget(label(tr(empty), "muted", wrap=True))
            return card
        t = DataTable(self._reco_cols(with_section), action=(tr("В корзину (Калькулятор/Расписание)"), tr("в корзину"),
                                                 self._add))
        self._tip_volume_header(t, "daily_volume")
        t.set_rows(rows)
        t.fit_height(30)
        t.activated.connect(self._add)
        card.set_meta(tr("{n} поз.", n=len(rows)))
        card.body.addWidget(t)
        return card

    # ------------------------------------------------------- дешевле купить
    def _build_cheaper(self, cfg):
        scroll, body = scroll_page()
        f = Card(tr("Дешевле купить, чем строить"), "cart", "gold")
        f.body.addWidget(label(tr("Предметы, которые рынок продаёт дешевле твоей себестоимости постройки "
                                  "(landed до места стройки). Без групп — весь торгуемый рынок (~10 с); "
                                  "выбери группы, чтобы сузить."), "muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(14)
        self.c_owned = check(tr("Только свои чертежи"), False)
        row.addWidget(self.c_owned, 0, Qt.AlignmentFlag.AlignBottom)
        self.c_minvol = NumberEdit(cfg.ui.buy_cheaper_min_volume, width=100)
        self.c_top = NumberEdit(cfg.ui.buy_cheaper_top, integer=True, width=70)
        self.c_limit = NumberEdit(None, tr("все"), integer=True, width=90)
        for title, w in ((tr("Мин. объём/сут"), self.c_minvol), (tr("Топ"), self.c_top),
                         (tr("Лимит кандидатов"), self.c_limit)):
            row.addWidget(_field(title, w))
        row.addStretch(1)
        self.c_go = button(tr("Сканировать рынок"), "search", "hero", icon_size=14)
        self.c_go.clicked.connect(self._run_cheaper)
        row.addWidget(self.c_go, 0, Qt.AlignmentFlag.AlignBottom)
        f.body.addLayout(row)
        self.c_groups = GroupFilter(self.svc, tr("Сузить по EVE-группе (напр. Hybrid Charge, Cruiser)…"),
                                    tr("группы не выбраны — сканируется весь рынок"))
        f.body.addWidget(self.c_groups)
        self.c_liq = label("", "faint", wrap=True)
        f.body.addWidget(self.c_liq)
        body.addWidget(f)
        self.c_busy = Busy()
        body.addWidget(self.c_busy)
        self.c_err = StatusLine()
        body.addWidget(self.c_err)
        self.c_card = Card(tr("Результат"), "list", "green")
        self.c_table = DataTable([
            Col("name", tr("Предмет"), icon_key="product_type_id", stretch=True),
            Col("build_unit", tr("Строить"), lambda v, _r: fmt_isk_short(v), "right"),
            Col("buy_unit", tr("Купить"), lambda v, _r: fmt_isk_short(v), "right", tone=lambda _v, _r: "green"),
            Col("buy_hub", tr("Хаб"), lambda v, _r: self._hub(v), "right"),
            Col("savings_pct", tr("Экономия"), lambda v, _r: "−" + fmt_pct(v), "right", tone=lambda _v, _r: "green"),
            Col("daily_volume", tr("Объём/сут"), lambda v, _r: fmt_isk_short(v), "right",
                tip=lambda _v, _r: self._liq_tip() + " " + (self._liq.get("cheaper_note") or "")),
            _section_col(),
        ])
        self.c_card.body.addWidget(self.c_table)
        self.c_card.hide()
        body.addWidget(self.c_card)
        body.addStretch(1)
        return scroll

    def _hub(self, v):
        cfg = self.svc.load_cfg()
        key = "jita" if v == "jita" else "c_j6mt"
        loc = cfg.locations.get(key)
        return loc.name if loc else v

    def _run_cheaper(self):
        owned = self.c_owned.isChecked()
        minvol = self.c_minvol.value()
        top = self.c_top.int_value(60)
        limit = self.c_limit.int_value(0) or None
        groups = self.c_groups.ids()
        self.c_busy.start(tr("Считаю себестоимость по рынку…"))
        self.c_go.setEnabled(False)
        self.c_err.clear_msg()
        t0 = self._started("cheaper")

        def done(rows, err):
            self.c_busy.stop()
            self.c_go.setEnabled(True)
            if err:
                self.c_err.show_msg("err", err_text(err))
                self._finished("cheaper", t0, False)
                return
            self.c_card.show()
            self.c_card.set_meta(tr("{n} поз.", n=len(rows)))
            self.c_table.set_rows(rows)
            self.c_table.fit_height(40)
            if not rows:
                self.c_err.show_msg("info", tr("Ничего не нашлось — всё выгоднее строить (или подними «мин. объём»)."))
            self._finished("cheaper", t0, True)
        run_bg(lambda: self.svc.buy_cheaper(owned, top, minvol, limit, groups), done)

    # ------------------------------------------------------------ из остатков
    def _build_stock(self, cfg):
        scroll, body = scroll_page()
        f = Card(tr("Что построить из остатков"), "boxes", "gold")
        f.body.addWidget(label(tr("Ищет постройки с максимальной реальной прибылью (себестоимость минус то, "
                                  "что уже лежит на складе) и использованием остатков: среди своих чертежей "
                                  "либо, без галки «Только свои чертежи», среди всего производимого — тогда "
                                  "решают только остатки. Размер партии подбирается сам — чтобы вычерпать "
                                  "самый дефицитный пересекающийся со складом материал. Что считать складом — "
                                  "вкладка «Склад»."), "muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(14)
        self.s_owned = check(tr("Только свои чертежи"), True, tr(
            "Кандидаты — из чертежей персонажей (в локациях «Где искать чертежи»). Снять — из всего "
            "производимого: решают только остатки, а чертёж считается как в Калькуляторе — инвента или "
            "«Стоимость чертежей вручную», иначе его цена 0 и в колонке «Чертёж» стоит «нет». Перебор "
            "всего рынка идёт около минуты — сузить можно группами и «Лимитом кандидатов»."))
        row.addWidget(self.s_owned, 0, Qt.AlignmentFlag.AlignBottom)
        self.s_minvol = NumberEdit(None, tr("нет"), width=100)
        self.s_top = NumberEdit(cfg.ui.stock_top, integer=True, width=70)
        self.s_limit = NumberEdit(None, tr("все"), integer=True, width=90)
        for title, w in ((tr("Мин. объём/сут"), self.s_minvol), (tr("Топ"), self.s_top),
                         (tr("Лимит кандидатов"), self.s_limit)):
            row.addWidget(_field(title, w))
        self.s_res = check(tr("без резервов строек"), True, tr("Не предлагать пускать в дело то, что уже "
                                                               "зарезервировали незавершённые стройки"))
        row.addWidget(self.s_res, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        self.s_go = button(tr("Проверить остатки"), "boxes", "hero-gold", icon_size=14)
        self.s_go.clicked.connect(self._run_stock)
        row.addWidget(self.s_go, 0, Qt.AlignmentFlag.AlignBottom)
        f.body.addLayout(row)
        self.s_groups = GroupFilter(self.svc, tr("Только эти EVE-группы продуктов (напр. Cruiser)…"),
                                    tr("группы не выбраны — все кандидаты"))
        f.body.addWidget(self.s_groups)
        self.s_liq = label("", "faint", wrap=True)
        f.body.addWidget(self.s_liq)
        body.addWidget(f)
        self.s_busy = Busy()
        body.addWidget(self.s_busy)
        self.s_err = StatusLine()
        body.addWidget(self.s_err)
        self.s_card = Card(tr("Результат"), "list", "green")
        self.s_table = DataTable([
            Col("name", tr("Предмет"), icon_key="product_type_id", stretch=True),
            Col("blueprint_source", tr("Чертёж"), lambda v, _r: tr(_BP_LABEL.get(v, _BP_NONE)[0]),
                tone=lambda v, _r: _BP_LABEL.get(v, _BP_NONE)[1],
                tip=lambda v, _r: tr(_BP_LABEL.get(v, _BP_NONE)[2])),
            Col("runs", tr("Прогонов"), align="right"),
            Col("real_profit", tr("Реальная прибыль"), lambda v, _r: fmt_isk_short(v), "right", tone=_tone_sign),
            Col("real_roi", tr("Реальный ROI"), lambda v, _r: fmt_pct(v), "right", tone=_tone_sign),
            Col("stock_value", tr("Со склада"), lambda v, _r: fmt_isk_short(v), "right", tone=lambda _v, _r: "cyan"),
            Col("stock_utilization", tr("% себест."), lambda v, _r: fmt_pct(v), "right"),
            Col("score", "Score", lambda v, _r: f"{v:.2f}", "right"),
            _section_col(),
        ], action=(tr("В корзину с подобранным числом прогонов"), tr("в корзину"), self._add))
        self.s_table.activated.connect(self._add)
        self.s_card.body.addWidget(self.s_table)
        self.s_card.hide()
        body.addWidget(self.s_card)
        body.addStretch(1)
        return scroll

    def _run_stock(self):
        owned = self.s_owned.isChecked()
        minvol = self.s_minvol.value()
        top = self.s_top.int_value(30)
        limit = self.s_limit.int_value(0) or None
        groups = self.s_groups.ids()
        res_ = self.s_res.isChecked()
        self.s_busy.start(tr("Сверяю склад с чертежами…") if owned
                          else tr("Сверяю склад со всем, что производится (около минуты)…"))
        self.s_go.setEnabled(False)
        self.s_err.clear_msg()
        t0 = self._started("stock")

        def done(rows, err):
            self.s_busy.stop()
            self.s_go.setEnabled(True)
            if err:
                self.s_err.show_msg("err", err_text(err))
                self._finished("stock", t0, False)
                return
            self.s_card.show()
            self.s_card.set_meta(tr("{n} поз.", n=len(rows)))
            self.s_table.set_rows(rows)
            self.s_table.fit_height(40)
            if not rows and owned:
                self.s_err.show_msg("info", tr("Ничего не нашлось — склад пуст (проверь вкладку «Склад») или ни "
                                               "один свой чертёж не пересекается с тем, что на нём лежит."))
            elif not rows:
                self.s_err.show_msg("info", tr("Ничего не нашлось — склад пуст (проверь вкладку «Склад») или из "
                                               "того, что на нём лежит, ничего продаваемого не строится."))
            self._finished("stock", t0, True)
        run_bg(lambda: self.svc.recommend_stock(top, minvol, limit, groups, res_, owned), done)
