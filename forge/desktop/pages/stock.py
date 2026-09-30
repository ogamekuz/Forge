"""Вкладка «Склад»: что считать остатками — по системам, станциям/структурам и контейнерам.

Склад выбирается по местам, где лежит твоё добро:
- режим «Авто» — структуры стройки + локации чертежей, «Выбрать» —
  дерево с галками: система целиком (включая новые структуры в ней), отдельная станция/
  структура, контейнер/корабль; снятая галка внутри выбранной системы — исключение;
- фильтры: чьи ассеты, модули в фите, собранные корабли, флаги (трюм, дронбей, Asset Safety…);
  дерево сразу показывает их действие: что отсечено целиком — серым «не склад»;
- запреты: предметы/группы, которые никогда не берутся со склада; «не трогать» — N штук в запасе.
Остатки вне системы стройки годятся: отчёт покажет, откуда брать, и довезёт их из C-J6MT/Jita
тем же фрахтом (прочие системы помечаются — доставку оттуда Forge не знает).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..widgets import (
    N_,
    Busy,
    Card,
    ChipList,
    Col,
    DataTable,
    ElidedLabel,
    FlowLayout,
    NumberEdit,
    Page,
    Segmented,
    StatusLine,
    TypeIcon,
    button,
    check,
    clear_layout,
    err_text,
    fmt_int,
    fmt_isk_short,
    group_picker,
    item_picker,
    label,
    run_bg,
    scroll_page,
    section,
    tr,
)

HUB_LABEL = {"gplb_c": N_("место стройки"), "c_j6mt": N_("рынок / хаб"), "jita": "Jita", None: ""}
FILTERED_ROLE = N_("не склад · фильтр")
FITTED_PREFIXES = ("HiSlot", "MedSlot", "LoSlot", "RigSlot", "SubSystemSlot", "ServiceSlot")
FLAG_RU = {
    "Cargo": N_("Трюм кораблей"), "DroneBay": N_("Дронбей"), "FleetHangar": N_("Флотский ангар"),
    "AssetSafety": "Asset Safety", "SpecializedFuelBay": N_("Топливный отсек"),
    "FighterBay": N_("Ангар истребителей"), "FighterTube": N_("Пусковые истребителей"),
    "ShipHangar": N_("Корабельный ангар (капиталы)"), "SpecializedAmmoHold": N_("Отсек боеприпасов"),
    "SpecializedOreHold": N_("Рудный трюм"), "ExpeditionHold": N_("Экспедиционный трюм"),
    "Deliveries": N_("Доставки"), "Unlocked": N_("Содержимое контейнеров"),
    "Locked": N_("Запертое в контейнерах"), "AutoFit": N_("AutoFit (структуры)"),
    "Hangar": N_("Ангар станции/структуры"), "HiddenModifiers": N_("Скрытые модификаторы"),
    "CorpseBay": N_("Отсек трупов"), "QuafeBay": "Quafe",
}


def _flag_base(flag: str) -> str:
    for p in ("FighterTube", "HiSlot", "MedSlot", "LoSlot", "RigSlot", "SubSystemSlot", "ServiceSlot"):
        if flag.startswith(p):
            return p
    return flag


class StockPage(Page):
    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, body = scroll_page()
        outer.addWidget(scroll)
        self._data = None
        self._cfg = None
        self._updating = False
        self._loading = False  # load() расставляет галки — не гонять перестройку дерева на каждую
        self._first_render = False
        self._expanded_memory: set = set()
        self._filtered: dict[tuple, str] = {}  # узел, целиком отсечённый фильтрами -> подсказка
        self.sel_systems: set[int] = set()
        self.sel_locations: set[int] = set()
        self.sel_excluded: set[int] = set()
        self._keep: dict[int, tuple[str, int]] = {}
        self._ex_types: dict[int, str] = {}
        self._ex_groups: dict[int, str] = {}

        self.warn = StatusLine()
        body.addWidget(self.warn)
        row = QHBoxLayout()
        row.setSpacing(14)

        # --- дерево локаций ---
        self.tree_card = Card(tr("Где что лежит"), "planet", "cyan")
        self.mode = Segmented([("auto", tr("Авто"), "sync"), ("custom", tr("Выбрать вручную"), "pin")],
                              small=True)
        self.mode.changed.connect(self._mode_changed)
        self.tree_card.head_right.addWidget(self.mode)
        self.mode_note = label("", "faint", wrap=True)
        self.tree_card.body.addWidget(self.mode_note)
        tools = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("Фильтр по системе/станции…"))
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        tools.addWidget(self.filter, 1)
        self.btn_from_auto = button(tr("Взять выбор «Авто» за основу"), "copy",
                                    tip=tr("Отметить то же, что считает режим «Авто» (структуры стройки + "
                                           "локации чертежей), и дальше править вручную"))
        self.btn_from_auto.clicked.connect(self._from_auto)
        tools.addWidget(self.btn_from_auto)
        expand = button("", "chevron", "ghost", tip=tr("Развернуть/свернуть всё"), icon_size=14)
        expand.clicked.connect(self._toggle_expand)
        tools.addWidget(expand)
        self.tree_card.body.addLayout(tools)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([tr("ГДЕ"), tr("ПРЕДМЕТОВ"), tr("ОЦЕНКА (JITA)"), tr("РОЛЬ")])
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setFont(theme.font(11, 600, display=True, spacing=106))
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in ((1, 76), (2, 96), (3, 104)):
            self.tree.header().setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.tree.setColumnWidth(c, w)
        self.tree.setMinimumHeight(460)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree_card.body.addWidget(self.tree, 1)
        self.sel_summary = label("", "muted", wrap=True)
        self.tree_card.body.addWidget(self.sel_summary)
        self.ghost_note = label("", "faint", wrap=True)
        self.ghost_note.hide()
        self.tree_card.body.addWidget(self.ghost_note)
        row.addWidget(self.tree_card, 5)
        # Галки «Чьи ассеты считать» и фильтры перестраивают дерево сразу (без сохранения), с
        # задержкой — чтобы серия щелчков не гоняла пересчёт на каждый.
        self._tree_timer = QTimer(self)
        self._tree_timer.setSingleShot(True)
        self._tree_timer.setInterval(350)
        self._tree_timer.timeout.connect(self._reload_tree)

        # --- фильтры и запреты ---
        side = QVBoxLayout()
        side.setSpacing(14)
        f = Card(tr("Фильтры"), "filter", "gold")
        f.body.addWidget(section(tr("Чьи ассеты считать")))
        self.chars_host = QWidget()
        self.chars_flow = FlowLayout(self.chars_host, spacing=10)
        f.body.addWidget(self.chars_host)
        f.body.addWidget(label(tr("Ни одна галка не снята = все персонажи."), "faint"))
        f.body.addWidget(section(tr("Не считать складом")))
        self.ex_fitted = check(tr("Модули в слотах фита кораблей и структур"), True,
                               tr("Снятие с фита не бесплатно — зафитованное не остаток"))
        self.ex_ships = check(tr("Собранные корабли (внутри что-то есть)"), False,
                              tr("Корабль с фитом/грузом — это чей-то корабль, а не корпус на складе. "
                                 "Сам груз при этом остаётся складом (если трюм не исключён ниже)."))
        self.ex_fitted.toggled.connect(self._schedule_tree)
        self.ex_ships.toggled.connect(self._schedule_tree)
        f.body.addWidget(self.ex_fitted)
        f.body.addWidget(self.ex_ships)
        self.flags_host = QWidget()
        self.flags_grid = QGridLayout(self.flags_host)
        self.flags_grid.setContentsMargins(0, 0, 0, 0)
        self.flags_grid.setHorizontalSpacing(12)
        self.flags_grid.setVerticalSpacing(4)
        f.body.addWidget(self.flags_host)
        self._flag_checks: dict[str, QCheckBox] = {}
        side.addWidget(f)

        z = Card(tr("Запреты и запас"), "ban", "red")
        z.body.addWidget(section(tr("Никогда не брать со склада")))
        self.ex_types_chips = ChipList(tr("предметы не выбраны"), with_type_icons=True)
        self.ex_types_chips.removed.connect(lambda k: (self._ex_types.pop(int(k), None), self._bans_changed()))
        z.body.addWidget(self.ex_types_chips)
        p1 = item_picker(self.svc, tr("Предмет, который не трогать вообще…"))
        p1.picked.connect(lambda r: (self._ex_types.__setitem__(int(r["type_id"]), r["name"]), self._bans_changed()))
        z.body.addWidget(p1)
        self.ex_groups_chips = ChipList(tr("группы не выбраны"), icon="filter", tone="red")
        self.ex_groups_chips.removed.connect(lambda k: (self._ex_groups.pop(int(k), None), self._bans_changed()))
        z.body.addWidget(self.ex_groups_chips)
        p2 = group_picker(self.svc, tr("EVE-группа, которую не трогать (напр. Fuel Block)…"))
        p2.picked.connect(lambda r: (self._ex_groups.__setitem__(int(r["group_id"]), r["name"]), self._bans_changed()))
        z.body.addWidget(p2)
        z.body.addWidget(section(tr("Не трогать N штук (неприкосновенный запас)")))
        self.keep_host = QVBoxLayout()
        self.keep_host.setSpacing(4)
        z.body.addLayout(self.keep_host)
        p3 = item_picker(self.svc, tr("Добавить предмет в запас…"))
        p3.picked.connect(self._keep_add)
        z.body.addWidget(p3)
        side.addWidget(z)

        acts = QHBoxLayout()
        self.btn_save = button(tr("Сохранить склад"), "check", "hero", icon_size=14)
        self.btn_save.clicked.connect(self.save)
        acts.addWidget(self.btn_save, 1)
        reset = button(tr("Сбросить"), "repeat")
        reset.clicked.connect(self.load)
        acts.addWidget(reset)
        side.addLayout(acts)
        self.status = StatusLine()
        side.addWidget(self.status)
        side.addStretch(1)
        row.addLayout(side, 3)
        body.addLayout(row)

        # --- содержимое ---
        self.cont = Card(tr("Сейчас на складе"), "boxes", "green")
        self.cont_search = QLineEdit()
        self.cont_search.setPlaceholderText(tr("Найти на складе…"))
        self.cont_search.setClearButtonEnabled(True)
        self.cont_search.setFixedWidth(260)
        self.cont_search.returnPressed.connect(self.load_contents)
        self.cont.head_right.addWidget(self.cont_search)
        self.busy = Busy()
        self.cont.body.addWidget(self.busy)
        self.cont_table = DataTable([
            Col("name", tr("Предмет"), icon_key="type_id", stretch=True),
            Col("quantity", tr("Можно взять"), lambda v, _r: fmt_int(v), "right"),
            Col("kept", tr("Не трогать"), lambda v, _r: fmt_int(v) if v else "", "right",
                tone=lambda v, _r: "gold" if v else None),
            Col("value", tr("Оценка (Jita)"), lambda v, _r: fmt_isk_short(v), "right"),
            Col("lots", tr("Где лежит"), lambda v, _r: " · ".join(f"{x['label']} ×{fmt_int(x['quantity'])}" for x in (v or [])[:3])
                + (" · " + tr("ещё {n}", n=len(v) - 3) if v and len(v) > 3 else ""), width=420,
                tip=lambda v, _r: "\n".join(f"{x['label']} ×{fmt_int(x['quantity'])}" for x in (v or []))),
        ])
        self.cont_table.setMinimumHeight(360)
        self.cont.body.addWidget(self.cont_table)
        self.cont.body.addWidget(label(tr("Список — ПО СОХРАНЁННОЙ настройке: выбранные локации + «Чьи ассеты "
                                          "считать», «Не считать складом», запреты и запас (после «Сохранить "
                                          "склад» обновится). Оценка — по sell Jita, только для ориентира."),
                                       "faint", wrap=True))
        body.addWidget(self.cont)
        body.addStretch(1)
        self._loaded = False
        self._expanded = False
        ctx.hub.data_changed.connect(self.load)

    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self.load()

    # ------------------------------------------------------------------ загрузка
    def load(self):
        svc = self.svc

        def work():
            cfg = svc.load_cfg()
            sc = cfg.stock
            names = svc.type_names([*sc.exclude_type_ids, *(k.type_id for k in sc.keep)])
            gnames = svc.group_names(sc.exclude_group_ids)
            return (svc.stock_locations(sc.character_ids), cfg, svc.characters(), svc.asset_flags(),
                    names, gnames)

        def done(res, err):
            if err:
                self.status.show_msg("err", err_text(err))
                return
            self._data, self._cfg, chars, flags, names, gnames = res
            sc = self._cfg.stock
            self._loading = True  # дерево уже посчитано по сохранённому — галки не должны его дёргать
            self.sel_systems = set(sc.system_ids)
            self.sel_locations = set(sc.location_ids)
            self.sel_excluded = set(sc.exclude_location_ids)
            self.mode.set_value(sc.mode if sc.mode in ("auto", "custom") else "auto")
            self.ex_fitted.setChecked(sc.exclude_fitted)
            self.ex_ships.setChecked(sc.exclude_assembled_ships)
            self._fill_chars(chars, sc.character_ids)
            self._fill_flags(flags, sc.exclude_flags)
            self._ex_types = {t: names.get(str(t), f"#{t}") for t in sc.exclude_type_ids}
            self._ex_groups = {g: gnames.get(str(g), f"#{g}") for g in sc.exclude_group_ids}
            self._keep = {k.type_id: (names.get(str(k.type_id), f"#{k.type_id}"), k.quantity) for k in sc.keep}
            self._loading = False
            self._render_bans()
            self._render_keep()
            self._build_tree()
            self._mode_changed(self.mode.value(), silent=True)
            self._data_notes()
            self.load_contents()
        run_bg(work, done)

    def _schedule_tree(self, *_args):
        """Галки персонажей/фильтров поменялись — перестроить дерево (с задержкой)."""
        if not self._loading:
            self._tree_timer.start()

    def _bans_changed(self):
        self._render_bans()
        self._schedule_tree()

    def _tree_filters(self) -> dict:
        """Фильтры по предметам — по текущим (несохранённым) галкам, для дерева."""
        return {"exclude_fitted": self.ex_fitted.isChecked(),
                "exclude_assembled_ships": self.ex_ships.isChecked(),
                "exclude_flags": [f for f, cb in self._flag_checks.items() if cb.isChecked()],
                "exclude_type_ids": list(self._ex_types), "exclude_group_ids": list(self._ex_groups)}

    def _data_notes(self):
        """Предупреждение о структурах без системы и строка о скрытых кораблях вне ассетов."""
        data = self._data or {}
        unresolved = data.get("unresolved") or 0
        if unresolved:
            self.warn.show_msg("warn", tr("{n} структур(ы) пока без имени и системы — их не "
                                          "получится выбрать по системе. «Обзор → Персонажи + рынки структур»: "
                                          "при синке Forge спросит ESI (нужен доступ к докингу у кого-то из "
                                          "персонажей). Пока их можно отметить поштучно.", n=unresolved))
        else:
            self.warn.clear_msg()
        ghosts = data.get("ghosts") or {}
        ships = ghosts.get("ships") or 0
        self.ghost_note.setText(
            tr("Скрыто кораблей вне ассетов: {ships} ({items} предм.) — ESI отдаёт "
               "только их модули и риги, самих кораблей в ассетах нет (напр. корабль выставлен в контракт). "
               "Системы у них нет, в склад они не входят.", ships=ships, items=fmt_int(ghosts.get("items") or 0))
            if ships else "")
        self.ghost_note.setVisible(bool(ships))

    def _picked_chars(self) -> list[int]:
        """Отмеченные в «Чьи ассеты считать»; все отмечены (или ни одного) — [] = все персонажи."""
        chars = getattr(self, "_char_checks", {})
        picked = [cid for cid, cb in chars.items() if cb.isChecked()]
        return [] if len(picked) == len(chars) else picked

    def _reload_tree(self):
        """Дерево — по текущим (ещё не сохранённым) галкам «Чьи ассеты считать» и фильтрам;
        раскрытое остаётся раскрытым."""
        chars = self._picked_chars()
        filters = self._tree_filters()
        expanded = self._expanded_keys()

        def done(res, err):
            if err:
                self.status.show_msg("err", err_text(err))
                return
            self._data = res
            self._build_tree(expanded)
            self._data_notes()
        run_bg(lambda: self.svc.stock_locations(chars, filters), done)

    def _expanded_keys(self) -> set:
        """Раскрытые узлы ("system"|"loc", id): видимые — по их состоянию, пропавшие из дерева
        (галку персонажа сняли) — как были, чтобы вернуться раскрытыми вместе с галкой."""
        keys = set(self._expanded_memory)

        def walk(it):
            key = tuple(it.data(0, Qt.ItemDataRole.UserRole))
            (keys.add if it.isExpanded() else keys.discard)(key)
            for i in range(it.childCount()):
                walk(it.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        self._expanded_memory = keys
        return keys

    def load_contents(self):
        q = self.cont_search.text().strip()
        self.busy.start(tr("Считаю склад…"))

        def done(res, err):
            self.busy.stop()
            if err:
                self.status.show_msg("err", err_text(err))
                return
            self.cont_table.set_rows(res["rows"])
            n, isk = fmt_int(res["types"]), fmt_isk_short(res["total_value"])
            self.cont.set_meta(tr("{n} позиций · ~{isk} ISK · показано {shown}", n=n, isk=isk, shown=res["shown"])
                               if res["shown"] < res["types"] else tr("{n} позиций · ~{isk} ISK", n=n, isk=isk))
        run_bg(lambda: self.svc.stock_contents(q, 600), done)

    def _fill_chars(self, chars, selected):
        clear_layout(self.chars_flow)
        self._char_checks = {}
        sel = set(selected)
        for c in chars:
            cb = check(c["name"], not sel or c["character_id"] in sel)
            cb.toggled.connect(self._schedule_tree)
            self._char_checks[c["character_id"]] = cb
            self.chars_flow.addWidget(cb)
        self.chars_host.updateGeometry()

    def _fill_flags(self, flags, excluded):
        clear_layout(self.flags_grid)
        self._flag_checks = {}
        agg: dict[str, int] = {}
        for f in flags:
            base = _flag_base(f["flag"])
            if base in FITTED_PREFIXES:
                continue  # слоты фита — отдельной галкой выше
            agg[base] = agg.get(base, 0) + f["count"]
        for extra in excluded:
            agg.setdefault(extra, 0)
        ex = set(excluded)
        for i, (flag, n) in enumerate(sorted(agg.items(), key=lambda kv: -kv[1])):
            cb = check(f"{tr(FLAG_RU.get(flag, flag))} ({flag}) · {fmt_int(n)}", flag in ex)
            if flag == "Hangar":
                cb.setToolTip(tr("Осторожно: это основной ангар станций/структур — почти весь склад"))
            cb.toggled.connect(self._schedule_tree)
            self._flag_checks[flag] = cb
            self.flags_grid.addWidget(cb, i // 2, i % 2)

    # ------------------------------------------------------------------ дерево
    def _build_tree(self, expanded: set | None = None):
        """Дерево из ``self._data``. ``expanded`` — ключи раскрытых узлов (перестройка по галкам
        персонажей); None — первая отрисовка: раскрыть системы, где есть выбранное."""
        self._updating = True
        self._first_render = expanded is None
        self._filtered = {}
        self.tree.clear()
        mono = theme.font(12, 400, num=True)
        for s in self._data["systems"]:
            sid = s.get("system_id")
            sec = s.get("security")
            title = s["name"] + (f"   {sec:.1f}" if sec is not None else "")
            it = QTreeWidgetItem(self.tree, [title, fmt_int(s["items"]), fmt_isk_short(s["value"]),
                                             tr(HUB_LABEL.get(s.get("hub"), ""))])
            it.setIcon(0, theme.icon("planet", theme.security_color(sec), 16))
            it.setForeground(0, QColor(theme.TEXT))
            f = QFont(theme.font(13, 600))
            it.setFont(0, f)
            it.setData(0, Qt.ItemDataRole.UserRole, ("system", sid))
            for c in (1, 2):
                it.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                it.setFont(c, mono)
            if s.get("hub"):
                it.setForeground(3, QColor(theme.tone_text({"gplb_c": "green", "c_j6mt": "gold", "jita": "cyan"}[s["hub"]])))
            if sid is None:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                it.setToolTip(0, tr("Структуры без известной системы — отмечай поштучно (или синкни персонажей)"))
            else:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setToolTip(0, tr("Галка на системе — весь склад в ней (включая новые структуры)"))
            for loc in s["locations"]:
                li = QTreeWidgetItem(it, [loc["name"], fmt_int(loc["items"]), fmt_isk_short(loc["value"]), ""])
                icon = {"station": "planet", "structure": "anvil", "system": "star"}.get(loc["kind"], "alert")
                li.setIcon(0, theme.icon(icon, theme.tone_text("cyan") if loc["resolved"] else theme.FAINT, 16))
                li.setData(0, Qt.ItemDataRole.UserRole, ("loc", loc["location_id"]))
                li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                li.setToolTip(0, f"{loc['name']} · id {loc['location_id']}")
                for c in (1, 2):
                    li.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    li.setFont(c, mono)
                if not loc["resolved"]:
                    li.setForeground(0, QColor(theme.MUTED))
                self._note_filters(li, loc, loc.get("passing") == 0)
                for ch in loc.get("children") or []:
                    ci = QTreeWidgetItem(li, [ch["name"], fmt_int(ch["items"]), fmt_isk_short(ch["value"]), ""])
                    ship = bool(ch.get("ship"))
                    ci.setIcon(0, theme.icon("ship" if ship else "box", theme.MUTED, 15))
                    ci.setData(0, Qt.ItemDataRole.UserRole, ("loc", ch["location_id"]))
                    ci.setFlags(ci.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    ci.setToolTip(0, tr("корабль · id {id}", id=ch["location_id"]) if ship
                                  else tr("контейнер · id {id}", id=ch["location_id"]))
                    for c in (1, 2):
                        ci.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                        ci.setFont(c, mono)
                    self._note_filters(ci, ch, ch.get("passing") == 0 and not ch.get("self_passes", True))
        if expanded:
            def walk(it):
                if tuple(it.data(0, Qt.ItemDataRole.UserRole)) in expanded:
                    it.setExpanded(True)
                for i in range(it.childCount()):
                    walk(it.child(i))
            for i in range(self.tree.topLevelItemCount()):
                walk(self.tree.topLevelItem(i))
        self._updating = False
        self._refresh_checks()
        self._apply_filter(self.filter.text())

    def _note_filters(self, it, node: dict, fully: bool):
        """Что фильтры справа делают с узлом: целиком отсекают — запомнить (серым «не склад» в
        ``_refresh_checks``), частично — подсказка у числа предметов."""
        passing = node.get("passing")
        if passing is None:  # сервис без подсчёта фильтров
            return
        why = ", ".join(f"{r} ×{fmt_int(n)}" for r, n in (node.get("reasons") or [])[:4])
        key = ("loc", node["location_id"])
        if fully:
            self._filtered[key] = tr("Не склад: всё здесь отсекают фильтры справа — {why}. Галка места "
                                     "тут ни при чём: фильтры действуют на предметы в выбранных местах.", why=why)
        elif passing < node["items"]:
            it.setToolTip(1, tr("Проходят фильтры: {passing} из {items} (отсечено: {why})",
                                passing=fmt_int(passing), items=fmt_int(node["items"]), why=why))

    def _state_of(self, it, parent_on: bool) -> bool:
        kind, key = it.data(0, Qt.ItemDataRole.UserRole)
        custom = self.mode.value() == "custom"
        if kind == "system":
            return bool(custom and key is not None and key in self.sel_systems)
        if not custom:
            auto = set((self._data or {}).get("auto_location_ids") or [])
            return parent_on or key in auto
        if key in self.sel_excluded:
            return False
        if key in self.sel_locations:
            return True
        return parent_on

    def _refresh_checks(self):
        self._updating = True
        custom = self.mode.value() == "custom"

        def walk(it, parent_on):
            on = self._state_of(it, parent_on)
            tip = self._filtered.get(tuple(it.data(0, Qt.ItemDataRole.UserRole)))
            if tip is not None:  # фильтры отсекают всё — не склад, где бы ни лежало
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Unchecked)
                it.setForeground(0, QColor(theme.FAINT))
                it.setText(3, tr(FILTERED_ROLE))
                it.setForeground(3, QColor(theme.FAINT))
                it.setToolTip(0, tip)
                it.setToolTip(3, tip)
            elif it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                it.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                if custom:
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEnabled)
            dim = not on or tip is not None
            for c in (1, 2):
                it.setForeground(c, QColor(theme.FAINT if dim else theme.tone_text("gold") if c == 2 else theme.MUTED))
            any_on = on
            for i in range(it.childCount()):
                any_on = walk(it.child(i), on) or any_on
            return any_on

        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            has_selected = walk(top, False)
            if self._first_render and has_selected:
                top.setExpanded(True)  # сразу видно, что сейчас входит в склад
        self._first_render = False
        self._updating = False
        self._summary()

    def _item_changed(self, it, col):
        if self._updating or col != 0:
            return
        if self.mode.value() != "custom":
            self._refresh_checks()  # в «Авто» дерево только показывает выбор
            self.status.show_msg("info", tr("Режим «Авто» — места выбираются сами (структуры стройки + локации "
                                            "чертежей). Переключи на «Выбрать вручную», чтобы отмечать системы и "
                                            "локации."), 6000)
            return
        kind, key = it.data(0, Qt.ItemDataRole.UserRole)
        on = it.checkState(0) == Qt.CheckState.Checked
        if kind == "system":
            (self.sel_systems.add if on else self.sel_systems.discard)(key)
        else:
            inherited = self._effective(it.parent())
            if on:
                self.sel_excluded.discard(key)
                if not inherited:
                    self.sel_locations.add(key)
            else:
                self.sel_locations.discard(key)
                if inherited:
                    self.sel_excluded.add(key)
        self._refresh_checks()

    def _effective(self, it) -> bool:
        """Входит ли узел в склад по текущему (несохранённому) выбору."""
        if it is None:
            return False
        kind, key = it.data(0, Qt.ItemDataRole.UserRole)
        if kind == "system":
            return key is not None and key in self.sel_systems
        if key in self.sel_excluded:
            return False
        if key in self.sel_locations:
            return True
        return self._effective(it.parent())

    def _summary(self):
        if self.mode.value() != "custom":
            self.sel_summary.setText(tr("Склад = структуры стройки из «Настроек» + все локации «Где искать "
                                        "чертежи» (и всё внутри, кроме отсечённого фильтрами)."))
            return
        names = {}
        for i in range(self.tree.topLevelItemCount()):
            s = self.tree.topLevelItem(i)
            _kind, key = s.data(0, Qt.ItemDataRole.UserRole)
            names[("system", key)] = s.text(0).split("   ")[0]
        sys_names = [names.get(("system", sid), str(sid)) for sid in self.sel_systems]
        parts = []
        if sys_names:
            parts.append(tr("системы: {names}", names=", ".join(sys_names)))
        if self.sel_locations:
            parts.append(tr("отдельных локаций: {n}", n=len(self.sel_locations)))
        if self.sel_excluded:
            parts.append(tr("исключений: {n}", n=len(self.sel_excluded)))
        self.sel_summary.setText(tr("Выбрано — {parts}", parts="; ".join(parts)) if parts else
                                 tr("Ничего не выбрано — склад пуст (отметь системы или локации)."))

    def _mode_changed(self, key, silent=False):
        custom = key == "custom"
        self.btn_from_auto.setVisible(custom)
        self.mode_note.setText(
            (tr("Отмечай системы целиком (включая будущие структуры в них) или отдельные станции/структуры/"
                "контейнеры. Снятая галка внутри выбранной системы — исключение.") if custom else
             tr("Авто: структуры стройки + локации «Где искать чертежи»."))
            + " " + tr("Галка — место входит в склад; фильтры справа действуют на предметы в этих местах: "
                       "что они отсекают целиком — серым «не склад». Дерево — сразу по галкам, до сохранения."))
        self._refresh_checks()

    def _from_auto(self):
        auto = set((self._data or {}).get("auto_location_ids") or [])
        self.sel_locations |= auto
        self.sel_excluded -= auto
        self._refresh_checks()
        self.status.show_msg("info", tr("Отмечены локации режима «Авто» — поправь и сохрани."), 6000)

    def _apply_filter(self, text):
        q = (text or "").strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            s = self.tree.topLevelItem(i)
            any_child = False
            for j in range(s.childCount()):
                c = s.child(j)
                hit = not q or q in c.text(0).lower() or q in s.text(0).lower()
                c.setHidden(not hit)
                any_child = any_child or hit
            s.setHidden(not any_child and bool(q) and q not in s.text(0).lower())
            if q and any_child:
                s.setExpanded(True)

    def _toggle_expand(self):
        self._expanded = not self._expanded
        if self._expanded:
            self.tree.expandAll()
        else:
            self.tree.collapseAll()

    # ------------------------------------------------------------ запреты
    def _render_bans(self):
        self.ex_types_chips.set_items([(t, n, t) for t, n in self._ex_types.items()])
        self.ex_groups_chips.set_items([(g, n) for g, n in self._ex_groups.items()])

    def _render_keep(self):
        clear_layout(self.keep_host)
        if not self._keep:
            self.keep_host.addWidget(label(tr("— запас не задан"), "faint"))
            return
        for tid, (name, qty) in self._keep.items():
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            h.addWidget(TypeIcon(tid, 20))
            h.addWidget(ElidedLabel(name), 1)
            q = NumberEdit(qty, integer=True, width=110, compact=True)
            q.editingFinished.connect(lambda t=tid, e=q: self._keep.__setitem__(t, (self._keep[t][0], e.int_value(0))))
            h.addWidget(q)
            rm = button("", "x", "ghost", tip=tr("Убрать из запаса"), icon_size=12)
            rm.setFixedSize(24, 24)
            rm.clicked.connect(lambda _=False, t=tid: (self._keep.pop(t, None), self._render_keep()))
            h.addWidget(rm)
            self.keep_host.addWidget(w)

    def _keep_add(self, r):
        tid = int(r["type_id"])
        if tid not in self._keep:
            self._keep[tid] = (r["name"], 0)
            self._render_keep()

    # --------------------------------------------------------------- сохранение
    def save(self):
        char_ids = self._picked_chars()
        stock = {
            "mode": self.mode.value() or "auto",
            "system_ids": sorted(x for x in self.sel_systems if x is not None),
            "location_ids": sorted(self.sel_locations),
            "exclude_location_ids": sorted(self.sel_excluded),
            "character_ids": char_ids,
            "exclude_fitted": self.ex_fitted.isChecked(),
            "exclude_assembled_ships": self.ex_ships.isChecked(),
            "exclude_flags": [f for f, cb in self._flag_checks.items() if cb.isChecked()],
            "exclude_type_ids": list(self._ex_types),
            "exclude_group_ids": list(self._ex_groups),
            "keep": [{"type_id": t, "quantity": q} for t, (_n, q) in self._keep.items() if q > 0],
        }
        empty_msg = tr("В режиме «Выбрать» ничего не отмечено — склад будет пустым. Сохранено всё равно.")
        if stock["mode"] == "custom" and not (stock["system_ids"] or stock["location_ids"]):
            self.status.show_msg("warn", empty_msg, 8000)
        self.btn_save.setEnabled(False)

        def done(_r, err):
            self.btn_save.setEnabled(True)
            if err:
                self.status.show_msg("err", err_text(err))
                return
            if not self.status.isVisible() or empty_msg not in self.status.text():
                self.status.show_msg("ok", tr("Склад сохранён — отчёты и «Из остатков» считают по нему."), 6000)
            self.ctx.hub.config_changed.emit()
            self.load_contents()
        run_bg(lambda: self.svc.put_config({"stock": stock}), done)
