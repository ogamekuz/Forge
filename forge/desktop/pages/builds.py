"""Вкладка «Стройки»: сформированные отчёты с живым прогрессом (отметки в самом отчёте).
Отчёт открывается в браузере через встроенный сервер (там чек-листы, S-кривая, план/факт)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from ...i18n import short_dt
from .. import theme
from ..widgets import (
    Card,
    ClickFrame,
    ElidedLabel,
    Page,
    ProgressLine,
    StatusLine,
    TypeIcon,
    button,
    clear_layout,
    confirm,
    err_text,
    fmt_isk_short,
    label,
    run_bg,
    scroll_page,
    tr,
)


def _dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return short_dt(datetime.fromisoformat(iso))
    except ValueError:
        return iso


class BuildsPage(Page):
    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll, body = scroll_page()
        outer.addWidget(scroll)
        self.card = Card(tr("Мои стройки"), "clipboard", "violet")
        refresh = button(tr("Обновить"), "sync")
        refresh.clicked.connect(self.refresh)
        self.card.head_right.addWidget(refresh)
        web = button(tr("Список в браузере"), "external")
        web.clicked.connect(lambda: self.ctx.open_report("/reports/"))
        self.card.head_right.addWidget(web)
        self.status = StatusLine()
        self.card.body.addWidget(self.status)
        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        self.card.body.addLayout(self.list)
        self.card.body.addWidget(label(tr("Отчёт формируется из корзины — кнопка «Сформировать отчёт» в "
                                          "Калькуляторе или Расписании. Отметки «запущено» и факт-стоимость "
                                          "вносятся в самом отчёте и сохраняются здесь, в reports/."),
                                       "faint", wrap=True))
        body.addWidget(self.card)
        body.addStretch(1)
        ctx.hub.reports_changed.connect(self.refresh)

    def on_show(self):
        self.refresh()

    def refresh(self):
        run_bg(self.svc.list_reports, self._fill)

    def _fill(self, rows, err):
        clear_layout(self.list)
        if err:
            self.status.show_msg("err", err_text(err))
            return
        self.status.clear_msg()
        self.card.set_meta(tr("{n} отч.", n=len(rows)) if rows else "")
        if not rows:
            self.list.addWidget(label(tr("Пока нет отчётов."), "muted"))
            return
        for r in rows:
            self.list.addWidget(self._tile(r))

    def _tile(self, r):
        tile = ClickFrame()
        tile.setProperty("card", "tile")
        tile.setProperty("hover", True)
        tile.setToolTip(tr("Открыть отчёт в браузере"))
        v = QVBoxLayout(tile)
        v.setContentsMargins(14, 10, 10, 12)
        v.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        items = r.get("items") or []
        if items:
            head.addWidget(TypeIcon(items[0].get("type_id"), 26))
        title = ElidedLabel(r.get("title") or r.get("report_id"))
        title.setFont(theme.font(14, 600))
        head.addWidget(title, 1)
        prog = r.get("progress")
        p = 0 if prog is None else round(prog * 100)
        pl = label(f"{p}%")
        pl.setFont(theme.font(15, 600, display=True, num=True))
        pl.setStyleSheet(f"color: {theme.tone_text('green' if p >= 100 else 'cyan')};")
        head.addWidget(pl)
        open_b = button("", "external", "ghost", tip=tr("Открыть в браузере"), icon_size=14)
        url = r.get("url") or ""
        open_b.clicked.connect(lambda _=False, u=url: self.ctx.open_report(u))
        head.addWidget(open_b)
        del_b = button("", "trash", "ghost", tip=tr("Удалить стройку (отчёт, прогресс и факт-данные)"),
                       icon_color=theme.tone_text("red"), icon_size=14)
        del_b.clicked.connect(lambda _=False, row=r: self._delete(row))
        head.addWidget(del_b)
        v.addLayout(head)
        bar = ProgressLine(prog or 0.0)
        v.addWidget(bar)
        meta = label(tr("создан {created} · ETA {eta} · {done}/{jobs} джобов · {cost} ISK",
                        created=_dt(r.get("generated_at")), eta=_dt(r.get("eta")), done=r.get("done") or 0,
                        jobs=r.get("jobs") or 0, cost=fmt_isk_short(r.get("total_cost")))
                     + (" · " + tr("{n} поз.", n=len(items)) if len(items) > 1 else ""), "faint")
        v.addWidget(meta)
        tile.clicked.connect(lambda u=url: self.ctx.open_report(u))
        return tile

    def _delete(self, r):
        if not confirm(self, tr("Удалить стройку"),
                       tr("Удалить стройку «{title}»?\nПрогресс и факт-данные удалятся безвозвратно, "
                          "резерв склада освободится.", title=r.get("title"))):
            return

        def done(_res, err):
            if err:
                self.status.show_msg("err", tr("Не удалось удалить: {error}", error=err_text(err)))
                return
            self.ctx.hub.reports_changed.emit()
        run_bg(lambda: self.svc.delete_report(r["report_id"]), done)
