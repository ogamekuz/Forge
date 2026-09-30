"""Вкладка «Обзор»: ключевые показатели, синхронизация данных, автообновление, источники."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QMessageBox, QVBoxLayout

from .. import theme
from ..main_window import SOURCE_LABEL, _age, _hours
from ..widgets import (
    N_,
    Card,
    Col,
    DataTable,
    NumberEdit,
    Page,
    StatCard,
    StatusLine,
    button,
    check,
    err_text,
    fmt_int,
    fmt_isk_short,
    form_row,
    label,
    plural,
    run_bg,
    scroll_page,
    tr,
)

TABLE_LABELS = {
    "sde_types": N_("Типы (SDE)"), "sde_blueprints": N_("Чертежи (SDE)"),
    "market_snapshot": N_("Рынок (снапшот)"), "market_adjusted_prices": "Adjusted prices",
    "system_cost_indices": N_("Cost-индексы"), "characters": N_("Персонажи"),
    "character_blueprints": N_("Чертежи чаров"),
}
SOURCE_NAMES = {"character": N_("Персонажи"), "market": N_("Рынок Jita"), "market_history": N_("История рынка"),
                "industry": N_("Индексы стоимости"), "sde": "SDE (Fuzzwork)",
                "structure_market": N_("Рынки-структуры")}
STATUS_TONE = {"ok": "green", "running": "cyan", "error": "red"}
# Метка сбоя в заметке источника от оркестратора синка: заметка лежит в БД на языке, что был
# при том синке, — поэтому ищем обе.
ERROR_MARKERS = ("ОШИБКА", "ERROR")  # i18n: ok — сопоставление с заметками синка на любом языке


def _src_name(source: str) -> str:
    return tr(SOURCE_NAMES.get(source, source))


class DashboardPage(Page):
    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, body = scroll_page()
        outer.addWidget(scroll)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        self.card_chars = StatCard("users", "cyan", tr("Персонажи"))
        self.card_stock = StatCard("boxes", "gold", tr("Склад"))
        self.card_data = StatCard("database", "green", tr("Данные"))
        self.card_builds = StatCard("clipboard", "violet", tr("Стройки"))
        for i, (c, tab, tip) in enumerate((
                (self.card_chars, "chars", tr("Персонажи и их роли в стройке")),
                (self.card_stock, "stock", tr("Что считается складом и где лежит")),
                (self.card_data, "dash", tr("Свежесть данных — ниже")),
                (self.card_builds, "builds", tr("Мои стройки (отчёты)")))):
            c.setMinimumHeight(122)
            c.setToolTip(tip)
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            c.clicked.connect(lambda t=tab: self.ctx.go(t))
            grid.addWidget(c, 0, i)
            grid.setColumnStretch(i, 1)
        body.addLayout(grid)

        self._sync_live = False  # в sync_status — «Запущено…»/«Идёт синк…» (ждём «Синк завершён.»)
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(self._build_sync_card(), 3)
        row.addWidget(self._build_autosync_card(), 2)
        body.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        c_src = Card(tr("Источники данных"), "sync", "green")
        self.sources = DataTable([
            Col("name", tr("Источник"), stretch=True, tip=lambda _v, r: r.get("note") or ""),
            Col("status", tr("Статус"), lambda v, _r: {"ok": tr("готово"), "running": tr("идёт…"),
                                                         "error": tr("ошибка")}.get(v, v or "—"),
                tone=lambda v, _r: STATUS_TONE.get(v)),
            Col("when", tr("Когда"), align="right"),
            Col("rows", tr("Строк"), lambda v, _r: fmt_int(v) if v is not None else "—", align="right"),
        ], sortable=False)
        c_src.body.addWidget(self.sources)
        self.src_notes = StatusLine()
        c_src.body.addWidget(self.src_notes)
        c_src.body.addStretch(1)
        row2.addWidget(c_src, 3)
        c_db = Card(tr("Наполнение БД"), "database", "steel")
        self.counts = DataTable([Col("name", tr("Таблица"), stretch=True),
                                 Col("n", tr("Строк"), lambda v, _r: fmt_int(v), align="right")],
                                sortable=False)
        c_db.body.addWidget(self.counts)
        c_db.body.addStretch(1)
        row2.addWidget(c_db, 2)
        body.addLayout(row2)
        body.addStretch(1)

        ctx.hub.status.connect(self._on_status)
        ctx.hub.data_changed.connect(self.refresh_stats)
        ctx.hub.reports_changed.connect(self.refresh_stats)
        ctx.hub.config_changed.connect(self.refresh_stats)
        ctx.hub.config_changed.connect(self._load_autosync)
        self._loaded = False

    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self._load_autosync()
        self.refresh_stats()

    # ---------------------------------------------------------------- синк
    def _build_sync_card(self):
        card = Card(tr("Синхронизация"), "sync", "cyan")
        card.body.addWidget(label(tr(
            "Сеть — только здесь: ESI (персонажи, рынок структуры, индексы) и Fuzzwork (SDE, "
            "снапшот Jita). Расчёты работают по локальной БД. Синк уважает ESI-кэш — повторный "
            "запуск может ничего не докачать."), "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.btn_chars = button(tr("Персонажи + рынки структур"), "users", "primary",
                                tip=tr("Чертежи, скиллы, ассеты, джобы, кошельки всех чаров; рынки "
                                       "структур из настроек; имена и системы структур (для склада)"))
        self.btn_public = button(tr("Рынок Jita и индексы"), "coins", "primary",
                                 tip=tr("Снапшот цен Jita (Fuzzwork), история (раз в сутки), "
                                        "adjusted prices и cost-индексы систем"))
        self.btn_sde = button(tr("Скачать SDE"), "download",
                              tip=tr("Дамп SDE с Fuzzwork — сотни МБ, несколько минут"))
        self.btn_auth = button(tr("Добавить персонажа (EVE SSO)"), "user", "hero", icon_size=14)
        self.btn_chars.clicked.connect(lambda: self._sync("character"))
        self.btn_public.clicked.connect(lambda: self._sync("public"))
        self.btn_sde.clicked.connect(self._sync_sde)
        self.btn_auth.clicked.connect(self._auth)
        grid.addWidget(self.btn_chars, 0, 0)
        grid.addWidget(self.btn_public, 0, 1)
        grid.addWidget(self.btn_sde, 0, 2)
        grid.addWidget(self.btn_auth, 1, 0, 1, 3)
        card.body.addLayout(grid)
        self.sync_status = StatusLine()
        card.body.addWidget(self.sync_status)
        self.auth_status = StatusLine()
        card.body.addWidget(self.auth_status)
        self.relogin_status = StatusLine()
        card.body.addWidget(self.relogin_status)
        card.body.addStretch(1)
        return card

    def _sync(self, source):
        def done(res, err):
            if err:
                self._sync_live = False
                self.sync_status.show_msg("err", err_text(err), 8000)
            else:
                self._sync_live = True
                self.sync_status.show_msg("info", tr("Запущено: {src}…",
                                                     src=tr(SOURCE_LABEL.get(source, source))))
            self.ctx.hub.poll()
        run_bg(lambda: self.svc.trigger_sync(source), done)

    def _sync_sde(self):
        box = QMessageBox(QMessageBox.Icon.Question, "SDE", tr("Скачать дамп SDE с Fuzzwork? Это сотни МБ "
                                                                "и несколько минут."),
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.button(QMessageBox.StandardButton.Yes).setText(tr("Скачать"))
        box.button(QMessageBox.StandardButton.No).setText(tr("Отмена"))
        if box.exec() == QMessageBox.StandardButton.Yes:
            self._sync("sde")

    def _auth(self):
        def done(_res, err):
            if err:
                self.auth_status.show_msg("err", err_text(err), 8000)
            else:
                self.auth_status.show_msg("info", tr("Открываю браузер EVE SSO — войди персонажем "
                                                     "и разреши доступ…"))
            self.ctx.hub.poll()
        run_bg(self.svc.trigger_auth, done)

    # ------------------------------------------------------- автообновление
    def _build_autosync_card(self):
        card = Card(tr("Автообновление"), "clock", "green")
        self.auto_on = check(tr("Обновлять в фоне, пока открыт пульт"))
        card.body.addWidget(self.auto_on)
        self.auto_market = NumberEdit(30, integer=True, width=90)
        self.auto_chars = NumberEdit(60, integer=True, width=90)
        card.body.addLayout(form_row(tr("Рынок Jita и индексы — раз в N минут"), self.auto_market))
        card.body.addLayout(form_row(tr("Персонажи (+ рынки структур) — раз в N минут"), self.auto_chars))
        save = button(tr("Сохранить"), "check", "primary")
        save.clicked.connect(self._save_autosync)
        h = QHBoxLayout()
        h.addWidget(save)
        h.addStretch(1)
        card.body.addLayout(h)
        self.auto_status = StatusLine()
        card.body.addWidget(self.auto_status)
        card.body.addWidget(label(tr("Срабатывает, когда с последнего успешного синка прошло больше "
                                     "интервала. ESI-кэш всё равно уважается."), "faint", wrap=True))
        card.body.addStretch(1)
        return card

    def _load_autosync(self):
        a = self.svc.load_cfg().autosync
        self.auto_on.setChecked(a.enabled)
        self.auto_market.setValue(a.market_minutes)
        self.auto_chars.setValue(a.character_minutes)

    def _save_autosync(self):
        upd = {"autosync": {"enabled": self.auto_on.isChecked(),
                            "market_minutes": max(1, self.auto_market.int_value(30)),
                            "character_minutes": max(1, self.auto_chars.int_value(60))}}

        def done(_r, err):
            if err:
                self.auto_status.show_msg("err", err_text(err))
                return
            self.auto_status.show_msg("ok", tr("Сохранено — планировщик подхватит за минуту."), 6000)
            self.ctx.hub.config_changed.emit()
        run_bg(lambda: self.svc.put_config(upd), done)

    # --------------------------------------------------------------- статус
    def _on_status(self, s):
        rows = []
        notes = []
        info = []
        for src in s.get("sources", []):
            note = src.get("note") or ""
            rows.append({"name": _src_name(src["source"]),
                         "status": src.get("status"),
                         "when": _age(src.get("last_success") or src.get("last_run")),
                         "rows": src.get("rows"), "note": note})
            if any(m in note for m in ERROR_MARKERS) or src.get("status") == "error":
                notes.append(f"{_src_name(src['source'])}: {note}")
            elif src["source"] == "structure_market" and note:
                info.append(f"{_src_name('structure_market')}: {note}")  # итог по каждой структуре
        self.sources.set_rows(rows)
        self.sources.fit_height()
        if notes:
            self.src_notes.show_msg("warn", "\n".join(notes + info))
        elif info:
            self.src_notes.show_msg("info", "\n".join(info))
        else:
            self.src_notes.clear_msg()
        counts = s.get("counts", {})
        self.counts.set_rows([{"name": tr(TABLE_LABELS.get(k, k)), "n": v} for k, v in counts.items()])
        self.counts.fit_height()

        r = s.get("runner", {})
        busy = bool(r.get("busy"))
        for b in (self.btn_chars, self.btn_public, self.btn_sde):
            b.setEnabled(not busy)
        if busy:
            cur = r.get("current")
            self._sync_live = True
            self.sync_status.show_msg("info", tr("Идёт синк: {src}…", src=tr(str(SOURCE_LABEL.get(cur, cur)))))
        elif r.get("error"):
            self._sync_live = False
            self.sync_status.show_msg("err", tr("Последняя ошибка: {error}", error=r["error"]))
        elif self._sync_live:
            self._sync_live = False
            self.sync_status.show_msg("ok", tr("Синк завершён."), 6000)
        auth = r.get("auth") or {}
        st = auth.get("status")
        self.btn_auth.setEnabled(st != "running")
        if st == "running":
            self.auth_status.show_msg("info", tr("Жду входа в браузере (EVE SSO)…"))
        elif st == "done":
            self.auth_status.show_msg("ok", tr("Добавлен: {name}. Запусти «Персонажи» — подтянуть его данные.",
                                               name=auth.get("name")), 12000)
        elif st == "error":
            self.auth_status.show_msg("err", tr("Вход не удался: {error}", error=auth.get("error")))
        rel = s.get("relogin") or []
        if rel:
            self.relogin_status.show_msg(
                "warn", tr("Нужно войти заново ({n}): {names}. EVE SSO не принимает их "
                           "сохранённый вход — нажми «Добавить персонажа (EVE SSO)» и войди каждым из них, "
                           "затем «Персонажи + рынки структур».", n=len(rel), names=", ".join(rel)))
        else:
            self.relogin_status.clear_msg()

        src = {x["source"]: x for x in s.get("sources", [])}
        mk = src.get("market") or {}
        ch = src.get("character") or {}
        worst = max(_hours(mk.get("last_success")), _hours(ch.get("last_success")))
        self.card_data.set(tr("рынок {age}", age=_age(mk.get("last_success"))),
                           tr("персонажи {age}", age=_age(ch.get("last_success"))),
                           None, theme.GREEN if worst < 24 else theme.GOLD if worst < 24 * 7 else theme.RED)

    def refresh_stats(self):
        svc = self.svc

        def work():
            chars = svc.characters()
            stock = svc.stock_contents(limit=0)
            reports = svc.list_reports()
            mode = svc.load_cfg().stock.mode
            return chars, stock, reports, mode

        def done(res, err):
            if err or not res:
                return
            chars, stock, reports, mode = res
            wallet = sum(c.get("wallet_balance") or 0 for c in chars)
            active = sum(c.get("active_jobs") or 0 for c in chars)
            n = len(chars)
            self.card_chars.set(f"{n} {plural(n, 'персонаж', 'персонажа', 'персонажей')}",
                                tr("кошельки {wallet} ISK · идёт джобов: {active}",
                                   wallet=fmt_isk_short(wallet), active=active))
            self.card_stock.set(f"{fmt_isk_short(stock['total_value'])} ISK",
                                tr("{n} позиций · {mode}", n=fmt_int(stock["types"]),
                                   mode=tr("выбран вручную") if mode == "custom" else tr("авто")))
            open_ = [r for r in reports if (r.get("progress") or 0) < 1]
            prog = (sum(r.get("progress") or 0 for r in open_) / len(open_)) if open_ else 0
            self.card_builds.set(tr("{n} в работе", n=len(open_)),
                                 tr("всего {total} · средний прогресс {pct}%", total=len(reports),
                                    pct=f"{prog * 100:.0f}")
                                 if open_ else tr("всего отчётов: {n}", n=len(reports)))
        run_bg(work, done)
        QTimer.singleShot(0, self.ctx.hub.poll)
