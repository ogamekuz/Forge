"""Вкладка «Расписание»: общий Gantt корзины по слотам персонажей, предупреждения (передачи
чертежей, нехватка ролей, запущенные джобы), сравнение вариантов «срок vs себестоимость»."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ...i18n import short_dt
from ...planner.instructions import GANTT_COLORS
from ...planner.schedule import RUNNING_JOBS_NOTE
from .. import theme
from ..widgets import (
    N_,
    Busy,
    Card,
    Col,
    DataTable,
    FlowLayout,
    Page,
    Stat,
    StatusLine,
    button,
    check,
    clear_layout,
    err_text,
    fmt_duration,
    fmt_isk,
    label,
    run_bg,
    scroll_page,
    tr,
)
from .basket_panel import BasketPanel

POOL = {"reaction": N_("реак"), "science": N_("наука"), "manufacturing": N_("пр")}
COPYING = 5  # activity_id копирования чертежа (копи-джобы T1 под инвенту)
def _pool(p: str) -> str:
    return tr(POOL.get(p, p))


def is_running_note(w: str) -> bool:
    """Заметка планировщика «Учтены запущенные джобы (N): …» — справка, а не предупреждение.
    Она приходит на текущем языке пульта (страница пересобирается и пересчитывает план при смене
    языка) — узнаём по началу переведённого шаблона, до первой подстановки."""
    return w.startswith(tr(RUNNING_JOBS_NOTE).split("{")[0])


def job_title(it: dict) -> str:
    """Подпись полосы Gantt: «Имя ×прогонов»; копи-джоб — «Копия: T1 — N коп. × M прог.»."""
    if it.get("activity_id") == COPYING:
        return tr("{name} — {copies} коп. × {runs} прог.", name=it["name"], copies=it["runs"],
                  runs=it.get("copy_runs") or 1)
    return f"{it['name']} ×{it['runs']}"


def owner_line(it: dict) -> str:
    """Строка подсказки Gantt про чертёж джоба: «владелец» или «нужна передача от …»."""
    status = it.get("owner_status")
    if status == "owner":
        return "<br>" + tr("чертёж/формула — свои (владелец)")
    if status == "transfer":
        who = ", ".join(it.get("owners") or []) or tr("владельца")
        return "<br><span style='color:#e8a93a'>" + tr("нужна передача чертежа от: {who}", who=who) + "</span>"
    return ""


class GanttView(QWidget):
    """Диаграмма Ганта: строка = персонаж · пул#слот, полоса = джоб (цвет — персонаж)."""

    ROW = 26
    LEFT = 200
    AXIS = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._rows: list[str] = []
        self._row_of: dict[tuple, int] = {}
        self._colors: dict[str, QColor] = {}
        self._span = 1.0
        self._bars: list[tuple[QRectF, dict]] = []
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._font = theme.font(11, 500)
        self._num = theme.font(11, 400, num=True)

    def set_schedule(self, sched: dict):
        self._items = list(sched.get("items") or [])
        self._span = max(sched.get("makespan") or 1.0, 1.0)
        names: list[str] = []
        for it in self._items:
            if it["character_name"] not in names:
                names.append(it["character_name"])
        self._colors = {n: QColor(GANTT_COLORS[i % len(GANTT_COLORS)]) for i, n in enumerate(names)}
        order = {"manufacturing": 0, "reaction": 1, "science": 2}
        keys = {(it["character_name"], it["pool"], it["slot"]) for it in self._items}
        keys_sorted = sorted(keys, key=lambda k: (names.index(k[0]), order.get(k[1], 9), k[2]))
        self._rows = [f"{c} · {_pool(p)}#{s + 1}" for c, p, s in keys_sorted]
        self._row_of = {k: i for i, k in enumerate(keys_sorted)}
        self.setFixedHeight(self.AXIS + 8 + self.ROW * max(len(self._rows), 1))
        self.update()

    def colors(self) -> dict[str, QColor]:
        return dict(self._colors)

    def sizeHint(self):
        return QSize(600, self.height())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        area = QRectF(self.LEFT, self.AXIS, max(10, w - self.LEFT - 8), self.ROW * max(len(self._rows), 1))
        self._bars = []
        # сетка по времени; подписи — сверху (видны без прокрутки длинного графика)
        ticks = self._ticks()
        p.setFont(self._num)
        for t in ticks:
            x = area.left() + area.width() * (t / self._span)
            p.setPen(QPen(theme.qcolor(theme.COOL, 0.07), 1))
            p.drawLine(QPointF(x, area.top() - 4), QPointF(x, area.bottom()))
            p.setPen(QColor(theme.FAINT))
            p.drawText(QRectF(x - 44, 2, 88, self.AXIS - 8), Qt.AlignmentFlag.AlignCenter,
                       fmt_duration(t) if t else tr("старт"))
        fm = QFontMetricsF(self._font)
        for i, name in enumerate(self._rows):
            y = area.top() + i * self.ROW
            if i % 2 == 0:
                p.fillRect(QRectF(0, y, w, self.ROW), theme.qcolor(theme.COOL, 0.018))
            p.setFont(self._font)
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(4, y, self.LEFT - 12, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(name, Qt.TextElideMode.ElideRight, self.LEFT - 14))
        p.setFont(self._font)
        for it in self._items:
            row = self._row_of.get((it["character_name"], it["pool"], it["slot"]))
            if row is None:
                continue
            y = area.top() + row * self.ROW + 3
            x0 = area.left() + area.width() * (it["start"] / self._span)
            x1 = area.left() + area.width() * (it["end"] / self._span)
            r = QRectF(x0, y, max(x1 - x0, 4.0), self.ROW - 6)
            base = self._colors.get(it["character_name"], QColor(theme.CYAN))
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            top = QColor(base)
            top.setAlphaF(0.95)
            bot = QColor(base)
            bot.setAlphaF(0.65)
            g.setColorAt(0, top)
            g.setColorAt(1, bot)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(r, 4, 4)
            if it.get("pool") == "reaction" or it.get("pool") == "science":
                p.setPen(QPen(QColor(theme.GOLD if it["pool"] == "reaction" else theme.VIOLET), 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
            text = job_title(it)
            if r.width() > 40:
                p.setPen(QColor("#061018"))
                p.drawText(r.adjusted(5, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() - 8))
            self._bars.append((r, it))

    def _ticks(self) -> list[float]:
        span = self._span
        for step in (3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 5 * 86400, 7 * 86400,
                     14 * 86400, 30 * 86400):
            if span / step <= 10:
                break
        n = int(span // step)
        return [i * step for i in range(n + 1)] + ([span] if span % step > step * 0.3 else [])

    def mouseMoveEvent(self, e):
        pos = e.position()
        for r, it in self._bars:
            if r.contains(pos):
                now = datetime.now()
                start = now + timedelta(seconds=it["start"])
                end = now + timedelta(seconds=it["end"])
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"<b>{job_title(it)}</b><br>{it['character_name']} · "
                                  f"{_pool(it['pool'])}#{it['slot'] + 1} · TE {it.get('te', 0)}<br>"
                                  f"{short_dt(start)} → {short_dt(end)} ({fmt_duration(it['end'] - it['start'])})"
                                  + owner_line(it), self)
                return
        QToolTip.hideText()


class PlannerPage(Page):
    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
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
        self.btn_plan = self.panel.add_action(button(tr("Построить расписание"), "gantt", "hero", icon_size=15))
        self.btn_plan.clicked.connect(self.run)
        self.btn_report = self.panel.add_action(button(tr("Сформировать отчёт по стройке"), "clipboard", "primary"))
        self.btn_report.clicked.connect(self.report)
        ropts = QHBoxLayout()
        self.use_stock = check(tr("учитывать склад"), True)
        self.use_res = check(tr("резервы строек"), True)
        ropts.addWidget(self.use_stock)
        ropts.addWidget(self.use_res)
        ropts.addStretch(1)
        self.panel.action_box.addLayout(ropts)
        self.btn_cmp = self.panel.add_action(button(tr("Сравнить варианты: срок vs себестоимость"), "activity",
                                                    "good"))
        self.btn_cmp.setToolTip(tr("Прогнать корзину при нескольких готовых настройках объединения/срока "
                                   "(набор сроков — «Настройки → Планировщик»)"))
        self.btn_cmp.clicked.connect(self.compare)

        scroll, self.body = scroll_page((2, 2, 10, 12), 14)
        h.addWidget(scroll, 1)
        self.busy = Busy()
        self.body.addWidget(self.busy)
        self.error = StatusLine()
        self.body.addWidget(self.error)
        self.cmp_host = QVBoxLayout()
        self.body.addLayout(self.cmp_host)

        self.stats_card = Card(corners=False)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        self.s_items = Stat(tr("Предметов"), "—")
        self.s_jobs = Stat(tr("Джобов"), "—")
        self.s_span = Stat(tr("Срок"), "—", "cyan")
        self.s_eta = Stat(tr("Готово к"), "—")
        self.s_transfers = Stat(tr("Передач чертежей"), "—")
        self.s_transfers.setToolTip(tr("Джобов, назначенных не владельцу чертежа/формулы — нужна передача. "
                                       "Политика — «Настройки → Планировщик»."))
        for i, s in enumerate((self.s_items, self.s_jobs, self.s_span, self.s_eta, self.s_transfers)):
            g.addWidget(s, 0, i)
        self.stats_card.body.addLayout(g)
        self.body.addWidget(self.stats_card)
        self.gantt_card = Card(tr("Расписание"), "gantt", "cyan")
        self.legend_host = QWidget()
        self.legend = FlowLayout(self.legend_host, spacing=12)
        self.gantt_card.body.addWidget(self.legend_host)
        self.gantt = GanttView()
        self.gantt_card.body.addWidget(self.gantt)
        self.warn = QVBoxLayout()
        self.warn.setSpacing(4)
        self.gantt_card.body.addLayout(self.warn)
        self.body.addWidget(self.gantt_card)
        self.stats_card.hide()
        self.gantt_card.hide()
        self.hint = Card(corners=False)
        self.hint.body.addWidget(label(tr("Добавь предметы в корзину и нажми «Построить расписание» — общий "
                                          "Gantt по слотам персонажей (роли — вкладка «Персонажи»; уже "
                                          "запущенные джобы занимают слоты до своего окончания)."),
                                       "muted", wrap=True))
        self.body.addWidget(self.hint)
        self.body.addStretch(1)
        ctx.basket.changed.connect(self._basket_changed)
        self._basket_changed()

    def _basket_changed(self):
        n = len(self.ctx.basket.items)
        for b in (self.btn_plan, self.btn_report, self.btn_cmp):
            b.setEnabled(n > 0)

    def run(self):
        b = self.ctx.basket.to_basket()
        self.busy.start(tr("Раскладываю джобы по слотам…"))
        self.error.clear_msg()
        self.btn_plan.setEnabled(False)

        def done(res, err):
            self.busy.stop()
            self.btn_plan.setEnabled(True)
            if err:
                self.error.show_msg("err", err_text(err))
                return
            self._render(res["schedule"])
        run_bg(lambda: self.svc.plan_basket(b), done)

    def report(self):
        b = self.ctx.basket.to_basket()
        use_stock, use_res = self.use_stock.isChecked(), self.use_res.isChecked()
        self.busy.start(tr("Формирую отчёт по стройке…"))
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

    def compare(self):
        b = self.ctx.basket.to_basket()
        self.busy.start(tr("Считаю варианты (каждый — полный расчёт + расписание)…"))
        clear_layout(self.cmp_host)

        def done(res, err):
            self.busy.stop()
            if err:
                self.error.show_msg("err", err_text(err))
                return
            rows = res["rows"]
            min_cost = min((r["total_cost"] for r in rows), default=None)
            min_span = min((r["makespan"] for r in rows), default=None)
            card = Card(tr("Сравнение вариантов: срок vs себестоимость"), "activity", "green")
            t = DataTable([
                Col("label", tr("Вариант"), stretch=True),
                Col("makespan", tr("Срок"), lambda v, _r: fmt_duration(v), "right",
                    tone=lambda v, _r: "cyan" if v == min_span else None),
                Col("total_cost", tr("Себестоимость"), lambda v, _r: fmt_isk(v), "right",
                    tone=lambda v, _r: "green" if v == min_cost else None),
                Col("jobs", tr("Джобов"), align="right"),
            ], sortable=False)
            t.set_rows(rows)
            t.fit_height(12)
            card.body.addWidget(t)
            card.body.addWidget(label(tr("Cyan — самый быстрый срок, зелёным — самая низкая себестоимость "
                                         "(обычно разные строки). Двойной клик — применить вариант к корзине."),
                                      "faint", wrap=True))
            t.activated.connect(self._apply_variant)
            self.cmp_host.addWidget(card)
        run_bg(lambda: self.svc.compare_basket(b), done)

    def _apply_variant(self, row):
        self.ctx.basket.set_opts(consolidate=row["consolidate"], auto_streams=row["auto_streams"],
                                 max_days=row["max_stream_days"])
        self.ctx.flash(tr("Применено к корзине: {label}", label=row["label"]), ms=5000)

    def _render(self, sched):
        self.hint.hide()
        self.stats_card.show()
        self.gantt_card.show()
        items = sched.get("items") or []
        self.s_items.set(str(len(self.ctx.basket.items)))
        self.s_jobs.set(str(len(items)))
        self.s_span.set(fmt_duration(sched.get("makespan") or 0), "cyan")
        eta = datetime.now() + timedelta(seconds=sched.get("makespan") or 0)
        self.s_eta.set(short_dt(eta))
        n_transfers = int(sched.get("transfer_jobs") or 0)
        self.s_transfers.set(str(n_transfers), "gold" if n_transfers else "green")
        self.gantt_card.set_meta(tr("{n} джоб(ов)", n=len(items)))
        self.gantt.set_schedule(sched)
        clear_layout(self.legend)
        for name, col in self.gantt.colors().items():
            w = QWidget()
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            dot = QWidget()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {col.name()}; border-radius: 5px;")
            hl.addWidget(dot)
            hl.addWidget(label(name, "muted"))
            self.legend.addWidget(w)
        for _tone, text in (("gold", tr("рамка золотом — реакция")),
                            ("violet", tr("рамка пурпуром — наука (инвента, копии)"))):
            self.legend.addWidget(label(text, "faint"))
        clear_layout(self.warn)
        for w in sched.get("warnings") or []:
            info = is_running_note(w)
            lb = label(("ℹ " if info else "⚠ ") + w, wrap=True)
            lb.setStyleSheet(f"color: {theme.tone_text('cyan' if info else 'gold')};")
            self.warn.addWidget(lb)
        if not items:
            self.warn.addWidget(label(tr("Нет джобов (всё покупается или на складе)."), "muted"))
