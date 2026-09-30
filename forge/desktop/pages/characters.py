"""Вкладка «Персонажи»: роли в стройке (производство / реакции / наука), лимиты слотов Forge,
кошельки, слоты, чертежи, джобы. Роли и лимиты учитывает Расписание (и срок под «Макс.
дней/поток»)."""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..widgets import (
    N_,
    Card,
    NumberEdit,
    Page,
    StatusLine,
    button,
    err_text,
    fmt_isk,
    fmt_isk_short,
    label,
    run_bg,
    scroll_page,
    tr,
)

ROLES = (("manufacturing_character_ids", N_("Произв.")), ("reaction_character_ids", N_("Реакции")),
         ("science_character_ids", N_("Наука")))


def _age(iso):
    if not iso:
        return "—"
    try:
        ts = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    h = (datetime.now(UTC) - ts).total_seconds() / 3600
    return tr("{n} ч назад", n=int(h)) if h < 48 else tr("{n} дн назад", n=int(h // 24))


RELOGIN_TIP = N_("EVE SSO не принял сохранённый вход этого персонажа (смена пароля или отзыв доступа приложения "
                  "на сайте EVE, перенос персонажа…) — его данные не обновляются. «Обзор → Добавить персонажа "
                  "(EVE SSO)», войти им, затем «Персонажи + рынки структур».")

POOLS = ("manufacturing", "reaction", "science")
LIMIT_TIP = N_("Сколько слотов Forge может занять в расписании (пр / рк / нк). Пусто — все по скиллам; 0 — "
                "персонаж в этом пуле не участвует. Уже идущие джобы (любые) занимают слоты по скиллам до "
                "окончания: Forge получает min(лимит, свободно) — напр. свободно 3 из 11 при лимите 5 → 3 "
                "сразу и до 5 по мере окончания идущих. Остальное — под ресёрч и свои джобы.")


class CharactersPage(Page):
    HEAD = (N_("Произв."), N_("Реакции"), N_("Наука"), N_("Персонаж"), N_("Кошелёк"), N_("Слоты пр/рк/нк"),
            N_("Лимит Forge пр/рк/нк"), N_("Чертежи"), N_("Джобы (идут)"), N_("Ассеты"), N_("Обновлён"))

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, body = scroll_page()
        outer.addWidget(scroll)
        self.card = Card(tr("Персонажи"), "users", "cyan")
        body.addWidget(self.card)
        self.card.body.addWidget(label(tr(
            "Отметь роли: «Произв.» — производство, «Реакции» — реакции, «Наука» — инвента/копирование "
            "(слоты Laboratory Operation). Можно несколько ролей или ни одной — тогда персонаж в стройке не "
            "участвует. Пустой выбор у всех = участвуют все. «Наука» пустая — наукой занимаются те, кто "
            "отмечен на производство. «Лимит Forge» — сколько слотов расписание может занять "
            "(пусто — все; 0 — не участвует в пуле), чтобы оставить часть под ресёрч и свои джобы."),
            "muted", wrap=True))
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.HEAD))
        self.table.setHorizontalHeaderLabels([tr(h).upper() for h in self.HEAD])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setFont(theme.font(11, 600, display=True, spacing=106))
        for i in range(len(self.HEAD)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        lim_head = self.table.horizontalHeaderItem(6)
        if lim_head is not None:
            lim_head.setToolTip(tr(LIMIT_TIP))
        self.card.body.addWidget(self.table)
        row = QHBoxLayout()
        save = button(tr("Сохранить роли"), "check", "good")
        save.clicked.connect(self.save)
        row.addWidget(save)
        reset = button(tr("Сбросить изменения"), "repeat")
        reset.clicked.connect(self.load)
        row.addWidget(reset)
        row.addStretch(1)
        self.total = label("", "isk")
        row.addWidget(self.total)
        self.card.body.addLayout(row)
        self.status = StatusLine()
        self.card.body.addWidget(self.status)
        body.addStretch(1)
        self._checks: dict[str, dict[int, QCheckBox]] = {}
        self._limits: dict[int, dict[str, NumberEdit]] = {}
        self._cfg_limits: list[dict] = []
        self._loaded = False
        ctx.hub.data_changed.connect(self.load)

    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self.load()

    def load(self):
        svc = self.svc
        run_bg(lambda: (svc.characters(), svc.load_cfg()), self._fill)

    def _fill(self, res, err):
        if err:
            self.status.show_msg("err", err_text(err))
            return
        chars, cfg = res
        self.table.setRowCount(len(chars))
        self._checks = {k: {} for k, _ in ROLES}
        self._limits = {}
        self._cfg_limits = [sl.model_dump() for sl in cfg.planner.slot_limits]
        num = theme.font(13, 400, num=True)
        for r, c in enumerate(chars):
            cid = c["character_id"]
            for col, (key, title) in enumerate(ROLES):
                holder = QWidget()
                h = QHBoxLayout(holder)
                h.setContentsMargins(0, 0, 0, 0)
                h.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cb = QCheckBox()
                cb.setChecked(cid in getattr(cfg, key))
                cb.setToolTip(f"{tr(title)}: {c['name']}")
                h.addWidget(cb)
                self._checks[key][cid] = cb
                self.table.setCellWidget(r, col, holder)
            sl = c["slots"]
            cells = {3: c["name"], 4: fmt_isk(c.get("wallet_balance")),
                     5: f"{sl['manufacturing']} / {sl['reaction']} / {sl['science']}",
                     7: str(c.get("blueprints", 0)), 8: f"{c.get('jobs', 0)} ({c.get('active_jobs', 0)})",
                     9: str(c.get("assets", 0)),
                     10: tr("нужен вход · {age}", age=_age(c.get("updated_at"))) if c.get("relogin")
                     else _age(c.get("updated_at"))}
            for i, text in cells.items():
                it = QTableWidgetItem(text)
                if i != 3:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    it.setFont(num)
                else:
                    it.setFont(theme.font(13, 600))
                if i == 4:
                    it.setForeground(theme.qcolor("gold"))
                if i == 10 and c.get("relogin"):
                    it.setForeground(theme.qcolor("red"))
                    it.setToolTip(tr(RELOGIN_TIP))
                self.table.setItem(r, i, it)
            self.table.setCellWidget(r, 6, self._limit_cell(cid, c["name"], cfg))
        self.table.setMinimumHeight(40 + 34 * max(len(chars), 1))
        total = sum(c.get("wallet_balance") or 0 for c in chars)
        self.total.setText(tr("Суммарно: {isk} ISK ({short})", isk=fmt_isk(total), short=fmt_isk_short(total)))
        relogin = sum(1 for c in chars if c.get("relogin"))
        self.card.set_meta(tr("{n} перс.", n=len(chars))
                           + (" · " + tr("нужен вход: {n}", n=relogin) if relogin else ""))

    def _limit_cell(self, cid: int, name: str, cfg) -> QWidget:
        """Три поля лимита слотов Forge (пр / рк / нк); пусто — все по скиллам."""
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(4, 0, 4, 0)
        h.setSpacing(4)
        eds: dict[str, NumberEdit] = {}
        for pool in POOLS:
            lim = cfg.planner.slot_limit(cid, pool)
            e = NumberEdit(lim, tr("все"), integer=True, width=44, compact=True)
            e.setToolTip(f"{name}: {tr(LIMIT_TIP)}")
            h.addWidget(e)
            eds[pool] = e
        self._limits[cid] = eds
        return holder

    def _collect_limits(self) -> list[dict]:
        """[planner] slot_limits: персонажи таблицы с хоть одним лимитом (≥ 0) + записи тех, кого в
        таблице нет (не терять чужие настройки)."""
        out: list[dict] = []
        for cid, eds in self._limits.items():
            vals = {}
            for pool, e in eds.items():
                v = e.value()
                vals[pool] = max(-1, int(v)) if v is not None else -1
            if any(v >= 0 for v in vals.values()):
                out.append({"character_id": cid, **vals})
        out += [sl for sl in self._cfg_limits if sl["character_id"] not in self._limits]
        return out

    def save(self):
        upd: dict = {key: [cid for cid, cb in self._checks.get(key, {}).items() if cb.isChecked()]
                     for key, _ in ROLES}
        upd["planner"] = {"slot_limits": self._collect_limits()}

        def done(_r, err):
            if err:
                self.status.show_msg("err", err_text(err))
                return
            self.status.show_msg("ok", tr("Сохранено — роли и лимиты слотов учитываются в Расписании и отчётах."),
                                 6000)
            self.ctx.hub.config_changed.emit()
        run_bg(lambda: self.svc.put_config(upd), done)
