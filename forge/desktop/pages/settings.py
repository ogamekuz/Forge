"""Вкладка «Настройки»: решения игрока из forge.toml — правятся здесь, без ручной правки файла.

Разделы: Производство (налоги, инвента — скиллы и декрипторы, «всегда покупать/строить»),
Станции (риги, структуры, группы),
Логистика и рынок (места, структуры, фрахт, хабы закупки, цена продажи, самоходные группы),
Рекомендации (веса, фильтры, группы ТОП, запреты), Чертежи (цены вручную, где искать),
Планировщик (варианты сравнения, потоки, запущенные джобы, значения корзины), Система (SSO,
пути). Сохранение — в forge.toml (комментарии в нём не сохраняются, справочник — forge.example.toml).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..widgets import (
    N_,
    Card,
    ChipList,
    ElidedLabel,
    NumberEdit,
    Page,
    Segmented,
    StatusLine,
    TypeIcon,
    button,
    category_picker,
    check,
    clear_layout,
    err_text,
    fmt_int,
    fmt_isk_short,
    form_row,
    group_picker,
    hline,
    item_picker,
    label,
    rig_picker,
    run_bg,
    scroll_page,
    section,
    system_picker,
    tr,
)

ROLES = [("reaction", N_("реакции")), ("invention", N_("инвента")), ("copy", N_("копирование")),
         ("component", N_("T2-компоненты (по группам)")), ("manufacturing", N_("производство (остальное)"))]
# откуда известна система станции (core.facility_system) — для подписи в редакторе станции
SYSTEM_HOW = {"structure": N_("система структуры «Где стоит»"), "station": N_("NPC-станция «Где стоит»"),
              "system": N_("указана системой"),
              "build": N_("система стройки — структура ещё не известна (синк персонажей)")}


class IdChips(QWidget):
    """Набор id с подписями: чипы + поиск. ``kind`` — 'type' | 'group' | 'category' | 'rig'."""

    def __init__(self, svc, kind: str, placeholder: str, empty: str, tone: str = "cyan", parent=None):
        super().__init__(parent)
        self.kind = kind
        self._names: dict[int, str] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        with_icons = kind in ("type", "rig")
        self.chips = ChipList(empty, icon=None if with_icons else "tag", tone=tone, with_type_icons=with_icons)
        self.chips.removed.connect(self._remove)
        v.addWidget(self.chips)
        picker = {"type": item_picker, "group": group_picker, "category": category_picker,
                  "rig": rig_picker}[kind](svc, placeholder)
        key = {"type": "type_id", "rig": "type_id", "group": "group_id", "category": "category_id"}[kind]
        picker.picked.connect(lambda r: self._add(int(r[key]), r["name"]))
        v.addWidget(picker)

    def set(self, ids, names: dict[str, str]):
        self._names = {int(i): names.get(str(i), f"#{i}") for i in ids}
        self._render()

    def ids(self) -> list[int]:
        return list(self._names)

    def _add(self, i, name):
        self._names[i] = name
        self._render()

    def _remove(self, i):
        self._names.pop(int(i), None)
        self._render()

    def _render(self):
        self.chips.set_items([(i, n, i) for i, n in self._names.items()])


def _num_grid(fields, values: dict, width=130) -> tuple[QGridLayout, dict]:
    g = QGridLayout()
    g.setHorizontalSpacing(12)
    g.setVerticalSpacing(8)
    edits = {}
    for r, (key, title, tip) in enumerate(fields):
        lb = label(tr(title), "label")
        lb.setWordWrap(True)
        if tip:
            lb.setToolTip(tr(tip))
        e = NumberEdit(values.get(key), width=width)
        if tip:
            e.setToolTip(tr(tip))
        g.addWidget(lb, r, 0)
        g.addWidget(e, r, 1, Qt.AlignmentFlag.AlignRight)
        edits[key] = e
    g.setColumnStretch(0, 1)
    return g, edits


IND_FIELDS = [
    ("rig_material_mult", N_("Множитель материалов (фолбэк, если нет станции роли)"), N_("1.0 = без бонуса")),
    ("rig_cost_mult", N_("Множитель стоимости джоба (фолбэк)"), None),
    ("time_mult", N_("Множитель времени (фолбэк)"), None),
    ("facility_tax", N_("Налог структуры, доля (фолбэк)"), "0.01 = 1%"),
    ("scc_surcharge", N_("SCC surcharge, доля"), N_("Налог SCC на джобы (сейчас 4% = 0.04)")),
    ("broker_fee", N_("Брокер при продаже, доля"), None),
    ("sales_tax", N_("Налог с продажи, доля"), None),
    ("reprocessing_efficiency", N_("Эффективность переработки, доля 0–1 (0 — не рассматривать)"),
     N_("Реальная эффективность со станции/скиллов — переработка прекурсора как альтернатива постройке")),
]
REC_FIELDS = [
    ("w_roi", N_("Вес ROI"), None), ("w_isk_hour", N_("Вес ISK/час"), None),
    ("w_liquidity", N_("Вес ликвидности"), None),
    ("min_daily_volume", N_("Мин. суточный объём (0 — без фильтра)"), None),
    ("max_capital", N_("Потолок вложения на партию, ISK (0 — нет)"), None),
    ("runs", N_("Прогонов по умолчанию"), None), ("top_per_group", N_("Размер ТОП на группу"), None),
    ("min_cost_ratio", N_("Фильтр битых чертежей: себестоимость / цена ≥"),
     N_("Отсекает артефакты SDE вроде «1 Tritanium»")),
    ("winsor_pct", N_("Винзоризация выбросов, доля"), None),
    ("buy_cheaper_max_savings_pct", N_("«Дешевле купить»: макс. экономия, доля 0–1"),
     N_("Выше — мусор SDE (компрессия и т.п.)")),
]


class FacilityEditor(QWidget):
    """Одна станция (facility): роль, где стоит, тип структуры, риги, группы/категории, бонусы."""

    def __init__(self, svc, f: dict, names: dict, loc_options: list[tuple[int, str]], on_remove, parent=None):
        super().__init__(parent)
        self.f = dict(f)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 6)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.name = QLineEdit(f.get("name", ""))
        self.name.setFont(theme.font(13, 600))
        top.addWidget(self.name, 1)
        self.role = QComboBox()
        for key, text in ROLES:
            self.role.addItem(tr(text), key)
        self.role.setCurrentIndex(max(0, [k for k, _ in ROLES].index(f.get("role", "manufacturing"))
                                      if f.get("role") in dict(ROLES) else 4))
        top.addWidget(self.role)
        rm = button("", "trash", "ghost", tip=tr("Удалить станцию"), icon_color=theme.tone_text("red"))
        rm.clicked.connect(on_remove)
        top.addWidget(rm)
        v.addLayout(top)

        loc = QHBoxLayout()
        loc.addWidget(label(tr("Где стоит:"), "label"))
        self.loc = QComboBox()
        self.loc.setEditable(True)
        self.loc.addItem(tr("— не указано —"), 0)
        for lid, text in loc_options:
            self.loc.addItem(f"{text} · {lid}", lid)
        cur = int(f.get("location_id") or 0)
        idx = self.loc.findData(cur)
        if idx < 0 and cur:
            self.loc.addItem(f"id {cur}", cur)
            idx = self.loc.count() - 1
        self.loc.setCurrentIndex(max(idx, 0))
        self.loc.setToolTip(tr("Структура в EVE (для подписей склада/чертежей). Можно вписать id вручную."))
        loc.addWidget(self.loc, 1)
        v.addLayout(loc)

        sy = QHBoxLayout()
        sy.addWidget(label(tr("Система:"), "label"))
        self.system_id = int(f.get("system_id") or 0)
        self.system_label = label("", "muted", wrap=True)
        self.system_label.setToolTip(tr(
            "По этой системе — индекс стоимости джобов станции и security-модификатор бонусов ригов "
            "(в low/null-sec риги дают больше). Логистика между системами НЕ считается: материалы — "
            "доставленными в систему стройки."))
        sy.addWidget(self.system_label, 1)
        sy_auto = button("", "x", "ghost", tip=tr("Авто — система из структуры «Где стоит»"), icon_size=12)
        sy_auto.clicked.connect(lambda: self._set_system(0, ""))
        sy.addWidget(sy_auto)
        v.addLayout(sy)
        spk = system_picker(svc, tr("Выбрать систему станции вручную (если не в системе стройки)…"))
        spk.picked.connect(lambda r: self._set_system(int(r["system_id"]), r["name"], r.get("security")))
        v.addWidget(spk)
        self._set_system(self.system_id, f.get("resolved_system_name") or "", f.get("resolved_security"),
                         initial=True)

        st = QHBoxLayout()
        st.addWidget(label(tr("Тип структуры:"), "label"))
        self.struct_id = int(f.get("structure_type_id") or 0)
        self.struct_label = label(names.get(str(self.struct_id), tr("не выбран — встроенный бонус (напр. "
                                                                    "Tatara −25% времени реакций) не учитывается"))
                                  if self.struct_id else tr("не выбран — встроенный бонус структуры не учитывается"),
                                  "muted" if self.struct_id else "faint", wrap=True)
        st.addWidget(self.struct_label, 1)
        clr = button("", "x", "ghost", tip=tr("Сбросить тип структуры"), icon_size=12)
        clr.clicked.connect(lambda: self._set_struct(0, ""))
        st.addWidget(clr)
        v.addLayout(st)
        sp = item_picker(svc, tr("Найти тип структуры (Raitaru, Azbel, Sotiyo, Athanor, Tatara)…"))
        sp.picked.connect(lambda r: self._set_struct(int(r["type_id"]), r["name"]))
        v.addWidget(sp)

        v.addWidget(section(tr("Риги и сервис-модули (бонусы считаются из SDE)")))
        self.rigs = IdChips(svc, "rig", tr("Найти риг (напр. Standup XL-Set Ship Manufacturing Efficiency)…"),
                            tr("риги не выбраны — используются ручные % ниже"))
        self.rigs.set(f.get("fitted_type_ids") or [], names)
        v.addWidget(self.rigs)
        comp = []
        for k, t in (("material", tr("материалы")), ("time", tr("время")), ("cost", tr("стоим. джоба"))):
            val = f.get(f"computed_{k}_bonus_pct")
            if val is not None:
                comp.append(f"{t} {val:.2f}%")
        if comp:
            v.addWidget(label(tr("Расчётный бонус (по сохранённому фиту): {bonuses}", bonuses=", ".join(comp)),
                              "good"))

        g = QGridLayout()
        g.setHorizontalSpacing(10)
        self.mat = NumberEdit(f.get("material_bonus_pct"), width=80, compact=True)
        self.time = NumberEdit(f.get("time_bonus_pct"), width=80, compact=True)
        self.cost = NumberEdit(f.get("cost_bonus_pct"), width=80, compact=True)
        self.tax = NumberEdit(f.get("tax_pct"), width=80, compact=True)
        manual = N_("Только без ригов и типа структуры")
        for i, (t, e, tip) in enumerate(((N_("ME эконом., %"), self.mat, manual),
                                         (N_("Время эконом., %"), self.time, manual),
                                         (N_("Стоим. джоба эконом., %"), self.cost, manual),
                                         (N_("Налог станции, %"), self.tax, N_("Налог владельца структуры")))):
            lb = label(tr(t), "faint")
            lb.setToolTip(tr(tip))
            g.addWidget(lb, 0, i)
            g.addWidget(e, 1, i)
        v.addLayout(g)

        v.addWidget(section(tr("Какие продукты сюда (пусто — все в этой роли)")))
        self.groups = IdChips(svc, "group", tr("EVE-группа продукта…"), tr("группы не выбраны"))
        self.groups.set(f.get("group_ids") or [], names)
        v.addWidget(self.groups)
        self.cats = IdChips(svc, "category", tr("EVE-категория продукта (для XL/L ригов EC)…"),
                            tr("категории не выбраны"), tone="violet")
        self.cats.set(f.get("category_ids") or [], names)
        v.addWidget(self.cats)
        v.addWidget(hline())

    def _set_struct(self, tid, name):
        self.struct_id = tid
        self.struct_label.setText(name if tid else tr("не выбран — встроенный бонус структуры не учитывается"))

    def _set_system(self, system_id: int, name: str, security=None, initial: bool = False):
        """Система станции: выбрана вручную или авто. Подпись — какая система реально используется
        (для авто — как её определил сервис при загрузке настроек)."""
        self.system_id = int(system_id or 0)
        sec = f" · sec {security:.1f}" if isinstance(security, (int, float)) else ""
        if self.system_id:
            self.system_label.setText(tr("выбрана вручную: {name}{sec}", name=name or self.system_id, sec=sec))
        elif initial and not self.f.get("system_id"):
            how = SYSTEM_HOW.get(self.f.get("resolved_system_how") or "", "")
            self.system_label.setText(tr("авто: {name}{sec} ({how})", name=name or "—", sec=sec, how=tr(how))
                                      if how else tr("авто: {name}{sec}", name=name or "—", sec=sec))
        else:
            self.system_label.setText(tr("авто — по структуре «Где стоит» (определится после сохранения)"))

    def collect(self) -> dict:
        loc = self.loc.currentData()
        if loc is None:
            txt = self.loc.currentText().strip().split("·")[-1].strip()
            loc = int(txt) if txt.isdigit() else 0
        return {
            "name": self.name.text().strip() or tr("Станция"), "role": self.role.currentData(),
            "location_id": int(loc or 0), "structure_type_id": self.struct_id,
            "fitted_type_ids": self.rigs.ids(), "group_ids": self.groups.ids(),
            "category_ids": self.cats.ids(),
            "material_bonus_pct": self.mat.value() or 0.0, "time_bonus_pct": self.time.value() or 0.0,
            "cost_bonus_pct": self.cost.value() or 0.0, "tax_pct": self.tax.value() or 0.0,
            "system_id": self.system_id,
        }


class SettingsPage(Page):
    SECTIONS = (("prod", N_("Производство"), "hammer"), ("fac", N_("Станции"), "anvil"),
                ("log", N_("Логистика и рынок"), "route"), ("rec", N_("Рекомендации"), "star"),
                ("bp", N_("Чертежи"), "clipboard"), ("plan", N_("Планировщик"), "gantt"),
                ("sys", N_("Система"), "server"))

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        bar = QHBoxLayout()
        self.seg = Segmented([(k, tr(t), i) for k, t, i in self.SECTIONS], small=True)
        self.seg.changed.connect(self._switch)
        bar.addWidget(self.seg)
        bar.addStretch(1)
        self.btn_save = button(tr("Сохранить"), "check", "hero", icon_size=14)
        self.btn_save.clicked.connect(self.save)
        reset = button(tr("Сбросить изменения"), "repeat")
        reset.clicked.connect(self.load)
        bar.addWidget(reset)
        bar.addWidget(self.btn_save)
        v.addLayout(bar)
        self.status = StatusLine()
        v.addWidget(self.status)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self._pages: dict[str, QVBoxLayout] = {}
        for key, _t, _i in self.SECTIONS:
            scroll, body = scroll_page()
            self._pages[key] = body
            self.stack.addWidget(scroll)
        self.cfg: dict = {}
        self._loaded = False
        ctx.hub.config_changed.connect(self._external_change)

    def _switch(self, key):
        self.stack.setCurrentIndex([k for k, _t, _i in self.SECTIONS].index(key))

    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self.load()

    def _external_change(self):
        # вкладки «Склад»/«Персонажи»/«Обзор» сохраняют свои части — перечитать, чтобы
        # «Сохранить» здесь не затёр их устаревшей копией
        if self._loaded and not getattr(self, "_saving", False):
            self.load()

    # ------------------------------------------------------------------ загрузка
    def load(self):
        svc = self.svc

        def work():
            cfg = svc.get_config()
            tids = {o["type_id"] for o in cfg["blueprint_overrides"]} | set(cfg["always_buy_types"]) \
                | set(cfg["always_build_types"]) | set(cfg["recommend"]["exclude_type_ids"]) \
                | {o["type_id"] for o in cfg["invention"]["per_product"]}
            gids = set(cfg["always_buy_groups"]) | set(cfg["always_build_groups"]) | set(cfg["jump_capable_groups"]) \
                | set(cfg["recommend"]["exclude_group_ids"])
            cids = set(cfg["recommend"]["exclude_category_ids"]) | set(cfg["recommend"]["buy_cheaper_exclude_categories"])
            for g in cfg["recommend"]["groups"]:
                gids |= set(g["group_ids"])
            for f in cfg["facilities"]:
                tids |= set(f.get("fitted_type_ids") or [])
                if f.get("structure_type_id"):
                    tids.add(f["structure_type_id"])
                gids |= set(f.get("group_ids") or [])
                cids |= set(f.get("category_ids") or [])
            names = {**svc.type_names(tids), **svc.group_names(gids), **svc.category_names(cids)}
            sysnames = svc.system_names([loc.get("system_id") for loc in cfg["locations"].values()
                                         if loc.get("system_id")])
            bp_locs = svc.blueprint_locations()
            stock = svc.stock_locations()
            loc_opts = []
            for s in stock["systems"]:
                for loc in s["locations"]:
                    loc_opts.append((loc["location_id"], loc["name"]))
            return cfg, names, sysnames, bp_locs, loc_opts, svc.decryptors(), svc.market_structures()

        def done(res, err):
            if err:
                self.status.show_msg("err", err_text(err))
                return
            self.cfg, self.names, self.sysnames, self.bp_locs, self.loc_opts, self.decs, self.mkt = res
            for body in self._pages.values():
                clear_layout(body)
            self._build_prod()
            self._build_fac()
            self._build_log()
            self._build_rec()
            self._build_bp()
            self._build_plan()
            self._build_sys()
            for body in self._pages.values():
                body.addStretch(1)
        run_bg(work, done)

    # ------------------------------------------------------------ Производство
    def _build_prod(self):
        body = self._pages["prod"]
        c = Card(tr("Производство и налоги"), "hammer", "cyan")
        g, self.ind = _num_grid(IND_FIELDS, self.cfg["industry"])
        c.body.addLayout(g)
        c.body.addWidget(label(tr("Станции с ригами (вкладка «Станции») важнее этих фолбэков: множители выше "
                                  "действуют, только если под роль джоба нет станции."), "faint", wrap=True))
        body.addWidget(c)
        inv = Card(tr("Инвента"), "flask", "violet")
        self.inv_skills = check(
            tr("Шанс инвенты — со скиллами инвентора"),
            bool(self.cfg["industry"].get("invention_use_skills", True)),
            tr("EVE: шанс = база × (1 + Encryption/40 + (наука1 + наука2)/30) × декриптор, не больше 100%. "
               "Выкл — «голый» шанс из SDE, без скиллов (попыток и себестоимости T2 больше)."))
        inv.body.addWidget(self.inv_skills)
        inv.body.addWidget(label(tr("Скиллы — те, что требует инвента конкретного T1-чертежа (Encryption "
                                    "Methods расы + две науки). Инвентор — лучший по этим скиллам среди "
                                    "назначенных на «Науку» (вкладка «Персонажи»; пусто — те, кто на "
                                    "производстве). Разбивка шанса видна в Калькуляторе, блок «Чертёж — "
                                    "инвента»."), "faint", wrap=True))
        self._build_decryptors(inv)
        body.addWidget(inv)
        row = QHBoxLayout()
        row.setSpacing(14)
        ab = Card(tr("Всегда покупать (не строить)"), "cart", "gold")
        ab.body.addWidget(label(tr("Эти предметы/группы Forge никогда не строит как компонент — берёт с рынка "
                                   "(напр. все Fuel Block)."), "muted", wrap=True))
        self.ab_groups = IdChips(self.svc, "group", tr("Группа (напр. Fuel Block)…"), tr("группы не выбраны"),
                                 "gold")
        self.ab_groups.set(self.cfg["always_buy_groups"], self.names)
        self.ab_types = IdChips(self.svc, "type", tr("Предмет…"), tr("предметы не выбраны"), "gold")
        self.ab_types.set(self.cfg["always_buy_types"], self.names)
        ab.body.addWidget(section(tr("По группам")))
        ab.body.addWidget(self.ab_groups)
        ab.body.addWidget(section(tr("По предметам")))
        ab.body.addWidget(self.ab_types)
        row.addWidget(ab, 1)
        bl = Card(tr("Всегда строить (не покупать)"), "hammer", "green")
        bl.body.addWidget(label(tr("Строить, даже если рынок дешевле (если чертёж есть). «Всегда покупать» "
                                   "сильнее, если предмет в обоих списках."), "muted", wrap=True))
        self.bl_groups = IdChips(self.svc, "group", tr("Группа…"), tr("группы не выбраны"), "green")
        self.bl_groups.set(self.cfg["always_build_groups"], self.names)
        self.bl_types = IdChips(self.svc, "type", tr("Предмет…"), tr("предметы не выбраны"), "green")
        self.bl_types.set(self.cfg["always_build_types"], self.names)
        bl.body.addWidget(section(tr("По группам")))
        bl.body.addWidget(self.bl_groups)
        bl.body.addWidget(section(tr("По предметам")))
        bl.body.addWidget(self.bl_types)
        row.addWidget(bl, 1)
        body.addLayout(row)

    def _dec_label(self, d: dict, short: bool = False) -> str:
        price = d.get("price")
        tail = f" · {fmt_isk_short(price)} ISK" if price is not None else tr(" · нет цены")
        if short:
            return d["name"] + tail
        return tr("{name} (шанс ×{prob}, ME {me}, TE {te}, прогонов {runs}){tail}", name=d["name"],
                  prob=f"{d['prob_mult']:g}", me=f"{d['me_mod']:+d}", te=f"{d['te_mod']:+d}",
                  runs=f"{d['run_mod']:+d}", tail=tail)

    def _build_decryptors(self, card: Card):
        """[invention]: режим выбора декриптора, разрешённые для авто-выбора, оверрайды по товару."""
        inv = self.cfg.get("invention") or {}
        card.body.addWidget(section(tr("Декриптор")))
        self.dec_mode = Segmented([("auto_cost", tr("Авто — по стоимости"), None),
                                   ("none", tr("Без декриптора"), None),
                                   ("fixed", tr("Один для всех"), None)], small=True)
        self.dec_mode.set_value(inv.get("decryptor_mode") or "auto_cost")
        card.body.addWidget(self.dec_mode, 0, Qt.AlignmentFlag.AlignLeft)
        self.dec_fixed = QComboBox()
        self.dec_fixed.addItem(tr("без декриптора"), 0)
        for d in self.decs:
            self.dec_fixed.addItem(self._dec_label(d), d["type_id"])
        idx = self.dec_fixed.findData(int(inv.get("decryptor_type_id") or 0))
        self.dec_fixed.setCurrentIndex(max(idx, 0))
        card.body.addLayout(form_row(tr("Декриптор режима «Один для всех»"), self.dec_fixed))
        self.dec_mode.changed.connect(lambda m: self.dec_fixed.setEnabled(m == "fixed"))
        self.dec_fixed.setEnabled(self.dec_mode.value() == "fixed")
        card.body.addWidget(section(tr("Разрешены для авто-выбора")))
        allowed = set(inv.get("allowed_decryptors") or [])
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        self.dec_allowed: dict[int, QCheckBox] = {}
        for i, d in enumerate(self.decs):
            cb = check(self._dec_label(d), not allowed or d["type_id"] in allowed)
            grid.addWidget(cb, i // 2, i % 2)
            self.dec_allowed[d["type_id"]] = cb
        card.body.addLayout(grid)
        card.body.addWidget(label(tr("Авто перебирает «без декриптора» и отмеченные (все отмечены — все 8). "
                                     "Декриптор без цены на рынке недоступен: заданный вручную — тогда берётся "
                                     "авто-выбор с пометкой в «Подготовка / проблемы»."), "faint", wrap=True))
        card.body.addWidget(section(tr("Для конкретных T2-товаров (сильнее режима)")))
        self.dec_host = QVBoxLayout()
        self.dec_host.setSpacing(4)
        card.body.addLayout(self.dec_host)
        self.dec_rows: dict[int, QComboBox] = {}
        for o in inv.get("per_product") or []:
            tid = int(o["type_id"])
            self._add_dec_override(tid, self.names.get(str(tid), f"#{tid}"), int(o.get("decryptor_type_id") or 0))
        p = item_picker(self.svc, tr("Добавить T2-товар (напр. Nomad)…"))
        p.picked.connect(lambda r: self._add_dec_override(int(r["type_id"]), r["name"], 0))
        card.body.addWidget(p)

    def _add_dec_override(self, tid: int, name: str, dec_id: int):
        if tid in self.dec_rows:
            return
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(TypeIcon(tid, 22))
        h.addWidget(ElidedLabel(name), 1)
        combo = QComboBox()
        combo.addItem(tr("без декриптора"), 0)
        for d in self.decs:
            combo.addItem(self._dec_label(d, short=True), d["type_id"])
        idx = combo.findData(dec_id)
        if idx < 0 and dec_id:
            combo.addItem(tr("тип {id} (не декриптор)", id=dec_id), dec_id)
            idx = combo.count() - 1
        combo.setCurrentIndex(max(idx, 0))
        h.addWidget(combo)
        rm = button("", "x", "ghost", tip=tr("Убрать"), icon_size=12)
        rm.setFixedSize(24, 24)

        def remove() -> None:
            self.dec_rows.pop(tid, None)
            w.setParent(None)
            w.deleteLater()

        rm.clicked.connect(remove)
        h.addWidget(rm)
        self.dec_rows[tid] = combo
        self.dec_host.addWidget(w)

    # ---------------------------------------------------------------- Станции
    def _build_fac(self):
        body = self._pages["fac"]
        c = Card(tr("Станции (структуры) — параметры джобов"), "anvil", "cyan")
        c.body.addWidget(label(tr("ME/время — из реально фитованных ригов и типа структуры (считаем сами из "
                                  "SDE, со стэкинг-пенальти и бонусом low/null-sec по системе станции). "
                                  "Стоимость джоба и налог — вручную. Группы/категории ограничивают, к каким "
                                  "продуктам применяется станция — можно завести несколько станций одной роли "
                                  "под разные группы. Система станции (авто — из структуры) задаёт индекс "
                                  "стоимости её джобов; перевозка материалов между системами не считается — "
                                  "они считаются доставленными в систему стройки."), "muted", wrap=True))
        self.fac_host = QVBoxLayout()
        self.fac_host.setSpacing(4)
        c.body.addLayout(self.fac_host)
        self.fac_editors: list[FacilityEditor] = []
        for f in self.cfg["facilities"]:
            self._add_fac(f)
        add = button(tr("Добавить станцию"), "plus", "primary")
        add.clicked.connect(lambda: self._add_fac({"name": tr("Новая станция"), "role": "manufacturing"}))
        h = QHBoxLayout()
        h.addWidget(add)
        h.addStretch(1)
        c.body.addLayout(h)
        body.addWidget(c)

    def _add_fac(self, f):
        holder: list = []

        def remove():
            ed = holder[0]
            self.fac_editors.remove(ed)
            ed.setParent(None)
            ed.deleteLater()

        ed = FacilityEditor(self.svc, f, self.names, self.loc_opts, remove)
        holder.append(ed)
        self.fac_editors.append(ed)
        self.fac_host.addWidget(ed)

    # --------------------------------------------------------- Логистика и рынок
    def _build_log(self):
        body = self._pages["log"]
        c = Card(tr("Места"), "pin", "cyan")
        c.body.addWidget(label(tr("Три роли логистики Forge: хаб закупки (Jita), рынок сбыта и второй хаб "
                                  "(C-J6MT), место стройки (GPLB-C). Имя системы резолвится из SDE; регион "
                                  "рынка — для цен. Названия идут в отчёты и подписи."), "muted", wrap=True))
        self.loc_edits = {}
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        for r, (key, role) in enumerate((("jita", tr("Хаб закупки")), ("c_j6mt", tr("Рынок сбыта / 2-й хаб")),
                                         ("gplb_c", tr("Место стройки")))):
            loc = self.cfg["locations"].get(key) or {"name": "", "system_id": 0, "region_id": 0}
            g.addWidget(label(role, "label"), r, 0)
            name = QLineEdit(loc.get("name", ""))
            name.setFixedWidth(150)
            g.addWidget(name, r, 1)
            sysl = label(tr("система {name} · id {id} · регион {region}",
                            name=self.sysnames.get(str(loc.get("system_id")), "—"),
                            id=loc.get("system_id") or "—", region=loc.get("region_id") or "—"), "faint")
            g.addWidget(sysl, r, 2)
            pick = system_picker(self.svc, tr("сменить систему…"))
            pick.setFixedWidth(200)
            state = {"system_id": loc.get("system_id") or 0, "region_id": loc.get("region_id") or 0}

            def on_pick(row, st=state, lbl=sysl, nm=name):
                st["system_id"], st["region_id"] = row["system_id"], row["region_id"]
                lbl.setText(tr("система {name} · id {id} · регион {region}", name=row["name"],
                               id=row["system_id"], region=row["region_id"]))
                if not nm.text().strip():
                    nm.setText(row["name"])
            pick.picked.connect(on_pick)
            g.addWidget(pick, r, 3)
            self.loc_edits[key] = (name, state)
        g.setColumnStretch(2, 1)
        c.body.addLayout(g)
        body.addWidget(c)

        s = Card(tr("Структуры"), "anvil", "steel")
        st = self.cfg["structures"]
        self.st_market = NumberEdit(st.get("taj_mahgoon_market_id"), integer=True, width=170)
        self.st_ec = NumberEdit(st.get("gplb_engineering_complex_id"), integer=True, width=170)
        self.st_ref = NumberEdit(st.get("gplb_refinery_id"), integer=True, width=170)
        s.body.addLayout(form_row(tr("Рынок-структура по умолчанию (если список рынков ниже пуст)"),
                                  self.st_market))
        s.body.addLayout(form_row(tr("Engineering Complex места стройки (склад «Авто»)"), self.st_ec))
        s.body.addLayout(form_row(tr("Refinery места стройки (склад «Авто»)"), self.st_ref))
        body.addWidget(s)
        body.addWidget(self._build_markets())

        m = Card(tr("Рынок: где покупать и как считать продажу"), "coins", "gold")
        mk = self.cfg["market"]
        jn = self.cfg["locations"].get("jita", {}).get("name") or "Jita"
        cn = self.cfg["locations"].get("c_j6mt", {}).get("name") or "C-J6MT"
        self.hub_jita = check(tr("Покупать в {hub} (+ доставка)", hub=jn),
                              "jita" in (mk.get("buy_hubs") or ["jita", "cj"]))
        self.hub_cj = check(tr("Покупать в {hub}", hub=cn), "cj" in (mk.get("buy_hubs") or ["jita", "cj"]))
        m.body.addWidget(self.hub_jita)
        m.body.addWidget(self.hub_cj)
        m.body.addWidget(label(tr("Оба выключены = оба разрешены. Выбор хаба — самый дешёвый landed с учётом "
                                  "наличия объёма."), "faint", wrap=True))
        self.sell_mode = Segmented([("sell_min", tr("Продажа: выставить sell-ордер"), None),
                                    ("buy_max", tr("Продажа: сразу в buy-ордер"), None)], small=True)
        self.sell_mode.set_value(mk.get("sell_price") or "sell_min")
        m.body.addWidget(self.sell_mode, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(m)

        fr = Card(tr("Фрахт (плечи логистики)"), "route", "cyan")
        fr.body.addWidget(label(tr("per_m3 — линейно ISK/м³ (+ минимум за заказ); fixed_jump — рейсами: 1-я "
                                   "партия = один прыжок, каждая следующая = ещё два (туда-обратно)."),
                                "muted", wrap=True))
        self.fr_edits = []
        names = {k: (v.get("name") or k) for k, v in self.cfg["locations"].items()}
        legs = [("jita", "c_j6mt"), ("c_j6mt", "gplb_c"), ("gplb_c", "c_j6mt")]
        routes = {(r["from"], r["to"]): r for r in self.cfg["freight_routes"]}
        for fr_to in legs:
            r = routes.get(fr_to) or {"from": fr_to[0], "to": fr_to[1], "mode": "per_m3"}
            box = QWidget()
            bv = QGridLayout(box)
            bv.setContentsMargins(0, 4, 0, 4)
            bv.setHorizontalSpacing(10)
            t = label(f"{names.get(fr_to[0], fr_to[0])} → {names.get(fr_to[1], fr_to[1])}")
            t.setFont(theme.font(13, 600))
            bv.addWidget(t, 0, 0)
            mode = Segmented([("per_m3", "per_m3", None), ("fixed_jump", tr("рейсами"), None)], small=True)
            mode.set_value(r.get("mode") or "per_m3")
            bv.addWidget(mode, 0, 1)
            e = {k: NumberEdit(r.get(k), width=110, compact=True) for k in
                 ("isk_per_m3", "min_cost", "fixed_cost", "vessel_capacity_m3", "load_factor")}
            for col, (k, title) in enumerate((("isk_per_m3", tr("ISK/м³")), ("min_cost", tr("мин. за заказ")),
                                              ("fixed_cost", tr("ISK за прыжок")),
                                              ("vessel_capacity_m3", tr("вместимость, м³")),
                                              ("load_factor", tr("загрузка 0–1")))):
                bv.addWidget(label(title, "faint"), 1, col)
                bv.addWidget(e[k], 2, col)
            fr.body.addWidget(box)
            self.fr_edits.append((fr_to, mode, e))
        body.addWidget(fr)

        jg = Card(tr("Самоходные при вывозе"), "ship", "violet")
        jg.body.addWidget(label(tr("Корабли этих групп летят на рынок сами — вывоз считается как топливо за "
                                   "прыжок (ISK за прыжок плеча «стройка → рынок») за штуку, а не объём × "
                                   "ставка."), "muted", wrap=True))
        self.jump_groups = IdChips(self.svc, "group", tr("Группа (напр. Dreadnought, Carrier)…"),
                                   tr("не выбрано — вывоз всех кораблей по объёму"), "violet")
        self.jump_groups.set(self.cfg["jump_capable_groups"], self.names)
        jg.body.addWidget(self.jump_groups)
        body.addWidget(jg)

    # --------------------------------------------------------- рынки-структуры
    def _build_markets(self) -> Card:
        """[structures] market_structures: чьи ордера сводятся в срез рынка сбыта."""
        cn = self.cfg["locations"].get("c_j6mt", {}).get("name") or "C-J6MT"
        c = Card(tr("Рынки-структуры (ордера — при синке персонажей)"), "coins", "steel")
        c.body.addWidget(label(tr(
            "Ордера всех перечисленных структур сводятся в ОДИН срез рынка {market}: минимальный sell, "
            "максимальный buy, суммарные объёмы. Каждую тянет персонаж со скоупом рынков структур — "
            "сначала те, у кого там лежат ассеты; нет доступа — пробуется следующий, ошибка одной "
            "структуры не мешает остальным (итог по каждой — «Обзор → Источники данных»).", market=cn),
            "muted", wrap=True))
        self.mkt_host = QVBoxLayout()
        self.mkt_host.setSpacing(4)
        c.body.addLayout(self.mkt_host)
        self.mkt_rows: dict[int, QWidget] = {}
        self._known_mkt = {k["structure_id"]: k for k in (self.mkt or {}).get("known", [])}
        self.mkt_empty = label("", "faint", wrap=True)
        c.body.addWidget(self.mkt_empty)
        for sid in self.cfg["structures"].get("market_structures") or []:
            self._add_market(int(sid))
        self._mkt_empty_note()
        add = QHBoxLayout()
        self.mkt_pick = QComboBox()
        self.mkt_pick.addItem(tr("— известная структура —"), 0)
        for k in self._known_mkt.values():
            if k["status"] != "ok":
                continue
            where = k.get("system_name") or tr("система ?")
            self.mkt_pick.addItem(f"{k['name']} · {where}" if k["in_hub"]
                                  else tr("{name} · {where} (не в системе хаба)", name=k["name"], where=where),
                                  k["structure_id"])
        add.addWidget(self.mkt_pick, 1)
        b1 = button(tr("Добавить"), "plus", "primary")
        b1.clicked.connect(lambda: self._add_market(int(self.mkt_pick.currentData() or 0)))
        add.addWidget(b1)
        self.mkt_id = NumberEdit(None, tr("id структуры"), integer=True, width=170)
        add.addWidget(self.mkt_id)
        b2 = button(tr("По id"), "plus")
        b2.clicked.connect(lambda: self._add_market(self.mkt_id.int_value(0)))
        add.addWidget(b2)
        c.body.addLayout(add)
        return c

    def _mkt_empty_note(self):
        default = int(self.cfg["structures"].get("taj_mahgoon_market_id") or 0)
        self.mkt_empty.setText("" if self.mkt_rows else (
            tr("Список пуст — синкается «Рынок-структура по умолчанию» (id {id}).", id=default) if default
            else tr("Список пуст и рынок-структура по умолчанию не задана — ордера структур не синкаются.")))
        self.mkt_empty.setVisible(not self.mkt_rows)

    def _add_market(self, sid: int):
        if not sid or sid in self.mkt_rows:
            return
        k = self._known_mkt.get(sid)
        hub_sys = (self.mkt or {}).get("hub_system_id")
        hub_name = (self.mkt or {}).get("hub_system_name") or tr("хаба")
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        name = (k or {}).get("name") or tr("Структура {id}", id=sid)
        h.addWidget(ElidedLabel(f"{name} · {sid}"), 1)
        if k and k.get("status") == "ok" and k.get("system_id"):
            if hub_sys and k["system_id"] != hub_sys:
                note = label(tr("⚠ {system}: не в системе хаба {hub} — ордера всё равно попадут в срез рынка "
                                "сбыта", system=k.get("system_name"), hub=hub_name), "faint", wrap=True)
                note.setStyleSheet(f"color: {theme.tone_text('gold')};")
            else:
                note = label(k.get("system_name") or "", "faint")
        else:
            note = label(tr("система не известна — структура ещё не резолвлена (синк персонажей)"), "faint")
        h.addWidget(note)
        rm = button("", "x", "ghost", tip=tr("Убрать"), icon_size=12)
        rm.setFixedSize(24, 24)

        def remove() -> None:
            self.mkt_rows.pop(sid, None)
            w.setParent(None)
            w.deleteLater()
            self._mkt_empty_note()

        rm.clicked.connect(remove)
        h.addWidget(rm)
        self.mkt_rows[sid] = w
        self.mkt_host.addWidget(w)
        self._mkt_empty_note()

    # ------------------------------------------------------------ Рекомендации
    def _build_rec(self):
        body = self._pages["rec"]
        rec = self.cfg["recommend"]
        c = Card(tr("Веса и фильтры «Что строить»"), "star", "gold")
        g, self.rec = _num_grid(REC_FIELDS, rec)
        c.body.addLayout(g)
        body.addWidget(c)
        lq = Card(tr("Ликвидность — откуда «Объём/сут»"), "activity", "cyan")
        cn = self.cfg["locations"].get("c_j6mt", {}).get("name") or "C-J6MT"
        jn = self.cfg["locations"].get("jita", {}).get("name") or "Jita"
        self.liq_src = QComboBox()
        for key, text in (("sell_region", tr("По записям: история {market}, иначе выставленное на "
                                             "продажу", market=cn)),
                          ("sell_region_history", tr("Реальный оборот {market} (история ESI региона, со "
                                                     "структурами)", market=cn)),
                          ("jita", tr("Оборот {hub} как прокси", hub=jn))):
            self.liq_src.addItem(text, key)
        self.liq_src.setCurrentIndex(max(0, self.liq_src.findData(rec.get("liquidity_source") or "sell_region")))
        lq.body.addLayout(form_row(tr("Источник"), self.liq_src))
        self.liq_days = NumberEdit(rec.get("liquidity_days") or 30, integer=True, width=90)
        lq.body.addLayout(form_row(tr("Окно, календарных дней (дни без сделок = 0)"), self.liq_days,
                                   tr("Для «реального оборота» и «Jita»; «по записям» окно не использует")))
        lq.body.addWidget(label(tr(
            "Влияет на колонку «Объём/сут», фильтр «Мин. объём/сут» и вес ликвидности в ТОП, «Из остатков» и "
            "«Дешевле купить» (там рынок — хаб покупки). История {market} качается с «Рынком Jita и индексами» "
            "раз в сутки и только при источнике «Реальный оборот»: в неё попадают и сделки в структурах игроков "
            "(в Insmother нет NPC-станций, а история ESI есть).", market=cn), "faint", wrap=True))
        body.addWidget(lq)
        z = Card(tr("Запреты — никогда не рекомендовать"), "ban", "red")
        z.body.addWidget(label(tr("Не попадут ни в ТОП, ни в «Из остатков», ни в «Дешевле купить» — напр. то, "
                                  "что не хочешь или не можешь продавать."), "muted", wrap=True))
        self.rx_types = IdChips(self.svc, "type", tr("Предмет…"), tr("предметы не выбраны"), "red")
        self.rx_types.set(rec["exclude_type_ids"], self.names)
        self.rx_groups = IdChips(self.svc, "group", tr("Группа…"), tr("группы не выбраны"), "red")
        self.rx_groups.set(rec["exclude_group_ids"], self.names)
        self.rx_cats = IdChips(self.svc, "category", tr("Категория…"), tr("категории не выбраны"), "red")
        self.rx_cats.set(rec["exclude_category_ids"], self.names)
        for t, w in ((tr("Предметы"), self.rx_types), (tr("Группы"), self.rx_groups),
                     (tr("Категории"), self.rx_cats)):
            z.body.addWidget(section(t))
            z.body.addWidget(w)
        body.addWidget(z)
        bc = Card(tr("«Дешевле купить»: исключить категории"), "cart", "gold")
        self.bc_cats = IdChips(self.svc, "category", tr("Категория (25 = Asteroid — руда/лёд)…"),
                               tr("не исключено ничего"), "gold")
        self.bc_cats.set(rec["buy_cheaper_exclude_categories"], self.names)
        bc.body.addWidget(self.bc_cats)
        body.addWidget(bc)
        gr = Card(tr("Группы ТОП"), "list", "cyan")
        gr.body.addWidget(label(tr("Для каждой группы — свой ТОП на вкладке «Что строить». Ограничения по числу "
                                   "групп нет."), "muted", wrap=True))
        self.grp_host = QVBoxLayout()
        gr.body.addLayout(self.grp_host)
        self.grp_editors = []
        for gdef in rec["groups"]:
            self._add_group(gdef)
        add = button(tr("Добавить группу"), "plus", "primary")
        add.clicked.connect(lambda: self._add_group({"name": tr("Новая группа"), "group_ids": []}))
        h = QHBoxLayout()
        h.addWidget(add)
        h.addStretch(1)
        gr.body.addLayout(h)
        body.addWidget(gr)
        ui = self.cfg["ui"]
        d = Card(tr("Значения по умолчанию на вкладке «Что строить»"), "sliders", "steel")
        self.ui_rec_top = NumberEdit(ui["recommend_top"], integer=True, width=90)
        self.ui_bc_top = NumberEdit(ui["buy_cheaper_top"], integer=True, width=90)
        self.ui_bc_vol = NumberEdit(ui["buy_cheaper_min_volume"], width=90)
        self.ui_st_top = NumberEdit(ui["stock_top"], integer=True, width=90)
        for t, w in ((tr("ТОП: сколько позиций"), self.ui_rec_top), (tr("«Дешевле купить»: топ"), self.ui_bc_top),
                     (tr("«Дешевле купить»: мин. объём/сут"), self.ui_bc_vol),
                     (tr("«Из остатков»: топ"), self.ui_st_top)):
            d.body.addLayout(form_row(t, w))
        body.addWidget(d)

    def _add_group(self, gdef):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 0, 8)
        top = QHBoxLayout()
        name = QLineEdit(gdef.get("name", ""))
        name.setFont(theme.font(13, 600))
        top.addWidget(name, 1)
        rm = button("", "trash", "ghost", tip=tr("Удалить группу"), icon_color=theme.tone_text("red"))
        top.addWidget(rm)
        v.addLayout(top)
        ids = IdChips(self.svc, "group", tr("EVE-группа (напр. Dreadnought)…"), tr("EVE-группы не выбраны"))
        ids.set(gdef.get("group_ids") or [], self.names)
        v.addWidget(ids)
        entry = (w, name, ids)
        self.grp_editors.append(entry)
        rm.clicked.connect(lambda: (self.grp_editors.remove(entry), w.setParent(None), w.deleteLater()))
        self.grp_host.addWidget(w)

    # ---------------------------------------------------------------- Чертежи
    def _build_bp(self):
        body = self._pages["bp"]
        o = Card(tr("Стоимость чертежей вручную (ISK на 1 прогон)"), "coins", "gold")
        o.body.addWidget(label(tr("Для чертежей, которыми не владеешь и где недоступна инвента. Для T1-чертежа "
                                  "инвенты (напр. Fenrir Blueprint) — цена одной T1-копии на попытку."),
                               "muted", wrap=True))
        self.ov_host = QVBoxLayout()
        self.ov_host.setSpacing(4)
        o.body.addLayout(self.ov_host)
        self.ov_rows: dict[int, NumberEdit] = {}
        for ov in self.cfg["blueprint_overrides"]:
            self._add_override(ov["type_id"], self.names.get(str(ov["type_id"]), f"#{ov['type_id']}"), ov["per_run"])
        p = item_picker(self.svc, tr("Добавить предмет…"))
        p.picked.connect(lambda r: self._add_override(int(r["type_id"]), r["name"], 0))
        o.body.addWidget(p)
        body.addWidget(o)

        loc = Card(tr("Где искать чертежи"), "pin", "cyan")
        loc.body.addWidget(label(tr("Ничего не отмечено — «свои» все чертежи во всех локациях. Отмечено — только "
                                    "лежащие там (включая контейнеры внутри): влияет на стоимость чертежа, "
                                    "ME/TE, ТОП «свои чертежи» и назначение в расписании. Склад "
                                    "настраивается отдельно — вкладка «Склад»."), "muted", wrap=True))
        sel = set(self.cfg["blueprint_location_ids"])
        self.bp_checks = {}
        if not self.bp_locs:
            loc.body.addWidget(label(tr("Нет данных о чертежах — синкни персонажей («Обзор»)."), "faint"))
        for r in self.bp_locs:
            h = QHBoxLayout()
            cb = check(r.get("label") or f"id {r['location_id']}", r["location_id"] in sel)
            cb.setToolTip(tr("id {id} · система {system}", id=r["location_id"], system=r["system_name"])
                          if r.get("system_name") else f"id {r['location_id']}")
            h.addWidget(cb, 1)
            if r.get("system_name"):
                h.addWidget(label(r["system_name"], "faint"))
            h.addWidget(label(tr("{n} черт.", n=fmt_int(r["count"])), "faint"))
            loc.body.addLayout(h)
            self.bp_checks[r["location_id"]] = cb
        for lid in sel - set(self.bp_checks):
            cb = check(tr("id {id} (сейчас там чертежей нет)", id=lid), True)
            loc.body.addWidget(cb)
            self.bp_checks[lid] = cb
        body.addWidget(loc)

    def _add_override(self, tid, name, per_run):
        if tid in self.ov_rows:
            return
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(TypeIcon(tid, 22))
        h.addWidget(ElidedLabel(name), 1)
        e = NumberEdit(per_run, width=150, compact=True)
        h.addWidget(e)
        rm = button("", "x", "ghost", tip=tr("Убрать"), icon_size=12)
        rm.setFixedSize(24, 24)
        rm.clicked.connect(lambda: (self.ov_rows.pop(tid, None), w.setParent(None), w.deleteLater()))
        h.addWidget(rm)
        self.ov_rows[tid] = e
        self.ov_host.addWidget(w)

    # ------------------------------------------------------------- Планировщик
    def _build_plan(self):
        body = self._pages["plan"]
        pl = self.cfg["planner"]
        c = Card(tr("Расписание"), "gantt", "cyan")
        self.pl_running = check(tr("Учитывать уже запущенные джобы (их слоты заняты до окончания)"),
                                pl.get("account_running_jobs", True))
        c.body.addWidget(self.pl_running)
        self.pl_copy = check(
            tr("Ставить копирование T1-чертежа для инвенты в расписание (копи-джобы со своего BPO)"),
            bool(pl.get("schedule_copy_jobs", False)),
            tr("Перед инвентой — копи-джобы в пуле «наука» (по копии на каждый джоб инвенты, минимальные "
               "раны), инвента ждёт свои копии; один BPO — одновременно в одном джобе. Время — из SDE без TE, "
               "со скиллами Science (−5%/ур.) и Advanced Industry (−3%/ур.) и бонусом станции «копирование». "
               "Выкл — стоимость копий учтена, их время в срок не входит."))
        c.body.addWidget(self.pl_copy)
        self.pl_streams = NumberEdit(pl.get("max_streams_per_node"), integer=True, width=90)
        c.body.addLayout(form_row(tr("Потолок потоков одного компонента при разбивке под срок"), self.pl_streams))
        self.pl_days = QLineEdit(", ".join(f"{d:g}" for d in pl.get("compare_max_days") or []))
        self.pl_days.setFixedWidth(220)
        c.body.addLayout(form_row(tr("«Сравнить варианты»: сроки «Макс. N дней/поток» (через запятую; "
                                     "0.5 = 12 ч)"), self.pl_days))
        body.addWidget(c)
        ow = Card(tr("Кому ставить джоб — владелец чертежа"), "users", "gold")
        self.pl_owner = Segmented([("any", tr("Любому — кто раньше закончит"), None),
                                   ("prefer_owner", tr("Предпочитать владельца"), None),
                                   ("owner_only", tr("Только владельцу"), None)], small=True)
        self.pl_owner.set_value(pl.get("owner_policy") or "any")
        ow.body.addWidget(self.pl_owner, 0, Qt.AlignmentFlag.AlignLeft)
        self.pl_slack = NumberEdit(pl.get("owner_slack_hours", 24.0), width=90)
        ow.body.addLayout(form_row(tr("«Предпочитать владельца»: допуск, часов — владелец берётся, если "
                                      "закончит не позже самого раннего на столько"), self.pl_slack))
        self.pl_owner.changed.connect(lambda m: self.pl_slack.setEnabled(m == "prefer_owner"))
        self.pl_slack.setEnabled(self.pl_owner.value() == "prefer_owner")
        ow.body.addWidget(label(tr(
            "Владелец — у кого чертёж (для реакций — формула, для инвенты — T1-чертёж) лежит в «Где искать "
            "чертежи». «Любому» — самый быстрый срок, но чертежи придётся передавать между чарами (список — в "
            "предупреждениях расписания и отчёта). «Только владельцу» — меньше передач, срок обычно дольше; если "
            "владельца нет среди назначенных в роли — джоб встанет любому, с предупреждением."), "faint",
            wrap=True))
        body.addWidget(ow)
        ui = self.cfg["ui"]
        d = Card(tr("Корзина по умолчанию (новый запуск пульта)"), "cart", "steel")
        self.ui_me = NumberEdit(ui["default_me"], integer=True, width=80)
        self.ui_te = NumberEdit(ui["default_te"], integer=True, width=80)
        self.ui_cons = check(tr("Объединять общие компоненты"), ui["default_consolidate"])
        self.ui_auto = check(tr("Авто-потоки для объединённых"), ui["default_auto_streams"])
        d.body.addLayout(form_row(tr("ME, если нет своего чертежа"), self.ui_me))
        d.body.addLayout(form_row(tr("TE, если нет своего чертежа"), self.ui_te))
        d.body.addWidget(self.ui_cons)
        d.body.addWidget(self.ui_auto)
        body.addWidget(d)

    # ------------------------------------------------------------------ Система
    def _build_sys(self):
        body = self._pages["sys"]
        s = Card("EVE SSO", "lock", "cyan")
        sso = self.cfg["sso"]
        self.sso_id = QLineEdit(sso.get("client_id", ""))
        self.sso_id.setProperty("mono", True)
        self.sso_id.setFixedWidth(320)
        self.sso_port = NumberEdit(sso.get("callback_port"), integer=True, width=90)
        s.body.addLayout(form_row(tr("Client ID приложения (developers.eveonline.com)"), self.sso_id))
        s.body.addLayout(form_row(tr("Порт callback (http://localhost:PORT/callback)"), self.sso_port))
        s.body.addWidget(section(tr("Скоупы")))
        s.body.addWidget(label("\n".join(sso.get("scopes") or []), "mono", selectable=True))
        s.body.addWidget(label(tr("Refresh-токены — в Диспетчере учётных данных Windows (keyring), отдельно для "
                                  "каждого Client ID; не в БД и не в git. Токен EVE привязан к приложению: "
                                  "сменишь Client ID — персонажей нужно будет добавить заново («Обзор → Добавить "
                                  "персонажа (EVE SSO)»)."), "faint", wrap=True))
        body.addWidget(s)
        p = Card(tr("Данные"), "database", "gold")
        root = Path(self.ctx.config_path).parent
        db = Path(self.cfg["db_path"])
        db = db if db.is_absolute() else root / db
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        for r, (name, path) in enumerate(((tr("Настройки"), Path(self.ctx.config_path)), (tr("База"), db),
                                          (tr("Отчёты"), root / "reports"),
                                          (tr("Лог пульта"), root / "forge_desktop.log"),
                                          (tr("Состояние пульта"), root / ".forge"))):
            grid.addWidget(label(name, "label"), r, 0)
            grid.addWidget(label(str(path), "mono", selectable=True), r, 1)
            b = button(tr("Показать"), "folder", tip=tr("Открыть в Проводнике"))
            b.clicked.connect(lambda _=False, pth=path: self._reveal(pth))
            grid.addWidget(b, r, 2)
        grid.setColumnStretch(1, 1)
        p.body.addLayout(grid)
        p.body.addWidget(label(tr("forge.toml переписывается при сохранении (комментарии не сохраняются) — "
                                  "справочник с пояснениями: forge.example.toml."), "faint", wrap=True))
        body.addWidget(p)

    @staticmethod
    def _reveal(path: Path):
        def work():
            if path.exists() and path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603, S607
            else:
                os.startfile(str(path if path.is_dir() else path.parent))  # noqa: S606
        run_bg(work)

    # --------------------------------------------------------------- сохранение
    def _collect(self) -> dict:
        c = self.cfg
        upd: dict = {}
        upd["industry"] = {k: (e.value() if e.value() is not None else c["industry"].get(k))
                           for k, e in self.ind.items()}
        upd["industry"]["invention_use_skills"] = self.inv_skills.isChecked()
        allowed = [tid for tid, cb in self.dec_allowed.items() if cb.isChecked()]
        mode = self.dec_mode.value() or "auto_cost"
        if not allowed and self.dec_allowed and mode == "auto_cost":
            raise ValueError(tr("Инвента: для авто-выбора не отмечен ни один декриптор — отметь хотя бы один "
                                "или выбери режим «Без декриптора»."))
        upd["invention"] = {
            "decryptor_mode": mode,
            "decryptor_type_id": int(self.dec_fixed.currentData() or 0),
            # все отмечены — то же, что «все разрешены» (пустой список в конфиге)
            "allowed_decryptors": [] if len(allowed) == len(self.dec_allowed) else allowed,
            "per_product": [{"type_id": t, "decryptor_type_id": int(c.currentData() or 0)}
                            for t, c in self.dec_rows.items()],
        }
        upd["always_buy_groups"] = self.ab_groups.ids()
        upd["always_buy_types"] = self.ab_types.ids()
        upd["always_build_groups"] = self.bl_groups.ids()
        upd["always_build_types"] = self.bl_types.ids()
        upd["facilities"] = [ed.collect() for ed in self.fac_editors]
        locs = dict(c["locations"])
        for key, (name, st) in self.loc_edits.items():
            base = dict(locs.get(key) or {})
            base.update({"name": name.text().strip() or base.get("name") or key,
                         "system_id": int(st["system_id"] or 0), "region_id": int(st["region_id"] or 0)})
            locs[key] = base
        upd["locations"] = locs
        upd["structures"] = {"taj_mahgoon_market_id": self.st_market.int_value(0),
                             "gplb_engineering_complex_id": self.st_ec.int_value(0),
                             "gplb_refinery_id": self.st_ref.int_value(0),
                             "market_structures": list(self.mkt_rows)}
        hubs = [h for h, cb in (("jita", self.hub_jita), ("cj", self.hub_cj)) if cb.isChecked()]
        upd["market"] = {"buy_hubs": hubs or ["jita", "cj"], "sell_price": self.sell_mode.value() or "sell_min"}
        routes = []
        other = [r for r in c["freight_routes"]
                 if (r["from"], r["to"]) not in {fr for fr, _m, _e in self.fr_edits}]
        for (fr, to), mode, e in self.fr_edits:
            routes.append({"from": fr, "to": to, "mode": mode.value() or "per_m3",
                           **{k: float(w.value() or 0.0) for k, w in e.items()}})
        upd["freight_routes"] = routes + other
        upd["jump_capable_groups"] = self.jump_groups.ids()
        rec = {k: (e.value() if e.value() is not None else c["recommend"].get(k)) for k, e in self.rec.items()}
        for k in ("runs", "top_per_group", "min_daily_volume"):
            rec[k] = int(rec[k] or 0)
        rec["exclude_type_ids"] = self.rx_types.ids()
        rec["exclude_group_ids"] = self.rx_groups.ids()
        rec["exclude_category_ids"] = self.rx_cats.ids()
        rec["buy_cheaper_exclude_categories"] = self.bc_cats.ids()
        rec["liquidity_source"] = self.liq_src.currentData() or "sell_region"
        rec["liquidity_days"] = max(1, self.liq_days.int_value(30))
        rec["groups"] = [{"name": n.text().strip() or tr("Группа"), "group_ids": ids.ids()}
                         for _w, n, ids in self.grp_editors]
        upd["recommend"] = rec
        upd["blueprint_overrides"] = [{"type_id": t, "per_run": e.value() or 0.0} for t, e in self.ov_rows.items()]
        upd["blueprint_location_ids"] = [lid for lid, cb in self.bp_checks.items() if cb.isChecked()]
        days = []
        for part in self.pl_days.text().replace(";", ",").split(","):
            part = part.strip().replace(" ", "")
            if not part:
                continue
            try:
                days.append(float(part.lower().rstrip("дd")))  # i18n: ok — суффикс дней при вводе: «4д» или «4d»
            except ValueError:
                raise ValueError(tr("Сроки сравнения: «{value}» — не число", value=part)) from None
        upd["planner"] = {"account_running_jobs": self.pl_running.isChecked(),
                          "max_streams_per_node": max(1, self.pl_streams.int_value(64)),
                          "compare_max_days": days,
                          "owner_policy": self.pl_owner.value() or "any",
                          "owner_slack_hours": max(0.0, self.pl_slack.value() or 0.0),
                          "schedule_copy_jobs": self.pl_copy.isChecked()}
        upd["ui"] = {"default_me": self.ui_me.int_value(0), "default_te": self.ui_te.int_value(0),
                     "default_consolidate": self.ui_cons.isChecked(), "default_auto_streams": self.ui_auto.isChecked(),
                     "recommend_top": self.ui_rec_top.int_value(30), "buy_cheaper_top": self.ui_bc_top.int_value(60),
                     "buy_cheaper_min_volume": self.ui_bc_vol.value() or 0.0, "stock_top": self.ui_st_top.int_value(30)}
        upd["sso"] = {"client_id": self.sso_id.text().strip(), "callback_port": self.sso_port.int_value(8765)}
        return upd

    def save(self):
        if not self.cfg:
            return
        try:
            upd = self._collect()
        except ValueError as e:
            self.status.show_msg("err", str(e))
            return
        self.btn_save.setEnabled(False)
        self._saving = True

        def done(_r, err):
            self.btn_save.setEnabled(True)
            if err:
                self._saving = False
                self.status.show_msg("err", err_text(err))
                return
            self.status.show_msg("ok", tr("Сохранено в forge.toml — расчёты используют новые значения."), 6000)
            self.ctx.hub.config_changed.emit()
            self._saving = False
            self.load()  # перечитать: расчётные бонусы станций считаются на сервисе
        run_bg(lambda: self.svc.put_config(upd), done)
