"""Вкладка «Калькулятор»: себестоимость и прибыль корзины, дерево материалов, проблемы,
побочка переработки, сравнение по ME главного чертежа, формирование отчёта по стройке."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..icons import TypeIcons
from ..widgets import (
    N_,
    Busy,
    Card,
    ClickFrame,
    Col,
    DataTable,
    ElidedLabel,
    Page,
    Stat,
    StatusLine,
    TypeIcon,
    button,
    check,
    clear_layout,
    err_text,
    fmt_int,
    fmt_isk,
    fmt_isk_short,
    fmt_pct,
    hline,
    label,
    run_bg,
    scroll_page,
    section,
    tr,
    type_qicon,
)
from .basket_panel import BasketPanel


def tone_of(v) -> str | None:
    if v is None:
        return None
    return "green" if v >= 0 else "red"


class Collapsible(QFrame):
    """Раскрывающийся блок: шапка-строка (виджет) + тело, которое строится лениво при первом
    раскрытии (дерево Nomad — тысячи строк, не строим его, пока не попросили)."""

    def __init__(self, head: QWidget, build_body, expanded: bool = False, tone: str | None = None,
                 parent=None):
        super().__init__(parent)
        self.setProperty("card", "tile")
        if tone:
            self.setStyleSheet(f'QFrame[card="tile"] {{ border-color: {theme.rgba(tone, 0.35)};'
                               f' background: {theme.rgba(tone, 0.05)}; }}')
        self._build = build_body
        self._body: QWidget | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.head = ClickFrame()
        h = QHBoxLayout(self.head)
        h.setContentsMargins(10, 7, 12, 7)
        h.setSpacing(8)
        self.arrow = label("")
        self.arrow.setFixedWidth(14)
        h.addWidget(self.arrow)
        h.addWidget(head, 1)
        self.head.clicked.connect(self.toggle)
        v.addWidget(self.head)
        self._v = v
        self._open = False
        self._paint_arrow()
        if expanded:
            self.toggle()

    def _paint_arrow(self):
        self.arrow.setPixmap(theme.icon_pixmap("chevron" if self._open else "chevron-right",
                                               theme.tone_text("cyan") if self._open else theme.FAINT,
                                               14, self.devicePixelRatioF()))

    def toggle(self):
        self._open = not self._open
        if self._open and self._body is None:
            self._body = QWidget()
            bl = QVBoxLayout(self._body)
            bl.setContentsMargins(12, 4, 12, 12)
            bl.setSpacing(8)
            bl.addWidget(hline())
            self._build(bl)
            self._v.addWidget(self._body)
        if self._body is not None:
            self._body.setVisible(self._open)
        self._paint_arrow()


def _note(text: str, tone: str = "gold") -> QWidget:
    lb = label(text, wrap=True, selectable=True)
    lb.setStyleSheet(f"color: {theme.tone_text(tone)}; background: {theme.rgba(tone, 0.07)};"
                     f" border: 1px solid {theme.rgba(tone, 0.28)}; border-radius: 9px; padding: 6px 10px;")
    return lb


def _where(b: dict, key: str) -> str:
    """« (индекс 6.14%, GPLB-C)» — где считан взнос копирования/инвенты (система станции роли)."""
    ci = b.get(f"{key}_cost_index")
    if ci is None:
        return ""
    name = b.get(f"{key}_system_name")
    pct = f"{ci * 100:.2f}%"
    return tr(" (индекс {pct}, {name})", pct=pct, name=name) if name else tr(" (индекс {pct})", pct=pct)


def _decryptor_suffix(name, manual) -> str:
    """« · декриптор «X» (задано вручную)» / « · без декриптора» — хвост строки об инвенте."""
    d = tr(" · декриптор «{name}»", name=name) if name else tr(" · без декриптора")
    return d + tr(" (задано вручную)") if manual else d


def _chance_rows(b: dict) -> list[tuple[str, str]]:
    """Разбивка шанса инвенты: база SDE × скиллы лучшего инвентора × декриптор (итог — отдельной
    строкой «= Вероятность успеха»). Payload без составляющих — пустой список."""
    base = b.get("base_probability")
    if base is None:
        return []
    rows = [(tr("Базовый шанс (SDE)"), f"{base * 100:.1f}%")]
    mult = b.get("skill_mult") or 1.0
    if not b.get("skills_enabled"):
        rows.append((tr("× скиллы — не учитываются (Настройки → Производство)"), "×1.000"))
    elif b.get("inventor_name"):
        rows.append((tr("× скиллы, лучший инвентор: {name}", name=b["inventor_name"]), f"×{mult:.3f}"))
    else:
        rows.append((tr("× скиллы — нет назначенных на «Науку» (вкладка «Персонажи»)"), "×1.000"))
    dmult = b.get("decryptor_mult") or 1.0
    if dmult != 1.0:
        rows.append((tr("× декриптор"), f"×{dmult:.2f}"))
    return rows


def _kv_rows(pairs: list[tuple[str, str]], total: tuple[str, str] | None = None) -> QWidget:
    w = QWidget()
    g = QGridLayout(w)
    g.setContentsMargins(4, 2, 4, 2)
    g.setHorizontalSpacing(16)
    g.setVerticalSpacing(3)
    r = 0
    for k, v in pairs:
        g.addWidget(label(k, "muted"), r, 0)
        vl = label(v)
        vl.setFont(theme.font(13, 400, num=True))
        vl.setAlignment(Qt.AlignmentFlag.AlignRight)
        g.addWidget(vl, r, 1)
        r += 1
    if total:
        g.addWidget(hline(), r, 0, 1, 2)
        k = label(total[0])
        k.setFont(theme.font(13, 600))
        v = label(total[1])
        v.setFont(theme.font(13, 600, num=True))
        v.setAlignment(Qt.AlignmentFlag.AlignRight)
        g.addWidget(k, r + 1, 0)
        g.addWidget(v, r + 1, 1)
    g.setColumnStretch(0, 1)
    return w


class MaterialTree(QTreeWidget):
    """Дерево материалов: кол-во, цены по хабам (выбранный — зелёным, не хватает объёма —
    зачёркнуто), источник (клик — строить ↔ купить), сумма."""

    COLS = (N_("Материал"), N_("Кол-во"), "", "", N_("Источник"), N_("Сумма"))

    def __init__(self, node: dict, overrides: list[int], on_toggle, places: dict, parent=None):
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._overrides = set(overrides)
        self._items_by_tid: dict[int, list[QTreeWidgetItem]] = {}
        self.setColumnCount(6)
        hdr = [tr(c) for c in self.COLS]
        hdr[2] = f"{places['jita']}→{places['cj']}"
        hdr[3] = places["cj"]
        self.setHeaderLabels([h.upper() for h in hdr])
        self.setIconSize(QSize(20, 20))
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setIndentation(16)
        self.header().setStretchLastSection(False)
        self.header().setFont(theme.font(11, 600, display=True, spacing=106))
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 90), (2, 96), (3, 96), (4, 96), (5, 128)):
            self.header().setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(c, w)
            self.headerItem().setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.headerItem().setTextAlignment(4, Qt.AlignmentFlag.AlignCenter)
        self._num = theme.font(12, 400, num=True)
        self._fill(self.invisibleRootItem(), node, 0)
        self.itemClicked.connect(self._clicked)
        TypeIcons.get().loaded.connect(self._icon_loaded)
        rows = self._count(self.invisibleRootItem())
        self.setMinimumHeight(min(560, 34 + 26 * max(rows, 1)))

    def _count(self, parent) -> int:
        n = parent.childCount()
        for i in range(parent.childCount()):
            ch = parent.child(i)
            if ch.isExpanded():
                n += self._count(ch)
        return n

    def _fill(self, parent, node: dict, depth: int):
        for ln in node.get("lines") or []:
            h = ln.get("hub") or {}
            j, c = h.get("jita_to_cj"), h.get("cj_local")
            chosen = ln.get("buy_hub") if ln.get("source") == "buy" and ln.get("buy_hub") else (
                "cj" if c is not None and (j is None or c < j) else ("jita" if j is not None else None))
            child = ln.get("child")
            name = ln["name"]
            if child and child.get("blueprint_source") == "reprocess":
                name = tr("{name}   ♻ переработка", name=name)
            it = QTreeWidgetItem(parent, [name, fmt_int(ln["quantity"]), fmt_isk_short(j),
                                          fmt_isk_short(c), "", fmt_isk(ln.get("subtotal"))])
            it.setIcon(0, type_qicon(ln["type_id"]))
            self._items_by_tid.setdefault(ln["type_id"], []).append(it)
            it.setData(0, Qt.ItemDataRole.UserRole, ln["type_id"])
            if child and child.get("blueprint_source") == "reprocess":
                it.setToolTip(0, tr("Дешевле переработать «{src}», чем строить/купить напрямую "
                                    "(побочка сверху: {credit})",
                                    src=child.get("reprocess_source_name"),
                                    credit=fmt_isk(child.get("reprocess_byproduct_credit"))))
            for col in (1, 2, 3, 5):
                it.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                it.setFont(col, self._num)
            self._hub_cell(it, 2, j, chosen == "jita", h.get("jita_enough"), h.get("jita_available"))
            self._hub_cell(it, 3, c, chosen == "cj", h.get("cj_enough"), h.get("cj_available"))
            src = ln.get("source")
            overridden = ln["type_id"] in self._overrides
            if overridden:
                text, tone, tip = (tr("✋ купить"), "violet",
                                   tr("Принудительно купить (клик — вернуть авто-решение)"))
            elif src == "build":
                text, tone, tip = tr("строить"), "gold", tr("Клик — купить вместо постройки")
            elif src == "unknown":
                text, tone, tip = tr("нет цены"), "red", tr("Нет цены — переключить нельзя")
            else:
                text = tr("⚠ купить") if ln.get("buy_shortage") else tr("купить")
                tone, tip = "cyan", tr("Клик — зафиксировать «купить» (не строить)")
            it.setText(4, text)
            it.setTextAlignment(4, Qt.AlignmentFlag.AlignCenter)
            it.setForeground(4, QColor(theme.tone_text(tone)))
            it.setToolTip(4, tip)
            it.setData(4, Qt.ItemDataRole.UserRole, src != "unknown")
            if child:
                self._fill(it, child, depth + 1)
            if depth == 0 and child:
                it.setExpanded(False)

    def _hub_cell(self, it, col, v, chosen, enough, avail):
        if v is None:
            it.setForeground(col, QColor(theme.FAINT))
            return
        f = QFont(self._num)
        if chosen:
            it.setForeground(col, QColor(theme.tone_text("green")))
            f.setWeight(QFont.Weight.DemiBold)
        elif enough is False:
            it.setForeground(col, QColor(theme.FAINT))
            f.setStrikeOut(True)
        else:
            it.setForeground(col, QColor(theme.MUTED))
        it.setFont(col, f)
        if avail is not None:
            it.setToolTip(col, tr("доступно {n} — не хватает на нужное количество", n=fmt_int(avail))
                          if enough is False else tr("доступно {n}", n=fmt_int(avail)))

    def _clicked(self, it, col):
        if col != 4 or not it.data(4, Qt.ItemDataRole.UserRole):
            return
        tid = it.data(0, Qt.ItemDataRole.UserRole)
        if tid:
            self._on_toggle(int(tid))

    def _icon_loaded(self, tid):
        for it in self._items_by_tid.get(tid, []):
            try:
                it.setIcon(0, type_qicon(tid))
            except RuntimeError:
                pass


class CalculatorPage(Page):
    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._data = None
        self._places = {"jita": "Jita", "cj": "C-J6MT", "build": "GPLB-C"}
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)

        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFrameShape(QFrame.Shape.NoFrame)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left.setFixedWidth(432)
        self.panel = BasketPanel(ctx)
        left.setWidget(self.panel)
        h.addWidget(left)

        self.btn_calc = self.panel.add_action(button(tr("Посчитать корзину"), "calc", "hero", icon_size=15))
        self.btn_calc.clicked.connect(lambda: self.run())
        self.btn_report = self.panel.add_action(button(tr("Сформировать отчёт по стройке"), "clipboard",
                                                       "primary"))
        self.btn_report.setToolTip(tr("HTML-отчёт: закупки (с вычетом склада), инструкции по чарам, "
                                      "контроль сроков и стоимости. Откроется в браузере."))
        self.btn_report.clicked.connect(self.report)
        ropts = QHBoxLayout()
        self.use_stock = check(tr("учитывать склад"), True,
                               tr("Вычитать из закупок то, что уже лежит на складе ([stock])"))
        self.use_res = check(tr("резервы строек"), True, tr("Уменьшать доступный склад на то, что "
                                                            "зарезервировали незавершённые стройки"))
        ropts.addWidget(self.use_stock)
        ropts.addWidget(self.use_res)
        ropts.addStretch(1)
        self.panel.action_box.addLayout(ropts)
        self.btn_me = self.panel.add_action(button(tr("Сравнить ME главного чертежа"), "target", "good"))
        self.btn_me.clicked.connect(self.compare_me)

        scroll, self.body = scroll_page((2, 2, 10, 12), 14)
        h.addWidget(scroll, 1)
        self.busy = Busy()
        self.body.addWidget(self.busy)
        self.error = StatusLine()
        self.body.addWidget(self.error)
        self.me_host = QVBoxLayout()
        self.body.addLayout(self.me_host)
        self.results = QVBoxLayout()
        self.results.setSpacing(14)
        self.body.addLayout(self.results)
        self.hint = Card(corners=False)
        self.hint.body.addWidget(label(tr("Добавь предметы в корзину и нажми «Посчитать корзину». Корзина "
                                          "общая с «Расписанием»; из «Что строить» предметы добавляются "
                                          "кнопкой «+ в корзину»."), "muted", wrap=True))
        self.body.addWidget(self.hint)
        self.body.addStretch(1)

        ctx.basket.changed.connect(self._basket_changed)
        ctx.hub.config_changed.connect(self._load_places)
        self._load_places()
        self._basket_changed()

    def _load_places(self):
        cfg = self.svc.load_cfg()
        from ...web.report import place_labels
        self._places = place_labels(cfg)

    def _basket_changed(self):
        n = len(self.ctx.basket.items)
        self.btn_calc.setEnabled(n > 0)
        self.btn_report.setEnabled(n > 0)
        self.btn_me.setEnabled(n == 1)
        self.btn_me.setToolTip(tr("Себестоимость этого предмета при ME его чертежа 0–10 — остальное "
                                  "дерево не меняется") if n == 1
                               else tr("Работает с одним предметом в корзине"))

    # ------------------------------------------------------------ действия
    def run(self):
        if not self.ctx.basket.items:
            return
        b = self.ctx.basket.to_basket()
        self.busy.start(tr("Считаю себестоимость корзины…"))
        self.error.clear_msg()
        self.btn_calc.setEnabled(False)

        def done(res, err):
            self.busy.stop()
            self.btn_calc.setEnabled(True)
            if err:
                self.error.show_msg("err", err_text(err))
                return
            self._data = res
            self._render(res)
        run_bg(lambda: self.svc.cost_basket(b), done)

    def report(self):
        b = self.ctx.basket.to_basket()
        use_stock, use_res = self.use_stock.isChecked(), self.use_res.isChecked()
        self.busy.start(tr("Формирую отчёт по стройке (расписание, склад, закупки)…"))
        self.btn_report.setEnabled(False)

        def done(res, err):
            self.busy.stop()
            self.btn_report.setEnabled(True)
            if err:
                self.error.show_msg("err", err_text(err))
                return
            self.ctx.hub.reports_changed.emit()
            if self.ctx.open_report(res["url"]):
                self.ctx.flash(tr("Отчёт сохранён: reports/{file} — открыт в браузере", file=res["filename"]),
                               ms=8000)
        run_bg(lambda: self.svc.report_basket(b, use_stock=use_stock, respect_reservations=use_res), done)

    def compare_me(self):
        items = self.ctx.basket.items
        if len(items) != 1:
            return
        x, o = items[0], self.ctx.basket.opts
        self.busy.start(tr("Считаю себестоимость при ME 0…10…"))
        clear_layout(self.me_host)

        def done(res, err):
            self.busy.stop()
            if err:
                self.error.show_msg("err", err_text(err))
                return
            self._render_me(res, o.me)
        run_bg(lambda: self.svc.compare_me(x.type_id, x.runs, x.streams, o.me, o.te, o.build,
                                           o.max_days, o.consolidate, o.consolidate and o.auto_streams),
               done)

    def _toggle_component(self, tid: int):
        self.ctx.basket.toggle_component(tid)
        self.run()

    # ------------------------------------------------------------ отрисовка
    def _render_me(self, res, cur_me):
        card = Card(tr("Сравнение по ME главного чертежа"), "target", "green")
        if res.get("reason") == "invention":
            card.body.addWidget(label(tr("У этого чертежа ME задаёт декриптор инвенты (авто-оптимум по стоимости "
                                         "или выбранный в «Настройки → Производство → Инвента») — свободного "
                                         "параметра 0–10 нет, сравнение недоступно."), "muted", wrap=True))
            self.me_host.addWidget(card)
            return
        rows = res.get("rows") or []
        min_cost = min((r["total_cost"] for r in rows), default=None)
        max_profit = max((r["profit"] for r in rows if r["profit"] is not None), default=None)
        t = DataTable([
            Col("me", "ME", lambda v, _r: tr("{me} (тек.)", me=v) if v == cur_me else f"{v}"),
            Col("total_cost", tr("Себестоимость"), lambda v, _r: fmt_isk(v), "right",
                tone=lambda v, _r: "green" if v == min_cost else None),
            Col("unit_cost", tr("За штуку"), lambda v, _r: fmt_isk(v), "right"),
            Col("material_cost", tr("Материалы"), lambda v, _r: fmt_isk_short(v), "right"),
            Col("job_cost", tr("Джобы"), lambda v, _r: fmt_isk_short(v), "right"),
            Col("profit", tr("Прибыль"), lambda v, _r: fmt_isk(v), "right",
                tone=lambda v, _r: "cyan" if v is not None and v == max_profit else None),
            Col("roi", "ROI", lambda v, _r: fmt_pct(v), "right"),
        ], sortable=False)
        t.set_rows(rows)
        t.fit_height(12)
        card.body.addWidget(t)
        card.body.addWidget(label(tr("Зелёным — самая низкая себестоимость, cyan — наибольшая прибыль. "
                                     "«тек.» — текущее поле «ME, если нет своего»."), "faint", wrap=True))
        self.me_host.addWidget(card)

    def _render(self, data):
        clear_layout(self.results)
        self.hint.hide()
        t = data["totals"]
        tot = Card(tr("Итого по корзине"), "coins", "gold")
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        # (подпись, значение, тон, ключ итога для подсказки с точной суммой — None: без подсказки)
        tiles = [
            (tr("Себестоимость"), fmt_isk_short(t["total_cost"]), None, "total_cost"),
            (tr("Выручка"), fmt_isk_short(t["revenue"]), None, "revenue"),
            (tr("Прибыль"), fmt_isk_short(t["profit"]), tone_of(t["profit"]), "profit"),
            ("ROI", fmt_pct(t.get("roi")), tone_of(t.get("roi")), None),
            (tr("Материалы"), fmt_isk_short(t["material_cost"]), None, "material_cost"),
            (tr("Установка джобов"), fmt_isk_short(t["job_cost"]), None, "job_cost"),
            (tr("Чертежи / инвента"), fmt_isk_short(t["blueprint_cost"]), None, "blueprint_cost"),
            (tr("Позиций"), str(len(data["items"])), None, None),
        ]
        for i, (k, v, tone, key) in enumerate(tiles):
            s = Stat(k, v, tone)
            s.setToolTip(fmt_isk(t.get(key, None)) + " ISK" if key else "")
            g.addWidget(s, i // 4, i % 4)
        tot.body.addLayout(g)
        self.results.addWidget(tot)

        prob = self._problems(data["items"])
        if prob is not None:
            self.results.addWidget(prob)
        left = data.get("reprocess_leftovers") or []
        if left:
            self.results.addWidget(self._leftovers(left))

        items = Card(tr("По предметам"), "list", "cyan")
        comps = list(self.ctx.basket.opts.buy_components)
        for it in data["items"]:
            items.body.addWidget(self._buy_only(it) if it.get("buy_only") else self._item(it, comps))
        self.results.addWidget(items)

    def _problems(self, items) -> Card | None:
        missing, invention, noprice, reaction, no_t1, bpc = {}, {}, {}, {}, {}, {}
        dec_fallback: dict[int, tuple[str, str]] = {}

        def visit(n):
            if not n:
                return
            src = n.get("blueprint_source")
            if n.get("decryptor_fallback"):
                dec_fallback[n["product_type_id"]] = (n["name"], n["decryptor_fallback"])
            if src == "missing":
                missing[n["product_type_id"]] = n["name"]
            elif src == "invention":
                ib = n.get("invention_breakdown") or {}
                invention[n["product_type_id"]] = {
                    "name": n["name"], "attempts": ib.get("attempts", 0), "produced": n.get("produced"),
                    "decryptor": n.get("decryptor"), "short": False, "prob": ib.get("probability"),
                    "manual": n.get("decryptor_manual")}
                if n.get("invention_source_owned") is False:
                    no_t1[n["product_type_id"]] = (n.get("invention_source_name") or f"#{n.get('invention_source_id')}",
                                                   n["name"])
            elif src == "missing_reaction":
                reaction[n["product_type_id"]] = (n["name"], n.get("reaction_bp_owned") or 0,
                                                  n.get("reaction_bp_needed") or 1)
            elif src == "owned_bpc_insufficient":
                bpc[n["product_type_id"]] = (n["name"], n.get("bpc_runs_owned") or 0, n.get("bpc_runs_needed") or 0)
                if n.get("bpc_shortfall_invention_attempts") is not None:
                    short = max(0, (n.get("bpc_runs_needed") or 0) - (n.get("bpc_runs_owned") or 0))
                    invention[n["product_type_id"]] = {
                        "name": n["name"], "attempts": n["bpc_shortfall_invention_attempts"], "produced": short,
                        "decryptor": n.get("bpc_shortfall_decryptor"), "short": True,
                        "prob": n.get("bpc_shortfall_probability"), "manual": n.get("decryptor_manual")}
                    if n.get("invention_source_owned") is False:
                        no_t1[n["product_type_id"]] = (n.get("invention_source_name") or "?", n["name"])
            for ln in n.get("lines") or []:
                if ln.get("source") == "unknown":
                    noprice[ln["type_id"]] = ln["name"]
                if ln.get("child"):
                    visit(ln["child"])

        for it in items:
            visit(it["node"])
        if not (missing or invention or noprice or reaction or no_t1 or bpc or dec_fallback):
            return None
        card = Card(tr("Подготовка / проблемы"), "alert", "gold")
        for r in invention.values():
            d = _decryptor_suffix(r["decryptor"], r["manual"])
            what = tr("недостача ранов у своей копии") if r["short"] else tr("нужен T1-чертёж + датакоры")
            chance = tr(", шанс {pct}", pct=f"{r['prob'] * 100:.1f}%") if r["prob"] else ""
            produced = tr(" → {n} шт.", n=fmt_int(r["produced"])) if r["produced"] else ""
            card.body.addWidget(self._line(
                tr("Заинвентить: {name}{produced}{dec} ({attempts} попыт. Т1-копий{chance}, план округлён "
                   "вверх; {what}).", name=r["name"], produced=produced, dec=d,
                   attempts=fmt_int(r["attempts"]), chance=chance, what=what), "cyan"))
        for name, note in dec_fallback.values():
            card.body.addWidget(self._line(tr("⚠ Инвента {name}: {note}. Проверь цену декриптора или «Настройки → "
                                              "Производство → Инвента».", name=name, note=note), "gold"))
        for src, prod in no_t1.values():
            card.body.addWidget(self._line(tr("⚠ Нет T1-чертежа для инвенты: {src} — нужен, чтобы заинвентить "
                                              "{prod}. Купи BPO/BPC или впиши цену в «Настройки → Чертежи».",
                                              src=src, prod=prod), "gold"))
        for name in missing.values():
            card.body.addWidget(self._line(tr("⚠ Нет чертежа: {name} — себестоимость занижена; задай цену в "
                                              "«Настройки → Чертежи».", name=name), "gold"))
        for name, owned, needed in reaction.values():
            card.body.addWidget(self._line(tr("⚠ Не хватает копий формулы реакции: {name} — нужно {needed}, "
                                              "есть {owned}. Докупи копии или уменьши число потоков.",
                                              name=name, needed=needed, owned=owned), "gold"))
        for name, owned, needed in bpc.values():
            card.body.addWidget(self._line(tr("⚠ Не хватает ранов у своей копии: {name} — нужно {needed}, "
                                              "осталось {owned}.", name=name, needed=fmt_int(needed),
                                              owned=fmt_int(owned)), "gold"))
        for name in noprice.values():
            card.body.addWidget(self._line(tr("Нет рыночной цены: {name} — не учтено в стоимости.", name=name),
                                           "red"))
        card.body.addWidget(label(tr("Закрой эти пробелы — и отчёт сформируется без «дыр» в себестоимости. "
                                     "Передачи чертежей между чарами видны в самом отчёте."), "faint", wrap=True))
        return card

    @staticmethod
    def _line(text, tone):
        lb = label(text, wrap=True, selectable=True)
        lb.setStyleSheet(f"color: {theme.tone_text(tone)};")
        return lb

    def _leftovers(self, rows) -> Card:
        card = Card(tr("Останется от переработки"), "recycle", "green")
        card.body.addWidget(label(tr("Побочные продукты переработки — физически осядут на складе "
                                     "{build}. В себестоимость не входят — бонус сверху, "
                                     "если продать (цена — с налогом/брокером и вывозом в {cj}).",
                                     build=self._places["build"], cj=self._places["cj"]),
                                  "faint", wrap=True))
        t = DataTable([
            Col("name", tr("Материал"), icon_key="type_id", stretch=True),
            Col("quantity", tr("Кол-во"), lambda v, _r: fmt_int(v), "right"),
            Col("sell_total", tr("Если продать"), lambda v, _r: fmt_isk(v) if v is not None else
                tr("нет цены в {place}", place=self._places["cj"]), "right"),
        ])
        t.set_rows(rows)
        t.fit_height(10)
        card.body.addWidget(t)
        total = sum(r.get("sell_total") or 0 for r in rows)
        card.body.addWidget(label(tr("Итого по цене продажи: {isk} ISK", isk=fmt_isk(total)), "isk"))
        return card

    def _head(self, n, produced_text, right_cells):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        h.addWidget(TypeIcon(n["product_type_id"], 26))
        name = ElidedLabel(n["name"])
        name.setFont(theme.font(14, 600))
        h.addWidget(name, 1)
        h.addWidget(label(produced_text, "muted"))
        for text, tone, width in right_cells:
            lb = label(text)
            lb.setFont(theme.font(13, 500, num=True))
            lb.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lb.setFixedWidth(width)
            if tone:
                lb.setStyleSheet(f"color: {theme.tone_text(tone)};")
            h.addWidget(lb)
        return w

    def _item(self, it, comps) -> QWidget:
        n, p = it["node"], it.get("profit") or {}
        head = self._head(n, tr("→ {n} шт.", n=fmt_int(n["produced"])),
                          [(fmt_isk_short(n["total_cost"]), None, 90),
                           (fmt_isk_short(p.get("profit")), tone_of(p.get("profit")), 90),
                           (fmt_pct(p.get("roi")), tone_of(p.get("roi")), 64)])

        def body(bl):
            g = QGridLayout()
            g.setHorizontalSpacing(8)
            g.setVerticalSpacing(8)
            src = n.get("blueprint_source")
            bad_bp = src in ("missing", "missing_reaction", "owned_bpc_insufficient")
            for i, (k, v, tone) in enumerate((
                    (tr("За штуку"), fmt_isk(n["unit_cost"]), None),
                    (tr("Цена продажи"), fmt_isk(p.get("sell_unit_price")), None),
                    (tr("Материалы (всего)"), fmt_isk_short((it.get("agg") or {}).get("materials")), None),
                    (tr("Джобы (всего)"), fmt_isk_short((it.get("agg") or {}).get("jobs")), None),
                    (tr("Чертежи (всего)"), fmt_isk_short((it.get("agg") or {}).get("blueprints")),
                     "red" if bad_bp else None))):
                g.addWidget(Stat(k, v, tone), 0, i)
            bl.addLayout(g)
            fr = it.get("freight")
            if fr:
                bl.addWidget(label(
                    tr("Логистика: входящий фрахт {inbound} ISK (зашит в материалы) · вывоз "
                       "{export} ISK ({mode}, вычтен из выручки)",
                       inbound=fmt_isk(fr["inbound"]), export=fmt_isk(fr["export_total"]),
                       mode=tr("топливо за прыжок") if fr.get("export_is_jump") else tr("по объёму")),
                    "muted", wrap=True))
            jc = n
            econ = (1 - (jc.get("cost_mult") or 1)) * 100
            sys_label = f" {jc['cost_system_name']}" if jc.get("cost_system_name") else ""
            job_head = self._line_head(tr("Установка джоба (основной чертёж) — разбивка"), fmt_isk(n["job_cost"]))
            job_head.setToolTip(tr("Индекс — системы станции этого джоба («Настройки → Станции»: своя или "
                                   "система стройки). Логистика между системами не считается: материалы — "
                                   "доставленными в {build}.", build=self._places["build"]))
            bl.addWidget(Collapsible(
                job_head,
                lambda lay: lay.addWidget(_kv_rows([
                    (tr("EIV (Σ adjusted_price × база × runs)"), fmt_isk(jc.get("eiv"))),
                    (tr("× индекс системы{sys}", sys=sys_label), f"{(jc.get('cost_index') or 0) * 100:.2f}%"),
                    (tr("× множитель станции"),
                     f"×{(jc.get('cost_mult') or 1):.3f}" + (f" (−{econ:.1f}%)" if econ else "")),
                    (tr("+ налог + SCC (от EIV)"), f"{(jc.get('facility_tax') or 0) * 100:.2f}% + "
                                                   f"{(jc.get('scc_surcharge') or 0) * 100:.2f}%"),
                ], (tr("= Установка джоба"), fmt_isk(n["job_cost"]))))))
            if src == "invention" and n.get("invention_breakdown"):
                b = n["invention_breakdown"]
                dec = _decryptor_suffix(n.get("decryptor"), n.get("decryptor_manual"))
                pairs = [(tr("Датакоры"), fmt_isk(b.get("datacores")))]
                if b.get("t1_copy"):
                    pairs.append((tr("T1-копия (источник)") + _where(b, "t1_copy"), fmt_isk(b["t1_copy"])))
                if b.get("decryptor_cost"):
                    pairs.append((tr("Декриптор"), fmt_isk(b["decryptor_cost"])))
                pairs += [(tr("Джоб-взнос инвенты") + _where(b, "job_fee"), fmt_isk(b.get("job_fee"))),
                          (tr("= за попытку"), fmt_isk(b.get("attempt")))]
                pairs += _chance_rows(b)
                pairs += [(tr("= Вероятность успеха"), f"{(b.get('probability') or 0) * 100:.1f}%"),
                          (tr("Прогонов на копию"), str(b.get("runs_per_copy"))),
                          (tr("Попыток (Т1-копий), округлено вверх"), str(b.get("attempts")))]
                bl.addWidget(Collapsible(
                    self._line_head(tr("Чертёж — инвента{dec}", dec=dec),
                                    tr("{isk}/прогон", isk=fmt_isk(b.get("per_run")))),
                    lambda lay, pairs=pairs, b=b: lay.addWidget(_kv_rows(pairs, (tr("= вся инвента (справочно)"),
                                                                                 fmt_isk(b.get("total"))))),
                    tone="cyan"))
            if src == "invention" and n.get("invention_source_owned") is False:
                t1_src = n.get("invention_source_name") or "#" + str(n.get("invention_source_id"))
                bl.addWidget(_note(tr("⚠ Нет T1-чертежа для инвенты: «{src}». Нужен BPO (для копий) или "
                                      "покупка BPC — иначе инвенту не запустить.", src=t1_src)))
            if src == "missing":
                bl.addWidget(_note(tr("⚠ Нет цены чертежа для «{name}» (не во владении, инвента недоступна). "
                                      "Задай вручную: «Настройки → Чертежи → Стоимость чертежей».", name=n["name"])))
            if src == "missing_reaction":
                bl.addWidget(_note(tr("⚠ Не хватает копий формулы реакции «{name}»: нужно {needed} (под {streams} "
                                      "потоков), есть {owned}.", name=n["name"], needed=n.get("reaction_bp_needed"),
                                      streams=n.get("streams"), owned=n.get("reaction_bp_owned"))))
            if src == "owned_bpc_insufficient":
                extra = ""
                if n.get("bpc_shortfall_invention_attempts") is not None:
                    dec = n.get("bpc_shortfall_decryptor")
                    prob = n.get("bpc_shortfall_probability")
                    extra = tr(" Заинвентить недостачу: {dec}{manual}, {attempts} попыт. Т1-копий{chance}.",
                               dec=tr("декриптор «{name}»", name=dec) if dec else tr("без декриптора"),
                               manual=tr(" (задано вручную)") if n.get("decryptor_manual") else "",
                               attempts=n["bpc_shortfall_invention_attempts"],
                               chance=tr(" (шанс {pct})", pct=f"{prob * 100:.1f}%") if prob else "")
                bl.addWidget(_note(tr("⚠ Не хватает ранов у своей копии «{name}»: нужно {needed} прогонов, "
                                      "осталось {owned}.{extra}", name=n["name"], needed=n.get("bpc_runs_needed"),
                                      owned=n.get("bpc_runs_owned"), extra=extra)))
            if n.get("decryptor_fallback"):
                bl.addWidget(_note(tr("⚠ Инвента: {note}.", note=n["decryptor_fallback"])))
            if not n.get("blueprint_type_id") and src != "reprocess":
                buy = it.get("buy_unit_price")
                bl.addWidget(_note(
                    tr("Нет чертежа — этот предмет нельзя построить (мета/дроп). Только покупка: {isk} ISK.",
                       isk=fmt_isk(buy)) if buy is not None
                    else tr("Нет чертежа — этот предмет нельзя построить (мета/дроп). Только покупка.")))
            elif it.get("buy_unit_price") is not None:
                if it.get("build_cheaper"):
                    bl.addWidget(_note(tr("Выгоднее строить: {cost} против {buy} за готовый.",
                                          cost=fmt_isk(n["unit_cost"]), buy=fmt_isk(it["buy_unit_price"])),
                                       "green"))
                else:
                    bl.addWidget(_note(tr("Выгоднее купить готовым: {buy} против {cost}.",
                                          buy=fmt_isk(it["buy_unit_price"]), cost=fmt_isk(n["unit_cost"])),
                                       "gold"))
            if n.get("missing_prices"):
                bl.addWidget(_note(tr("⚠ Нет цен для {n} материал(ов).", n=len(n["missing_prices"])), "red"))
            if n.get("lines"):
                bl.addWidget(section(tr("Материалы — клик по «источнику» переключает строить ↔ купить")))
                bl.addWidget(MaterialTree(n, comps, self._toggle_component, self._places))

        return Collapsible(head, body, expanded=len((self._data or {}).get("items", [])) == 1)

    @staticmethod
    def _line_head(title, right):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(label(title, "muted"), 1)
        r = label(right)
        r.setFont(theme.font(13, 500, num=True))
        h.addWidget(r)
        return w

    def _buy_only(self, it) -> QWidget:
        n = it["node"]
        hub = {"jita": self._places["jita"], "cj": self._places["cj"]}.get(it.get("buy_hub"), "—")
        head = self._head(n, tr("{n} шт. · только покупка", n=fmt_int(n["produced"])),
                          [(fmt_isk_short(n["total_cost"]), "gold", 90)])

        def body(bl):
            h = n.get("hub") or {}
            g = QGridLayout()
            g.setHorizontalSpacing(8)
            for i, (k, v, tone) in enumerate((
                    (tr("За штуку (до {place})", place=self._places["build"]), fmt_isk(n["unit_cost"]), None),
                    (tr("{hub} + доставка", hub=self._places["jita"]), fmt_isk(h.get("jita_to_cj")),
                     "green" if it.get("buy_hub") == "jita" else None),
                    (self._places["cj"], fmt_isk(h.get("cj_local")), "green" if it.get("buy_hub") == "cj" else None),
                    (tr("Цена продажи"), fmt_isk((it.get("profit") or {}).get("sell_unit_price")), None))):
                g.addWidget(Stat(k, v, tone), 0, i)
            bl.addLayout(g)
            txt = tr("Считаем как материал: дешевле купить в {hub}.", hub=hub)
            if it.get("buy_shortage"):
                txt += tr(" ⚠ На хабе не хватает объёма под количество.")
            if (it.get("freight") or {}).get("inbound"):
                txt += tr(" Входящий фрахт {isk} ISK.", isk=fmt_isk(it["freight"]["inbound"]))
            bl.addWidget(label(txt, "muted", wrap=True))

        return Collapsible(head, body, tone="gold")
