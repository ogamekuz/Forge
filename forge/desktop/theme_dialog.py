"""Окно «Тема»: темы пульта списком, у выбранной — живой предпросмотр.

Слева — темы из ``themes.py`` (образец цветов, имя, описание); справа — пульт в миниатюре в
выбранной теме: шапка с логотипом, названием и «пилюлями» статуса, вкладки-сегменты, панель с
таблицей и кнопками, плитки итогов, поля, чипы, строки статуса, прогресс и диаграмма Ганта. Он
собран из настоящих виджетов под stylesheet, палитрой и шрифтами этой темы (у виджетов со своей
отрисовкой — параметр ``spec``), а само окно остаётся в теме, с которой работает пульт: любую
тему видно, не уходя с текущей.

«Применить» отдаёт выбор сигналом ``chosen`` — главное окно перекрашивает приложение и
пересобирается на месте (``MainWindow.set_theme``).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import branding, theme
from .themes import THEMES, Theme
from .widgets import (
    N_,
    Backdrop,
    BrandTitle,
    Card,
    ElidedLabel,
    Logo,
    Pill,
    ProgressLine,
    Segmented,
    button,
    fmt_isk_short,
    fmt_pct,
    label,
    tr,
)

#: Высота строки темы в списке, px: все десять видны без прокрутки.
_ROW_HEIGHT = 56


class _Swatch(QWidget):
    """Тема в миниатюре для строки списка: фон, панель с выбранной вкладкой, две строки
    текста, кнопка-акцент, золото и цвета «хорошо»/«плохо»."""

    def __init__(self, spec: Theme, parent=None):
        super().__init__(parent)
        self._spec = spec
        self.setFixedSize(64, 44)

    def paintEvent(self, _e):
        t = self._spec
        c = theme.brush_color
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(c(t.line_strong), 1))
        p.setBrush(QColor(t.backdrop[0]))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 6, 6)
        p.setPen(QPen(c(t.line), 1))
        p.setBrush(c(t.panel_bg))
        p.drawRoundedRect(QRectF(5.5, 5.5, 53, 33), 3, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.accent))
        p.drawRoundedRect(QRectF(8.5, 8.5, 15, 5), 2.5, 2.5)
        p.setBrush(QColor(t.text))
        p.drawRoundedRect(QRectF(9, 18, 27, 3), 1.5, 1.5)
        p.setBrush(QColor(t.muted))
        p.drawRoundedRect(QRectF(9, 24, 19, 3), 1.5, 1.5)
        p.setBrush(QColor(t.accent))
        p.drawRoundedRect(QRectF(9, 30, 20, 6), 3, 3)
        p.setBrush(QColor(t.tone_color("gold")))
        p.drawEllipse(QPointF(49.5, 21.5), 4.5, 4.5)
        p.setBrush(QColor(t.tone_color("green")))
        p.drawEllipse(QPointF(43, 33), 2.5, 2.5)
        p.setBrush(QColor(t.tone_color("red")))
        p.drawEllipse(QPointF(50.5, 33), 2.5, 2.5)


class _ThemeRow(QWidget):
    """Строка списка: образец, имя (с меткой «Текущая») и описание в одну строку (целиком — в
    подсказке и над предпросмотром)."""

    def __init__(self, spec: Theme, current: bool, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 8, 4)
        lay.setSpacing(10)
        lay.addWidget(_Swatch(spec), 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.name = QLabel(spec.title)
        self.name.setFont(theme.font(13, 600))
        head.addWidget(self.name)
        self.badge: QLabel | None = None
        if current:
            self.badge = QLabel(tr("Текущая"))
            self.badge.setProperty("chip", "cyan")
            head.addWidget(self.badge)
        head.addStretch(1)
        col.addLayout(head)
        self.description = ElidedLabel(tr(spec.description), "faint")
        col.addWidget(self.description)
        lay.addLayout(col, 1)


class _MockGantt(QWidget):
    """Диаграмма Ганта в миниатюре — как на вкладке «Расписание»: строка = слот персонажа,
    полоса = джоб в цвете персонажа (ряды темы), у реакций — контур."""

    ROW = 24
    LEFT = 132
    # (персонаж, пул, слот) → полосы (начало, конец 0..1, подпись)
    ROWS = (
        (0, "manufacturing", 1, ((0.00, 0.44, "Sabre ×10"), (0.47, 0.83, "Hulk ×2"))),
        (0, "reaction", 1, ((0.05, 0.62, "Fullerides ×40"),)),
        (1, "manufacturing", 1, ((0.00, 0.30, "Fuel Block ×200"), (0.34, 1.00, "Ishtar ×4"))),
        (2, "manufacturing", 2, ((0.12, 0.71, "Nanofiber II ×60"),)),
    )

    def __init__(self, spec: Theme, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._font = theme.font(11, 500, spec=spec)
        self.setFixedHeight(self.ROW * len(self.ROWS) + 4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _e):
        t = self._spec
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font)
        fm = QFontMetricsF(self._font)
        w = self.width()
        area_w = max(10.0, w - self.LEFT - 6)
        pools = {"manufacturing": tr("пр"), "reaction": tr("реак")}
        for i, (who, pool, slot, bars) in enumerate(self.ROWS):
            y = 2 + i * self.ROW
            if i % 2 == 0:
                p.fillRect(QRectF(0, y, w, self.ROW), theme.qcolor(t.wash, 0.018))
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(4, y, self.LEFT - 10, self.ROW),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       f"Pilot {who + 1} · {pools[pool]}#{slot}")
            base = QColor(t.series[who % len(t.series)])
            for start, end, text in bars:
                r = QRectF(self.LEFT + area_w * start, y + 3, area_w * (end - start), self.ROW - 6)
                g = QLinearGradient(r.topLeft(), r.bottomLeft())
                top, bottom = QColor(base), QColor(base)
                top.setAlphaF(0.95)
                bottom.setAlphaF(0.65)
                g.setColorAt(0, top)
                g.setColorAt(1, bottom)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawRoundedRect(r, 4, 4)
                if pool == "reaction":
                    p.setPen(QPen(QColor(t.tone_color("gold")), 1.2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                p.setPen(QColor(t.on_series))
                p.drawText(r.adjusted(5, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                           fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() - 8))


class ThemePreview(QFrame):
    """Пульт в миниатюре в теме ``spec``: настоящие виджеты под её stylesheet, палитрой и
    шрифтами — какая бы тема ни была у приложения (stylesheet самого виджета сильнее
    stylesheet приложения)."""

    # (предмет, ROI, ISK/час, score) — строки таблицы «Что строить»
    ROWS = (("Sabre", 0.184, 41.2e6, 0.91), ("Hulk", 0.127, 28.4e6, 0.74),
            ("Ishtar", 0.061, 12.9e6, 0.52), ("Nanofiber Internal Structure II", -0.032, -2.1e6, 0.18))

    def __init__(self, spec: Theme, parent=None):
        super().__init__(parent)
        self.setMinimumSize(800, 500)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._content: QWidget | None = None
        self._spec = spec
        self.set_spec(spec)

    def spec(self) -> Theme:
        return self._spec

    def set_spec(self, spec: Theme) -> None:
        """Показать ``spec``: её stylesheet, палитра и шрифт на этой рамке, окно — заново (шрифты,
        иконки и своя отрисовка у каждой темы свои)."""
        self._spec = spec
        if self._content is not None:
            # прочь сразу: deleteLater ждёт цикла событий, а до него прежнее окно было бы видно
            self._content.hide()
            self._layout.removeWidget(self._content)
            self._content.setParent(None)
            self._content.deleteLater()
        self.setStyleSheet(theme.stylesheet(spec))
        self.setPalette(theme.palette(spec))
        self.setFont(theme.font(13, spec=spec))
        self._content = self._window(spec)
        self._layout.addWidget(self._content)

    # -- части окна --

    def _window(self, t: Theme) -> QWidget:
        root = Backdrop(star_zone=90, spec=t, animate=False)
        v = QVBoxLayout(root)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(10)
        v.addLayout(self._header(t))
        tabs = QHBoxLayout()
        tabs.addWidget(Segmented([("dash", tr("Обзор"), "gauge"), ("recommend", tr("Что строить"), "target"),
                                  ("calc", tr("Калькулятор"), "calc"), ("plan", tr("Расписание"), "gantt"),
                                  ("stock", tr("Склад"), "boxes")], value="recommend", small=True, spec=t))
        tabs.addStretch(1)
        v.addLayout(tabs)
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.addWidget(self._recommend(t), 0, 0)
        grid.addWidget(self._calculator(t), 0, 1)
        grid.addWidget(self._schedule(t), 1, 0, 1, 2)
        grid.setColumnStretch(0, 6)
        grid.setColumnStretch(1, 5)
        grid.setRowStretch(0, 1)
        v.addLayout(grid, 1)
        return root

    def _header(self, t: Theme) -> QHBoxLayout:
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(Logo(36, spec=t))
        head.addWidget(BrandTitle("FORGE", 22, spec=t, animate=False))
        version = QLabel(branding.VERSION)
        version.setProperty("chip", "gold")
        head.addWidget(version, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        head.addWidget(Pill(tr("Данные: {age}", age=tr("{n} мин назад", n=5)), "ok", spec=t))
        head.addWidget(Pill(tr("Отчёты: сервер выключен"), "warn", spec=t))
        return head

    def _button(self, t: Theme, text: str, icon: str | None, variant: str, color: str) -> QPushButton:
        b = QPushButton(text)
        if variant:
            b.setProperty("variant", variant)
        if icon:
            b.setIcon(theme.icon(icon, color, 14, t.faint))
            b.setIconSize(QSize(14, 14))
        b.setAutoDefault(False)  # Enter в окне — у «Применить», не у кнопок образца
        return b

    def _recommend(self, t: Theme) -> QWidget:
        card = Card(tr("Что строить"), "target", "gold", meta=tr("{n} поз.", n=len(self.ROWS)), spec=t)
        table = QTableWidget(len(self.ROWS), 4)
        table.setHorizontalHeaderLabels([tr("Предмет").upper(), "ROI", tr("ISK/час").upper(), "SCORE"])
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setFont(theme.font(11, 600, display=True, spacing=106, spec=t))
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        num = theme.font(13, 400, num=True, spec=t)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            header = table.horizontalHeaderItem(c)
            if header is not None:
                header.setTextAlignment(right)
        for r, (name, roi, isk_hour, score) in enumerate(self.ROWS):
            sign = "green" if roi >= 0 else "red"
            cells = ((name, None), (fmt_pct(roi), sign), (fmt_isk_short(isk_hour), None), (f"{score:.2f}", "cyan"))
            for c, (text, tone) in enumerate(cells):
                it = QTableWidgetItem(text)
                if c:
                    it.setTextAlignment(right)
                    it.setFont(num)
                if tone:
                    it.setForeground(QColor(t.tone_text(tone)))
                table.setItem(r, c, it)
        table.selectRow(0)
        table.setFixedHeight(hh.sizeHint().height() + 28 * len(self.ROWS) + 6)
        card.body.addWidget(table)
        for row in (
            ((N_("Показать"), "star", "hero", t.hero.fg), (N_("Проверить остатки"), "boxes", "hero-gold", t.hero_gold.fg)),
            ((N_("в корзину"), "plus", "primary", t.primary.fg), (N_("Отмена"), None, "", t.muted),
             (N_("Удалить"), "trash", "danger", t.danger.fg)),
        ):
            actions = QHBoxLayout()
            actions.setSpacing(8)
            for text, icon, variant, color in row:
                actions.addWidget(self._button(t, tr(text), icon, variant, color))
            actions.addStretch(1)
            card.body.addLayout(actions)
        card.body.addStretch(1)
        return card

    def _calculator(self, t: Theme) -> QWidget:
        card = Card(tr("Калькулятор"), "calc", "cyan", spec=t)
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        for title, value, tone in ((tr("Себестоимость"), fmt_isk_short(1.42e9), None),
                                   (tr("Прибыль"), fmt_isk_short(261.5e6), "green"),
                                   ("ROI", fmt_pct(0.184), "gold")):
            tile = QFrame()
            tile.setProperty("card", "tile")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(12, 8, 12, 9)
            tl.setSpacing(2)
            tl.addWidget(label(title, "faint"))
            val = QLabel(value)
            val.setFont(theme.font(17, 600, display=True, num=True, spec=t))
            if tone:
                val.setStyleSheet(f"color: {t.tone_text(tone)};")
            tl.addWidget(val)
            tiles.addWidget(tile, 1)
        card.body.addLayout(tiles)
        fields = QHBoxLayout()
        fields.setSpacing(8)
        item = QLineEdit("Sabre")
        item.setMinimumWidth(90)
        fields.addWidget(item, 1)
        hub = QComboBox()
        hub.addItems(["Jita", "Amarr"])
        fields.addWidget(hub)
        own = QCheckBox(tr("Только свои чертежи"))
        own.setChecked(True)
        fields.addWidget(own)
        card.body.addLayout(fields)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for text, tone in (("ME 10", "cyan"), ("ISK", "gold"), ("BPO", "green"), ("BPC", "red"),
                           ("T2", "violet"), ("TE 20", "steel")):
            chip = QLabel(text)
            chip.setProperty("chip", tone)
            chips.addWidget(chip)
        chips.addStretch(1)
        card.body.addLayout(chips)
        for kind, text in (("ok", tr("Склад сохранён — отчёты и «Из остатков» считают по нему.")),
                           ("err", tr("Отчёты: сервер не запущен"))):
            line = QLabel(text)
            line.setProperty("status", kind)
            line.setWordWrap(True)
            card.body.addWidget(line)
        card.body.addWidget(ProgressLine(0.65, spec=t))
        card.body.addStretch(1)
        return card

    def _schedule(self, t: Theme) -> QWidget:
        card = Card(tr("Расписание"), "gantt", "violet", spec=t, margins=(18, 12, 18, 12), spacing=8)
        card.body.addWidget(_MockGantt(t))
        return card


class ThemeDialog(QDialog):
    """Темы пульта с предпросмотром выбранной. «Применить» закрывает окно и отдаёт выбор
    сигналом ``chosen(theme_id)`` — если он отличается от ``running``, темы этого процесса."""

    chosen = Signal(str)

    def __init__(self, running: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Тема оформления"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._running = running if running in THEMES else next(iter(THEMES))
        self._ids = list(THEMES)

        self.list = QListWidget()
        self.list.setFixedWidth(330)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows: dict[str, _ThemeRow] = {}
        for theme_id, spec in THEMES.items():
            row = _ThemeRow(spec, theme_id == self._running)
            item = QListWidgetItem()
            item.setSizeHint(QSize(1, _ROW_HEIGHT))  # высота; ширина строки — по списку
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            self.rows[theme_id] = row

        self.title = label("", "title")
        self.title.setFont(theme.font(15, 600, display=True))
        self.kind = label("", "faint")
        self.description = label("", "muted")
        heading = QHBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(self.title)
        heading.addWidget(self.kind)
        heading.addWidget(self.description, 1)

        self.preview = ThemePreview(THEMES[self._running])
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addLayout(heading)
        right.addWidget(self.preview, 1)
        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.list)
        body.addLayout(right, 1)

        note = label(tr("Тема применяется сразу и запоминается. Окно при этом пересобирается — "
                        "несохранённые правки на вкладках (например, в «Настройках») пропадут. "
                        "HTML-отчёты от темы не зависят."), "faint", wrap=True)
        self.apply_btn = button(tr("Применить"), "check", "primary")
        self.apply_btn.setDefault(True)
        self.apply_btn.clicked.connect(self.accept)
        cancel = button(tr("Отмена"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(note, 1)
        bottom.addWidget(cancel)
        bottom.addWidget(self.apply_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 12)
        lay.setSpacing(10)
        lay.addLayout(body, 1)
        lay.addLayout(bottom)

        self.list.currentRowChanged.connect(self._on_row)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        self.list.setCurrentRow(self._ids.index(self._running))
        self.list.setFocus()
        # просторно, но в пределах рабочей области экрана
        available = self.screen().availableGeometry()
        self.resize(min(1240, available.width() - 40), min(720, available.height() - 40))

    def chosen_id(self) -> str:
        """Тема, выбранная в списке."""
        row = self.list.currentRow()
        return self._ids[row] if 0 <= row < len(self._ids) else self._running

    def select(self, theme_id: str) -> None:
        """Выбрать тему в списке (как клик по её строке)."""
        if theme_id in self._ids:
            self.list.setCurrentRow(self._ids.index(theme_id))

    def _on_row(self, row: int) -> None:
        if not 0 <= row < len(self._ids):
            return
        theme_id = self._ids[row]
        spec = THEMES[theme_id]
        self.title.setText(spec.title)
        self.kind.setText(tr("Тёмная") if spec.dark else tr("Светлая"))
        self.description.setText(tr(spec.description))
        if self.preview.spec() is not spec:
            self.preview.set_spec(spec)
        self.apply_btn.setText(tr("Оставить эту тему") if theme_id == self._running else tr("Применить"))

    def accept(self) -> None:
        theme_id = self.chosen_id()
        super().accept()
        if theme_id != self._running:
            self.chosen.emit(theme_id)

    def showEvent(self, e):
        super().showEvent(e)
        theme.style_titlebar(self)
