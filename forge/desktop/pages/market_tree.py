"""Панель «Что строю» (вкладка «Что строить»): дерево разделов рынка EVE с галками — «как на
маркете»: Ships › Frigates › Standard / Faction / Advanced, Ammunition › Hybrid Charges › Standard /
Advanced, Rigs, Materials › Reaction Materials… Снятый раздел (со всеми подразделами) не
предлагается ни в ТОП, ни в «Дешевле купить», ни в «Из остатков». Выбор сразу (с задержкой на
серию щелчков) пишется в forge.toml — [recommend] exclude_market_group_ids."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
)

from .. import theme
from ..widgets import (
    Card,
    StatusLine,
    button,
    check,
    err_text,
    label,
    plural,
    run_bg,
    tr,
)

_ID = Qt.ItemDataRole.UserRole          # id узла (market_group_id; 0 — без раздела маркета)
_CHAIN = Qt.ItemDataRole.UserRole + 1   # все market_group_id склеенной цепочки
_OWN = Qt.ItemDataRole.UserRole + 2     # производимых по своим чертежам


class MarketTreePanel(Card):
    """``changed`` — выбор разделов сохранён (расчёты на вкладке пора пересчитать)."""

    changed = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(tr("Что строю"), "tag", "cyan", parent=parent, margins=(16, 14, 16, 16), spacing=10)
        self.ctx = ctx
        self._updating = False
        self._loaded = False
        self._ready = False
        self._saved: list[int] = []     # что сейчас в forge.toml
        self._saving = False
        self._expanded = False
        cfg = ctx.svc.load_cfg()
        self.own_only = check(tr("только свои"), cfg.ui.recommend_tree_own_only,
                              tr("Показывать только разделы, где есть свои чертежи (в «Где искать чертежи»). "
                                 "Галки скрытых разделов не теряются."))
        self.own_only.toggled.connect(self._own_only_toggled)
        self.head_right.addWidget(self.own_only)
        self.body.addWidget(label(tr("Галка — раздел участвует в «Что строить» (ТОП, «Дешевле купить», «Из "
                                     "остатков»). Снял — его предметы не предлагаются. Галка раздела "
                                     "ставит/снимает всё внутри."), "faint", wrap=True))
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("Найти раздел: Frigates, Rigs, Reaction…"))
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda _t: self._apply_view())
        tools.addWidget(self.filter, 1)
        b_all = button(tr("Все"), "check", "ghost", tip=tr("Отметить все разделы"), icon_size=13)
        b_all.clicked.connect(lambda: self._set_all(True))
        tools.addWidget(b_all)
        b_none = button(tr("Ничего"), "x", "ghost", tip=tr("Снять все — и отметить только то, что хочешь строить"),
                        icon_size=13)
        b_none.clicked.connect(lambda: self._set_all(False))
        tools.addWidget(b_none)
        expand = button("", "chevron", "ghost", tip=tr("Развернуть/свернуть всё"), icon_size=14)
        expand.clicked.connect(self._toggle_expand)
        tools.addWidget(expand)
        self.body.addLayout(tools)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([tr("РАЗДЕЛ МАРКЕТА"), tr("СВОИХ / ВСЕГО")])
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)    # до 5 уровней маркета — не съедать ширину у имён
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setFont(theme.font(11, 600, display=True, spacing=106))
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._item_changed)
        self.body.addWidget(self.tree, 1)
        self.summary = label("", "muted", wrap=True)
        self.body.addWidget(self.summary)
        self.status = StatusLine()
        self.body.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._save)
        ctx.hub.data_changed.connect(self._reload_if_loaded)

    # ------------------------------------------------------------------ загрузка
    def ensure_loaded(self):
        if not self._loaded:
            self._loaded = True
            self.reload()

    def _reload_if_loaded(self):
        if self._loaded and not self._timer.isActive() and not self._saving:
            self.reload()

    def reload(self):
        def done(res, err):
            if err:
                self.status.show_msg("err", err_text(err))
                return
            self.status.clear_msg()
            self._build(res)
        run_bg(self.ctx.svc.market_tree, done)

    def _build(self, data: dict):
        self._ready = bool(data.get("ready"))
        self._saved = sorted(int(x) for x in data.get("excluded") or [])
        expanded = self._expanded_ids()
        self._updating = True
        self.tree.clear()
        excluded = set(self._saved)
        mono = theme.font(11, 400, num=True)
        bold = theme.font(13, 600)

        def add(parent, node: dict, off: bool):
            it = QTreeWidgetItem(parent, [node["name"], f"{node['own']} / {node['total']}"])
            it.setData(0, _ID, int(node["id"]))
            it.setData(0, _CHAIN, list(node.get("chain") or [node["id"]]))
            it.setData(0, _OWN, int(node["own"]))
            flags = it.flags() | Qt.ItemFlag.ItemIsUserCheckable
            if node.get("children"):
                flags |= Qt.ItemFlag.ItemIsAutoTristate
            it.setFlags(flags)
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setFont(1, mono)
            it.setToolTip(0, node["name"])
            it.setToolTip(1, tr("По своим чертежам: {own} · всего производимых: {total}",
                                own=node["own"], total=node["total"]))
            if parent is self.tree:
                it.setFont(0, bold)
            if not node["own"]:
                for c in (0, 1):
                    it.setForeground(c, QColor(theme.MUTED))
            off = off or any(int(g) in excluded for g in (node.get("chain") or [node["id"]]))
            # Сверху вниз: галка родителя (авто-тристейт) раздаётся детям, дети уточняют.
            it.setCheckState(0, Qt.CheckState.Unchecked if off else Qt.CheckState.Checked)
            for ch in node.get("children") or []:
                add(it, ch, off)
            if int(node["id"]) in expanded:
                it.setExpanded(True)

        for root in data.get("roots") or []:
            add(self.tree, root, False)
        self._updating = False
        if not self._ready:
            self.status.show_msg("warn", tr("Дерева разделов рынка ещё нет в базе — обнови SDE: «Обзор → Скачать "
                                            "SDE» (один раз). До этого фильтр по разделам не работает."))
        self._apply_view()
        self._render_summary()

    def _expanded_ids(self) -> set[int]:
        out: set[int] = set()
        for it in self._items():
            if it.isExpanded():
                out.add(int(it.data(0, _ID)))
        return out

    def _items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    # ------------------------------------------------------------------ вид
    def _own_only_toggled(self, on: bool):
        self._apply_view()
        run_bg(lambda: self.ctx.svc.put_config({"ui": {"recommend_tree_own_only": bool(on)}}))

    def _apply_view(self):
        """Скрыть разделы без своих чертежей («только свои») и не подходящие под поиск."""
        q = self.filter.text().strip().lower()
        own_only = self.own_only.isChecked()

        def walk(it, ancestor_hit: bool) -> bool:
            hit = bool(q) and q in it.text(0).lower()
            child_visible = False
            for i in range(it.childCount()):
                child_visible = walk(it.child(i), ancestor_hit or hit) or child_visible
            visible = (not own_only or int(it.data(0, _OWN)) > 0) and (
                not q or hit or ancestor_hit or child_visible)
            it.setHidden(not visible)
            if q and child_visible:
                it.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i), False)

    def _toggle_expand(self):
        self._expanded = not self._expanded
        if self._expanded:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()

    # ------------------------------------------------------------------ галки
    def excluded(self) -> list[int]:
        """Снятые разделы: верхние полностью снятые узлы (подразделы внутри не перечисляются)."""
        out: list[int] = []

        def walk(it):
            st = it.checkState(0)
            if st == Qt.CheckState.Unchecked:
                out.append(int(it.data(0, _ID)))
            elif st == Qt.CheckState.PartiallyChecked:
                for i in range(it.childCount()):
                    walk(it.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return sorted(out)

    def _set_all(self, on: bool):
        self._updating = True
        st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, st)
        self._updating = False
        self._changed_now()

    def _item_changed(self, _it, col):
        if self._updating or col != 0:
            return
        self._changed_now()

    def _changed_now(self):
        self._render_summary()
        self._timer.start()

    def _render_summary(self):
        if not self.tree.topLevelItemCount():
            self.summary.setText("")
            return
        ex = self.excluded()
        roots = self.tree.topLevelItemCount()
        if not ex:
            self.summary.setText(tr("Отмечено всё — предлагается любой производимый предмет."))
        elif len(ex) >= roots and all(self.tree.topLevelItem(i).checkState(0) == Qt.CheckState.Unchecked
                                      for i in range(roots)):
            self.summary.setText(tr("Ничего не отмечено — предлагать нечего. Отметь разделы, которые хочешь "
                                    "строить."))
        else:
            self.summary.setText(tr("Снято: {n} {what} — их предметы не предлагаются.", n=len(ex),
                                    what=plural(len(ex), "раздел", "раздела", "разделов")))

    def _save(self):
        if self._saving:
            self._timer.start()  # дождаться прошлой записи — сохранить свежий выбор следом
            return
        ex = self.excluded()
        if ex == self._saved:
            return
        self._saving = True

        def done(_r, err):
            self._saving = False
            if err:
                self.status.show_msg("err", err_text(err))
                return
            self._saved = ex
            self.changed.emit()
        run_bg(lambda: self.ctx.svc.put_config({"recommend": {"exclude_market_group_ids": ex}}), done)
