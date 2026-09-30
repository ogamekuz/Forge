"""Виджеты пульта: стеклянные панели с уголками в стиле UI EVE, карточки показателей,
сегменты, пульсирующие точки, «пилюли» статуса, ночное небо — и фоновые задачи.

Платформа: Qt трогаем только из UI-потока;
долгое (SQLite-расчёты, сеть) — ``run_bg()``: функция в потоке, результат — сигналом обратно.
Плюс то, что нужно индустрии: таблицы с числами ISK, поиск с подсказками, чипы, иконки предметов.
"""

from __future__ import annotations

import math
import random
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any, ClassVar

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFontMetricsF,
    QGradient,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import N_, dec, group, parse_number, plural, tr
from . import theme
from .icons import EveImages, TypeIcons

__all__ = ["N_", "plural", "tr"]  # переэкспорт для вкладок: from ..widgets import plural, tr

# ---------------------------------------------------------------------------
# Фоновые задачи
# ---------------------------------------------------------------------------

_alive: set = set()  # держим ретрансляторы, пока задача не закончилась


class _Relay(QObject):
    finished = Signal(object, object)

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        # слот QObject из UI-потока: сигнал из рабочего потока придёт очередью
        self.finished.connect(self._deliver)

    @Slot(object, object)
    def _deliver(self, result, error):
        _alive.discard(self)
        if self._callback is None:
            return
        try:
            self._callback(result, error)
        except RuntimeError as e:  # окно уже закрыто — C++-объекты удалены
            if "already deleted" not in str(e):
                raise


def run_bg(fn: Callable[[], Any], on_done: Callable[[Any, BaseException | None], None] | None = None):
    """fn() — в рабочем потоке; on_done(result, error) — в UI-потоке."""
    relay = _Relay(on_done)
    _alive.add(relay)

    def target():
        try:
            result, error = fn(), None
        except BaseException as e:
            result, error = None, e
        try:
            relay.finished.emit(result, error)
        except RuntimeError:
            pass  # приложение закрывается

    threading.Thread(target=target, daemon=True).start()


def err_text(e: BaseException | None) -> str:
    if e is None:
        return ""
    msg = getattr(e, "message", None) or str(e) or e.__class__.__name__
    return msg


# ---------------------------------------------------------------------------
# Форматирование (пробелы тысяч, B/M/k для кратких; по-английски —
# запятые тысяч и десятичная точка)
# ---------------------------------------------------------------------------

def fmt_int(n) -> str:
    if n is None:
        return "—"
    return group(f"{round(n):,}")


def fmt_isk(v, digits: int = 0) -> str:
    if v is None:
        return "—"
    return group(f"{v:,.{digits}f}")


def fmt_isk_short(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:.2f}T"
    if a >= 1e9:
        return f"{v / 1e9:.2f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{v / 1e3:.1f}k"
    return f"{v:.0f}"


def fmt_pct(v, digits: int = 1) -> str:
    if v is None:
        return "—"
    return dec(f"{v * 100:.{digits}f}%")


def fmt_duration(seconds) -> str:
    s = int(seconds or 0)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(tr("{n}д", n=d))
    if h:
        parts.append(tr("{n}ч", n=h))
    if m or not parts:
        parts.append(tr("{n}м", n=m))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def set_prop(w, name, value, deep=False):
    """Динамическое свойство + перерисовка по stylesheet. deep — перечитать стиль
    и у потомков (правила вида «QFrame[pill=ok] QLabel» сами их не обновляют)."""
    if w.property(name) == value:
        return
    w.setProperty(name, value)
    for x in [w] + (w.findChildren(QWidget) if deep else []):
        x.style().unpolish(x)
        x.style().polish(x)
        x.update()


def label(text="", role=None, wrap=False, selectable=False):
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                   | Qt.TextInteractionFlag.LinksAccessibleByMouse)
    return lb


def section(text: str) -> QLabel:
    """Заголовок-разделитель: КАПС с разрядкой, cyan (как подписи окон клиента EVE)."""
    lb = label(text.upper(), "section")
    lb.setFont(theme.font(11, 600, display=True, spacing=112))
    return lb


_VARIANT_ICON = {
    "primary": lambda: theme.tone_text("cyan"),
    "gold": lambda: theme.tone_text("gold"),
    "good": lambda: theme.tone_text("green"),
    "danger": lambda: theme.tone_text("red"),
    "hero": lambda: "#eafaff",
    "hero-gold": lambda: "#fff6e3",
    "link": lambda: theme.tone_text("cyan"),
}


def mnemonic_safe(text: str) -> str:
    """Текст кнопки/галки как есть: «&» у Qt — метка горячей клавиши (англ. «Jita market & indices»
    иначе превращается в «Jita market _indices»)."""
    return text.replace("&", "&&")


def button(text="", icon=None, variant=None, tip=None, icon_color=None, icon_size=15):
    b = QPushButton(mnemonic_safe(text))
    if variant:
        b.setProperty("variant", variant)
    if icon:
        color = icon_color or _VARIANT_ICON.get(variant, lambda: theme.MUTED)()
        b.setIcon(theme.icon(icon, color, icon_size))
        b.setIconSize(QSize(icon_size, icon_size))
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def hline():
    ln = QFrame()
    ln.setProperty("hline", True)
    return ln


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def fade_in(w, ms=240):
    """Появление вкладки: прозрачность 0→1, затем эффект снимаем."""
    eff = QGraphicsOpacityEffect(w)
    w.setGraphicsEffect(eff)
    anim = QVariantAnimation(w)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(ms)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(eff.setOpacity)
    anim.finished.connect(lambda: w.setGraphicsEffect(None))
    anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)


def confirm(parent, title: str, text: str, ok: str = N_("Удалить"), cancel: str = N_("Отмена")) -> bool:
    box = QMessageBox(QMessageBox.Icon.Question, title, text,
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    box.button(QMessageBox.StandardButton.Yes).setText(tr(ok))
    box.button(QMessageBox.StandardButton.No).setText(tr(cancel))
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


# ---------------------------------------------------------------------------
# Поверхности
# ---------------------------------------------------------------------------

class IconChip(QWidget):
    """Квадратик с иконкой в тоне."""

    def __init__(self, icon="spark", tone="cyan", size=28, parent=None):
        super().__init__(parent)
        self._icon, self._tone, self._size = icon, tone, size
        self.setFixedSize(size, size)

    def set(self, icon=None, tone=None):
        self._icon = icon or self._icon
        self._tone = tone or self._tone
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self._size - 1, self._size - 1)
        p.setPen(theme.qcolor(self._tone, 0.26))
        p.setBrush(theme.qcolor(self._tone, 0.10))
        p.drawRoundedRect(r, self._size * 0.28, self._size * 0.28)
        s = int(self._size * 0.54)
        pm = theme.icon_pixmap(self._icon, theme.tone_text(self._tone), s,
                               self.devicePixelRatioF())
        p.drawPixmap(int((self._size - s) / 2), int((self._size - s) / 2), pm)


def _paint_corners(widget: QWidget, tone: str = "cyan", alpha: float = 0.55, arm: float = 11.0,
                   radius: float = 16.0):
    """Уголки-скобы (мотив окон клиента EVE): слева сверху и справа снизу."""
    p = QPainter(widget)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(theme.qcolor(tone, alpha))
    pen.setWidthF(1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    w, h = widget.width(), widget.height()
    o = radius * 0.30 + 0.7
    path = QPainterPath()
    path.moveTo(o, o + arm)
    path.lineTo(o, o + 3)
    path.quadTo(o, o, o + 3, o)
    path.lineTo(o + arm, o)
    path.moveTo(w - o, h - o - arm)
    path.lineTo(w - o, h - o - 3)
    path.quadTo(w - o, h - o, w - o - 3, h - o)
    path.lineTo(w - o - arm, h - o)
    p.drawPath(path)


class Card(QFrame):
    """Стеклянная панель с необязательной шапкой: иконка + заголовок слева, мета/кнопки
    справа. Содержимое — в ``.body``. Уголки-скобы — в тоне шапки."""

    def __init__(self, title=None, icon=None, tone="cyan", meta=None, parent=None,
                 margins=(18, 16, 18, 18), spacing=12, corners=True):
        super().__init__(parent)
        self.setProperty("card", "panel")
        self._tone = tone
        self._corners = corners
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*margins)
        outer.setSpacing(spacing)
        self.head_right = QHBoxLayout()
        self.head_right.setSpacing(8)
        self.meta = label(meta or "", "meta")
        self.meta.setVisible(bool(meta))
        if title:
            head = QHBoxLayout()
            head.setSpacing(10)
            if icon:
                self.chip = IconChip(icon, tone)
                head.addWidget(self.chip)
            self.title = label(title, "title")
            self.title.setFont(theme.font(15, 600, display=True))
            head.addWidget(self.title)
            head.addStretch(1)
            head.addWidget(self.meta)
            head.addLayout(self.head_right)
            outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(spacing)
        outer.addLayout(self.body, 1)

    def set_meta(self, text):
        self.meta.setText(text)
        self.meta.setVisible(bool(text))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._corners:
            _paint_corners(self, self._tone, 0.45)


class StatCard(QFrame):
    """Карточка ключевого показателя: иконка, подпись КАПСОМ, крупное значение, подстрока."""

    clicked = Signal()

    def __init__(self, icon, tone, title, parent=None):
        super().__init__(parent)
        self.setProperty("card", "panel")
        self.setProperty("hover", True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._tone = tone
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setSpacing(9)
        self.chip = IconChip(icon, tone)
        head.addWidget(self.chip)
        t = label(title.upper(), "faint")
        t.setFont(theme.font(11, 600, display=True, spacing=110))
        head.addWidget(t)
        head.addStretch(1)
        self.dot = PulseDot(theme.FAINT, size=8)
        self.dot.hide()
        head.addWidget(self.dot)
        lay.addLayout(head)
        lay.addSpacing(10)
        self.value = label("—", "stat-value")
        self.value.setFont(theme.font(24, 600, display=True, num=True))
        lay.addWidget(self.value)
        lay.addSpacing(4)
        self.sub = label("", "stat-sub", wrap=True)
        lay.addWidget(self.sub)
        lay.addStretch(1)

    def set(self, value=None, sub=None, value_color=None, dot=None):
        if value is not None:
            self.value.setText(value)
        if sub is not None:
            self.sub.setText(sub)
        self.value.setStyleSheet(f"color: {value_color};" if value_color else "")
        if dot is None:
            self.dot.hide()
        else:
            self.dot.set_color(dot)
            self.dot.show()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        _paint_corners(self, self._tone, 0.40, arm=9)


class Stat(QFrame):
    """Малая плитка «подпись — значение» (сетка итогов расчёта)."""

    def __init__(self, title: str, value: str = "—", tone: str | None = None, parent=None):
        super().__init__(parent)
        self.setProperty("card", "tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 9)
        lay.setSpacing(2)
        self.title = label(title, "faint")
        lay.addWidget(self.title)
        self.value = label(value)
        self.value.setFont(theme.font(17, 600, display=True, num=True))
        lay.addWidget(self.value)
        self.set(value, tone)

    def set(self, value: str, tone: str | None = None):
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {theme.tone_text(tone)};" if tone else "")


class ClickFrame(QFrame):
    """QFrame, который ведёт себя как кнопка."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def mouseReleaseEvent(self, e):
        if (e.button() == Qt.MouseButton.LeftButton and self.isEnabled()
                and self.rect().contains(e.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


# ---------------------------------------------------------------------------
# Сегменты / статус
# ---------------------------------------------------------------------------

class Segmented(QFrame):
    """Табы-сегменты. options: [(key, text, icon|None), ...]."""

    changed = Signal(str)

    def __init__(self, options, value=None, small=False, parent=None):
        super().__init__(parent)
        self.setProperty("seg", True)
        if small:
            self.setProperty("small", True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._silent = False
        for key, text, icon in options:
            b = QPushButton(mnemonic_safe(text))
            b.setProperty("segbtn", True)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if icon:
                b.setIcon(theme.icon(icon, theme.MUTED, 14))
                b.setIconSize(QSize(14, 14))
            b.setProperty("icon_name", icon)
            self._group.addButton(b)
            self._buttons[key] = b
            lay.addWidget(b)
            b.toggled.connect(lambda on, k=key: self._on_toggled(k, on))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        if value is None and options:
            value = options[0][0]
        self.set_value(value, emit=False)

    def _paint_icons(self):
        for b in self._buttons.values():
            name = b.property("icon_name")
            if name:
                b.setIcon(theme.icon(name, "#eafaff" if b.isChecked() else theme.MUTED, 14))

    def _on_toggled(self, key, on):
        self._paint_icons()
        if on and not self._silent:
            self.changed.emit(key)

    def value(self):
        for k, b in self._buttons.items():
            if b.isChecked():
                return k
        return None

    def set_value(self, key, emit=False):
        b = self._buttons.get(key)
        if b is None:
            return
        self._silent = not emit
        try:
            b.setChecked(True)
        finally:
            self._silent = False
        self._paint_icons()

    def button(self, key):
        return self._buttons[key]


class StatusLine(QLabel):
    """Строка статуса: ok / err / info / warn; пустая — скрыта."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # свойство — до первой полировки: отступы/скругление из QSS Qt берёт только тогда
        self.setProperty("status", "info")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clear_msg)
        self.hide()

    def show_msg(self, kind, text, timeout_ms=None):
        set_prop(self, "status", kind)
        self.setText(text)
        self.show()
        if timeout_ms:
            self._timer.start(timeout_ms)
        else:
            self._timer.stop()

    def clear_msg(self):
        self.setText("")
        self.hide()


class PulseDot(QWidget):
    """Точка статуса со свечением; пульсирует."""

    def __init__(self, color=theme.CYAN, size=8, pulse=False, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        self._k = 1.0
        self.setFixedSize(size + 8, size + 8)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(2400)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self.set_pulsing(pulse)

    def _tick(self, v):
        self._k = 0.5 + 0.5 * math.cos(v * 2 * math.pi)
        self.update()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def set_pulsing(self, on):
        if on:
            self._anim.start()
        else:
            self._anim.stop()
            self._k = 1.0
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = self._size / 2 * (0.72 + 0.28 * self._k)
        glow = QRadialGradient(c, r * 2.4)
        g = QColor(self._color)
        g.setAlphaF(0.45 * (0.35 + 0.65 * self._k))
        glow.setColorAt(0, g)
        g2 = QColor(self._color)
        g2.setAlphaF(0.0)
        glow.setColorAt(1, g2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, r * 2.4, r * 2.4)
        col = QColor(self._color)
        col.setAlphaF(0.35 + 0.65 * self._k)
        p.setBrush(col)
        p.drawEllipse(c, r, r)


class Pill(ClickFrame):
    """«Пилюля» статуса в шапке: точка + текст."""

    TONE_COLORS: ClassVar[dict[str, str]] = {"ok": theme.GREEN, "err": theme.RED, "warn": theme.GOLD,
                                           "info": theme.CYAN}

    def __init__(self, text="", kind="info", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 14, 5)
        lay.setSpacing(4)
        self.setMinimumHeight(30)
        self.dot = PulseDot(self.TONE_COLORS[kind], 8)
        lay.addWidget(self.dot)
        self.text = QLabel(text)
        self.text.setFont(theme.font(13, 500))
        lay.addWidget(self.text)
        self.set_state(kind, text)

    def set_state(self, kind, text, pulse=None):
        set_prop(self, "pill", kind, deep=True)
        self.dot.set_color(self.TONE_COLORS.get(kind, theme.CYAN))
        self.dot.set_pulsing(kind == "ok" if pulse is None else pulse)
        self.text.setText(text)


class LinkPill(ClickFrame):
    """«Пилюля»-ссылка в шапке: иконка + текст, в тон пилюль статуса."""

    TONES: ClassVar[dict[str, str]] = {"ok": "green", "err": "red", "warn": "gold", "info": "cyan"}

    def __init__(self, text, icon, kind="warn", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 14, 5)
        lay.setSpacing(6)
        self.setMinimumHeight(30)
        ic = QLabel()
        ic.setPixmap(theme.icon_pixmap(icon, theme.tone_text(self.TONES.get(kind, "cyan")), 14))
        lay.addWidget(ic)
        self.text = QLabel(text)
        self.text.setFont(theme.font(13, 500))
        lay.addWidget(self.text)
        set_prop(self, "pill", kind, deep=True)


class BusyDots(QWidget):
    """Три «печатающие» точки — идёт расчёт."""

    def __init__(self, color=theme.CYAN, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._t = 0.0
        self.setFixedSize(30, 14)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1200)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._t = v
        self.update()

    def showEvent(self, e):
        self._anim.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._anim.stop()
        super().hideEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            ph = (self._t - i * 0.18) % 1.0
            k = max(0.0, math.sin(ph * math.pi))
            c = QColor(self._color)
            c.setAlphaF(0.3 + 0.7 * k)
            p.setBrush(c)
            p.drawEllipse(QPointF(5 + i * 10, 9 - 3 * k), 3, 3)


class Busy(QWidget):
    """Строка «идёт расчёт…» с точками — показывать на время run_bg."""

    def __init__(self, text=N_("Считаю…"), parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 4, 4, 4)
        h.setSpacing(8)
        self.dots = BusyDots()
        h.addWidget(self.dots)
        self.text = label(tr(text), "muted")
        h.addWidget(self.text)
        h.addStretch(1)
        self.hide()

    def start(self, text: str | None = None):
        if text:
            self.text.setText(text)
        self.show()

    def stop(self):
        self.hide()


# ---------------------------------------------------------------------------
# Бренд
# ---------------------------------------------------------------------------

class Logo(QWidget):
    def __init__(self, size=40, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.drawPixmap(0, 0, theme.logo_pixmap(self._size, self.devicePixelRatioF()))


class BrandTitle(QWidget):
    """Название с переливающимся градиентом cyan → золото (7 с на цикл)."""

    def __init__(self, text, px=28, parent=None):
        super().__init__(parent)
        self._text = text
        self._font = theme.font(px, 700, display=True, spacing=104)
        fm = QFontMetricsF(self._font)
        self._ascent = fm.ascent()
        self.setFixedSize(math.ceil(fm.horizontalAdvance(text) * 1.06) + 6, math.ceil(fm.height()) + 2)
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(7000)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self._anim.start()

    def _tick(self, v):
        self._phase = v
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(1.0, float(self.width()))
        x0 = -self._phase * 2 * w
        g = QLinearGradient(x0, 0, x0 + 2 * w, 0)
        g.setSpread(QGradient.Spread.RepeatSpread)
        for pos, col in ((0.0, "#3fb8e0"), (0.33, "#a9e4f7"), (0.55, "#e8a93a"),
                         (0.78, "#7fd0ec"), (1.0, "#3fb8e0")):
            g.setColorAt(pos, QColor(col))
        path = QPainterPath()
        path.addText(1, self._ascent, self._font, self._text)
        p.fillPath(path, g)


class Backdrop(QWidget):
    """Фон окна — глубокий космос EVE: вертикальный градиент, туманности (cyan слева, золото-
    оранжевая справа) и мерцающие звёзды над шапкой."""

    def __init__(self, star_zone=150, parent=None):
        super().__init__(parent)
        self.setObjectName("Root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._star_zone = star_zone
        rnd = random.Random(12)  # noqa: S311 — узор звёзд, не криптография
        self._stars = [(rnd.random(), rnd.random(), rnd.uniform(0.5, 1.4),
                        rnd.uniform(0, 2 * math.pi), rnd.uniform(0.6, 1.8)) for _ in range(80)]
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(110)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        if not self.isVisible() or self.window().isMinimized():
            return
        self._t += 0.11
        self.update(QRect(0, 0, self.width(), self._star_zone))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, QColor(theme.BG1))
        g.setColorAt(0.6, QColor(theme.BG0))
        g.setColorAt(1.0, QColor(theme.BG_DEEP))
        p.fillRect(e.rect(), g)
        # туманности: cyan слева сверху, золото/оранж справа сверху, слабый пурпур внизу справа
        for fx, fy, rx, ry, rgb, a, stop in ((0.06, -0.12, 1100, 600, (63, 184, 224), 30, 0.58),
                                             (0.97, -0.08, 950, 560, (232, 150, 58), 22, 0.55),
                                             (0.92, 1.10, 900, 520, (140, 110, 230), 12, 0.6)):
            p.save()
            p.translate(w * fx, h * fy)
            p.scale(1.0, ry / rx)
            rg = QRadialGradient(QPointF(0, 0), rx)
            rg.setColorAt(0.0, QColor(*rgb, a))
            rg.setColorAt(stop, QColor(*rgb, 0))
            rg.setColorAt(1.0, QColor(*rgb, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.drawEllipse(QPointF(0, 0), rx, rx)
            p.restore()
        zone = self._star_zone
        p.setPen(Qt.PenStyle.NoPen)
        for fx, fy, r, ph, sp in self._stars:
            a = 0.22 + 0.73 * (0.5 + 0.5 * math.sin(self._t * sp + ph))
            fade = 1.0 - fy
            c = QColor(226, 240, 255)
            c.setAlphaF(max(0.0, min(1.0, a * fade * 0.8)))
            p.setBrush(c)
            p.drawEllipse(QPointF(fx * w, fy * zone), r, r)


# ---------------------------------------------------------------------------
# Раскладка «потоком» (чипы)
# ---------------------------------------------------------------------------

class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do_layout(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + self._spacing
            if nx - self._spacing > rect.right() + 1 and line_h > 0:
                x, y = rect.x(), y + line_h + self._spacing
                nx, line_h = x + hint.width() + self._spacing, 0
            if not test:
                it.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x, line_h = nx, max(line_h, hint.height())
        return y + line_h - rect.y()


class ElidedLabel(QLabel):
    """Однострочная подпись с «…» по ширине (полный текст — в подсказке)."""

    def __init__(self, text="", role=None, parent=None):
        super().__init__(parent)
        if role:
            self.setProperty("role", role)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        self._elide()

    def full_text(self):
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight,
                                              max(10, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


class ScrollBody(QWidget):
    """Тело QScrollArea: минимум — ноль, высота считается по heightForWidth при реальной
    ширине (иначе под текстом в переносах оставалась пустота)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ScrollBody")

    def minimumSizeHint(self):
        return QSize(0, 0)


def scroll_page(widget_margins=(2, 2, 10, 12), spacing=16):
    """QScrollArea с прозрачным телом: (scroll, body_layout)."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = ScrollBody()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(*widget_margins)
    lay.setSpacing(spacing)
    scroll.setWidget(body)
    scroll.viewport().setAutoFillBackground(False)
    body.setAutoFillBackground(False)
    return scroll, lay


class Page(QWidget):
    """Базовая вкладка: прозрачный фон + ссылка на контекст окна (сервис, хаб статуса)."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctx = ctx

    @property
    def svc(self):
        return self.ctx.svc


# ---------------------------------------------------------------------------
# Иконка предмета
# ---------------------------------------------------------------------------

class TypeIcon(QLabel):
    """Иконка предмета EVE (асинхронно с Image Server CCP, с кэшем)."""

    def __init__(self, type_id: int | None = None, size: int = 22, parent=None):
        super().__init__(parent)
        self._tid: int | None = None
        self._size = size
        self.setFixedSize(size, size)
        TypeIcons.get().loaded.connect(self._on_loaded)
        self.set_type(type_id)

    def set_type(self, type_id: int | None):
        self._tid = int(type_id) if type_id else None
        self._apply()

    def _apply(self):
        pm = TypeIcons.get().pixmap(self._tid)
        self.setPixmap(pm.scaled(self._size * 2, self._size * 2, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
                       .scaled(self._size, self._size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))

    def _on_loaded(self, tid: int):
        if tid == self._tid:
            self._apply()


def type_qicon(type_id: int | None) -> QIcon:
    return QIcon(TypeIcons.get().pixmap(type_id))


class EveImage(QLabel):
    """Логотип альянса/корпорации или портрет персонажа (Image Server CCP), size×size.
    Пока картинка не загружена — место пустое (без заглушки: это не данные, а оформление)."""

    def __init__(self, kind: str, eid: int, size: int = 36, tip: str = "", parent=None):
        super().__init__(parent)
        self._key = (kind, int(eid))
        self._size = size
        self.setFixedSize(size, size)
        if tip:
            self.setToolTip(tip)
        EveImages.get().loaded.connect(self._on_loaded)
        self._apply()

    def _apply(self):
        pm = EveImages.get().pixmap(*self._key)
        if pm is None:
            return
        dpr = self.devicePixelRatioF()
        px = round(self._size * dpr)
        scaled = pm.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self.setPixmap(scaled)

    def _on_loaded(self, kind: str, eid: int):
        if (kind, eid) == self._key:
            self._apply()


# ---------------------------------------------------------------------------
# Чипы
# ---------------------------------------------------------------------------

class Chip(QFrame):
    """Чип: [иконка] текст [×]. ``remove(key)`` по крестику."""

    remove = Signal(object)

    def __init__(self, key, text: str, icon: str | None = None, type_id: int | None = None,
                 tone: str = "cyan", removable: bool = True, tip: str | None = None, parent=None):
        super().__init__(parent)
        self.key = key
        self.setProperty("chip", True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9 if (icon or type_id) else 11, 3, 4 if removable else 11, 3)
        lay.setSpacing(6)
        if type_id:
            lay.addWidget(TypeIcon(type_id, 16))
        elif icon:
            ic = QLabel()
            ic.setPixmap(theme.icon_pixmap(icon, theme.tone_text(tone), 13, self.devicePixelRatioF()))
            lay.addWidget(ic)
        txt = QLabel(text)
        txt.setFont(theme.font(12, 500))
        lay.addWidget(txt)
        if tip:
            self.setToolTip(tip)
        if removable:
            x = button("", "x", "ghost", tip=tr("Убрать"), icon_size=12)
            x.setFixedSize(22, 22)
            x.clicked.connect(lambda: self.remove.emit(self.key))
            lay.addWidget(x)


class ChipList(QWidget):
    """Набор чипов потоком + подпись «пусто». ``items``: [(key, text, type_id|None)]."""

    removed = Signal(object)

    def __init__(self, empty_text: str = N_("— ничего не выбрано"), icon: str | None = None,
                 tone: str = "cyan", with_type_icons: bool = False, parent=None):
        super().__init__(parent)
        self._flow = FlowLayout(self, spacing=6)
        self._empty_text = tr(empty_text)
        self._icon = icon
        self._tone = tone
        self._with_icons = with_type_icons
        self.set_items([])

    def set_items(self, items: Iterable[tuple]):
        clear_layout(self._flow)
        items = list(items)
        for it in items:
            key, text = it[0], it[1]
            tid = it[2] if len(it) > 2 and self._with_icons else None
            chip = Chip(key, text, icon=self._icon, type_id=tid, tone=self._tone)
            chip.remove.connect(self.removed.emit)
            self._flow.addWidget(chip)
        if not items:
            self._flow.addWidget(label(self._empty_text, "faint"))
        self.updateGeometry()


# ---------------------------------------------------------------------------
# Поля ввода
# ---------------------------------------------------------------------------

class NumberEdit(QLineEdit):
    """Число с «человеческим» вводом: пробелы тысяч и запятая как десятичный разделитель.
    ``value()`` → float | None (пусто/мусор — None). ``integer`` — округлять до целого."""

    def __init__(self, value=None, placeholder: str = "", integer: bool = False, width: int | None = 120,
                 compact: bool = False, parent=None):
        super().__init__(parent)
        self._integer = integer
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setPlaceholderText(placeholder)
        if compact:
            self.setProperty("compact", True)
        if width:
            self.setFixedWidth(width)
        self.setValue(value)

    def value(self) -> float | None:
        v = parse_number(self.text())
        if v is None:
            return None
        return float(round(v)) if self._integer else v

    def int_value(self, default: int = 0) -> int:
        v = self.value()
        return int(v) if v is not None else default

    def setValue(self, v) -> None:
        if v is None or v == "":
            self.setText("")
            return
        if self._integer:
            self.setText(str(round(float(v))))
            return
        f = float(v)
        self.setText(f"{f:g}" if abs(f) < 1e15 else str(f))


class Stepper(QWidget):
    """− [число] + (вместо стрелок QSpinBox)."""

    changed = Signal(int)

    def __init__(self, lo: int, hi: int, value: int = 0, width: int = 56, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.spin = QSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setValue(value)
        self.spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin.setFixedWidth(width)
        self.spin.setProperty("compact", True)
        minus = button("", "minus", tip=tr("Меньше"), icon_size=12)
        plus = button("", "plus", tip=tr("Больше"), icon_size=12)
        for b in (minus, plus):
            b.setFixedSize(28, 28)
            b.setProperty("variant", "ghost")
        minus.clicked.connect(self.spin.stepDown)
        plus.clicked.connect(self.spin.stepUp)
        self.spin.valueChanged.connect(self.changed.emit)
        h.addWidget(minus)
        h.addWidget(self.spin)
        h.addWidget(plus)

    def value(self) -> int:
        return self.spin.value()

    def setValue(self, v: int) -> None:
        self.spin.setValue(int(v))


def check(text: str, checked: bool = False, tip: str | None = None) -> QCheckBox:
    cb = QCheckBox(mnemonic_safe(text))
    cb.setChecked(checked)
    cb.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        cb.setToolTip(tip)
    return cb


def form_row(text: str, widget: QWidget, tip: str | None = None, stretch: bool = True) -> QHBoxLayout:
    """Строка формы: подпись слева, поле справа."""
    h = QHBoxLayout()
    h.setSpacing(10)
    lb = label(text, "label")
    lb.setWordWrap(True)
    if tip:
        lb.setToolTip(tip)
        widget.setToolTip(tip)
    h.addWidget(lb, 1 if stretch else 0)
    h.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
    return h


# ---------------------------------------------------------------------------
# Поиск с подсказками
# ---------------------------------------------------------------------------

class SearchPicker(QLineEdit):
    """Поле поиска с выпадающими подсказками (для предметов, групп, категорий, ригов, систем).

    ``fetch(q) -> list[dict]`` — вызывается в фоне (run_bg); ``describe(row) -> (text, sub,
    type_id|None)``. По выбору — сигнал ``picked(row)``."""

    picked = Signal(dict)

    def __init__(self, fetch: Callable[[str], list[dict]],
                 describe: Callable[[dict], tuple[str, str, int | None]],
                 placeholder: str = N_("Найти…"), min_chars: int = 2, clear_on_pick: bool = True,
                 parent=None):
        super().__init__(parent)
        self._fetch = fetch
        self._describe = describe
        self._min = min_chars
        self._clear_on_pick = clear_on_pick
        self._seq = 0
        self.setPlaceholderText(tr(placeholder))
        self.setClearButtonEnabled(True)
        self.addAction(theme.icon("search", theme.FAINT, 14), QLineEdit.ActionPosition.LeadingPosition)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(220)
        self._timer.timeout.connect(self._search)
        self.textEdited.connect(lambda _t: self._timer.start())
        self._popup = QListWidget(self)  # своё окно, но удаляется вместе с полем (пересборка на смену языка)
        self._popup.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self._popup.setProperty("popup", True)
        self._popup.setStyleSheet(
            f"QListWidget {{ background: {theme.POPUP}; border: 1px solid {theme.LINE_STRONG};"
            f" border-radius: 10px; padding: 4px; }}")
        self._popup.setIconSize(QSize(22, 22))
        self._popup.itemClicked.connect(self._choose)
        self._popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._rows: list[dict] = []
        TypeIcons.get().loaded.connect(self._icon_loaded)

    def _search(self):
        q = self.text().strip()
        if len(q) < self._min:
            self._popup.hide()
            return
        self._seq += 1
        seq = self._seq
        run_bg(lambda: self._fetch(q), lambda res, err, s=seq: self._show(res, err, s))

    def _show(self, rows, err, seq):
        if seq != self._seq or not self.hasFocus():
            return
        self._popup.clear()
        self._rows = list(rows or [])
        if err or not self._rows:
            self._popup.hide()
            return
        for row in self._rows:
            text, sub, tid = self._describe(row)
            it = QListWidgetItem(f"{text}   ·   {sub}" if sub else text)
            if tid:
                it.setIcon(type_qicon(tid))
                it.setData(Qt.ItemDataRole.UserRole + 1, tid)
            self._popup.addItem(it)
        w = max(self.width(), 360)
        h = min(320, 12 + self._popup.sizeHintForRow(0) * len(self._rows) + 2 * len(self._rows))
        self._popup.setFixedSize(w, max(h, 40))
        self._popup.move(self.mapToGlobal(QPoint(0, self.height() + 2)))
        self._popup.show()

    def _icon_loaded(self, tid):
        if not self._popup.isVisible():
            return
        for i in range(self._popup.count()):
            it = self._popup.item(i)
            if it.data(Qt.ItemDataRole.UserRole + 1) == tid:
                it.setIcon(type_qicon(tid))

    def _choose(self, item):
        row = self._rows[self._popup.row(item)]
        self._popup.hide()
        if self._clear_on_pick:
            self.clear()
        else:
            self.setText(self._describe(row)[0])
        self.picked.emit(row)

    def keyPressEvent(self, e):
        if self._popup.isVisible():
            if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                n = self._popup.count()
                r = self._popup.currentRow()
                r = (r + (1 if e.key() == Qt.Key.Key_Down else -1)) % max(n, 1)
                self._popup.setCurrentRow(r)
                return
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                it = self._popup.currentItem() or self._popup.item(0)
                if it is not None:
                    self._choose(it)
                return
            if e.key() == Qt.Key.Key_Escape:
                self._popup.hide()
                return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        QTimer.singleShot(180, self._popup.hide)
        super().focusOutEvent(e)

    def hideEvent(self, e):
        self._popup.hide()
        super().hideEvent(e)


def item_picker(svc, placeholder=N_("Название предмета…"), **kw) -> SearchPicker:
    return SearchPicker(lambda q: svc.search(q, 25),
                        lambda r: (r["name"], "" if r.get("buildable") else tr("покупка"), r["type_id"]),
                        placeholder, **kw)


def group_picker(svc, placeholder=N_("Найти EVE-группу (напр. Cruiser, Fuel Block)…"), **kw) -> SearchPicker:
    return SearchPicker(lambda q: svc.groups(q, 30),
                        lambda r: (r["name"], r.get("category") or "", None), placeholder, **kw)


def category_picker(svc, placeholder=N_("Найти EVE-категорию (напр. Ship, Module)…"), **kw) -> SearchPicker:
    return SearchPicker(lambda q: svc.categories(q, 30),
                        lambda r: (r["name"], "", None), placeholder, **kw)


def rig_picker(svc, placeholder=N_("Найти риг/сервис-модуль (напр. Reactor Efficiency)…"), **kw) -> SearchPicker:
    return SearchPicker(lambda q: svc.rigs(q, 30),
                        lambda r: (r["name"], r.get("group_name") or "", r["type_id"]), placeholder, **kw)


def system_picker(svc, placeholder=N_("Найти систему…"), **kw) -> SearchPicker:
    return SearchPicker(lambda q: svc.systems(q, 30),
                        lambda r: (r["name"], f"sec {r['security']:.1f}" if r.get("security") is not None else "",
                                   None), placeholder, **kw)


# ---------------------------------------------------------------------------
# Таблица
# ---------------------------------------------------------------------------

class _SortItem(QTableWidgetItem):
    """Ячейка, сортируемая по числу из UserRole (иначе «1 000» < «200» как строки)."""

    def __lt__(self, other):
        a = self.data(Qt.ItemDataRole.UserRole)
        b = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return str(self.text()).lower() < str(other.text()).lower()


class Col:
    """Описание колонки DataTable. ``fmt(value, row) -> str``; ``tone(value, row) -> tone|None``."""

    def __init__(self, key: str, title: str, fmt: Callable[[Any, dict], str] | None = None,
                 align: str = "left", width: int | None = None, tone=None, stretch: bool = False,
                 icon_key: str | None = None, tip: Callable[[Any, dict], str] | None = None):
        self.key, self.title, self.fmt, self.align = key, title, fmt, align
        self.width, self.tone, self.stretch, self.icon_key, self.tip = width, tone, stretch, icon_key, tip


def num_col(key, title, fmt=fmt_isk_short, width=None, tone=None, tip=None) -> Col:
    return Col(key, title, lambda v, _r: fmt(v), "right", width, tone, tip=tip)


class DataTable(QTableWidget):
    """Таблица строк-словарей по описаниям колонок. Иконки предметов — асинхронно (TypeIcons).
    Двойной клик — сигнал ``activated(row)``."""

    activated = Signal(dict)

    def __init__(self, cols: Sequence[Col], parent=None, row_height: int = 30, sortable: bool = True,
                 action: tuple[str, str, Callable[[dict], None]] | None = None):
        """``action`` — (подсказка, текст кнопки, callback(row)): кнопка в последней колонке."""
        super().__init__(parent)
        self._cols = list(cols)
        self._action = action
        self._rows: list[dict] = []
        self._icon_cells: dict[int, list[QTableWidgetItem]] = {}
        n = len(self._cols) + (1 if action else 0)
        self.setColumnCount(n)
        self.setHorizontalHeaderLabels([c.title.upper() for c in self._cols] + ([""] if action else []))
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setIconSize(QSize(20, 20))
        self.setWordWrap(False)
        self.setSortingEnabled(sortable)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hh.setFont(theme.font(11, 600, display=True, spacing=106))
        for i, c in enumerate(self._cols):
            if c.stretch:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
            elif c.width:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.setColumnWidth(i, c.width)
            else:
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
            hi = self.horizontalHeaderItem(i)
            if c.align == "right" and hi is not None:
                hi.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if action:
            hh.setSectionResizeMode(len(self._cols), QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(len(self._cols), 118)
        self.cellDoubleClicked.connect(self._dbl)
        TypeIcons.get().loaded.connect(self._icon_loaded)
        self._num_font = theme.font(13, 400, num=True)

    def rows(self) -> list[dict]:
        return list(self._rows)

    def set_header_tip(self, key: str, text: str) -> None:
        """Подсказка у заголовка колонки ``key`` (напр. откуда берётся цифра)."""
        for i, c in enumerate(self._cols):
            item = self.horizontalHeaderItem(i)
            if c.key == key and item is not None:
                item.setToolTip(text)

    def set_rows(self, rows: Iterable[dict]):
        self._rows = list(rows)
        self._icon_cells = {}
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self.clearContents()
        self.setRowCount(len(self._rows))
        # подпись кнопки — своё имя: ниже ``text`` — текст ячейки (иначе кнопка взяла бы текст
        # последней колонки вместо «в корзину»)
        tip, action_text, cb = self._action if self._action else ("", "", None)
        for r, row in enumerate(self._rows):
            for c, col in enumerate(self._cols):
                v = row.get(col.key)
                text = col.fmt(v, row) if col.fmt else ("" if v is None else str(v))
                it = _SortItem(text)
                it.setData(Qt.ItemDataRole.UserRole, v if isinstance(v, (int, float)) else text)
                it.setData(Qt.ItemDataRole.UserRole + 2, r)
                if col.align == "right":
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    it.setFont(self._num_font)
                elif col.align == "center":
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col.tone:
                    tone = col.tone(v, row)
                    if tone:
                        it.setForeground(QColor(theme.tone_text(tone)))
                if col.tip:
                    t = col.tip(v, row)
                    if t:
                        it.setToolTip(t)
                if col.icon_key:
                    tid = row.get(col.icon_key)
                    if tid:
                        it.setIcon(type_qicon(tid))
                        self._icon_cells.setdefault(int(tid), []).append(it)
                self.setItem(r, c, it)
            if self._action:
                holder = QWidget()
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(4, 2, 4, 2)
                b = button(action_text, "plus", "primary", tip=tip, icon_size=12)
                b.setProperty("variant", "primary")
                b.setStyleSheet("padding: 2px 10px; border-radius: 10px; font-size: 12px;")
                b.clicked.connect(lambda _=False, row=row, f=cb: f(row))
                hl.addWidget(b)
                self.setCellWidget(r, len(self._cols), holder)
        self.setSortingEnabled(sorting)

    def row_at(self, table_row: int) -> dict | None:
        it = self.item(table_row, 0)
        if it is None:
            return None
        idx = it.data(Qt.ItemDataRole.UserRole + 2)
        return self._rows[idx] if isinstance(idx, int) and idx < len(self._rows) else None

    def _dbl(self, r, _c):
        row = self.row_at(r)
        if row is not None:
            self.activated.emit(row)

    def _icon_loaded(self, tid):
        for it in self._icon_cells.get(tid, []):
            try:
                it.setIcon(type_qicon(tid))
            except RuntimeError:
                pass

    def fit_height(self, max_rows: int = 18):
        """Высота под содержимое (для таблиц внутри прокручиваемой страницы)."""
        n = min(self.rowCount(), max_rows)
        h = self.horizontalHeader().height() + n * self.verticalHeader().defaultSectionSize() + 6
        self.setMinimumHeight(max(h, 60))
        self.setMaximumHeight(max(h, 60) if self.rowCount() <= max_rows else 16777215)


class ProgressLine(QWidget):
    """Тонкая полоса прогресса cyan → золото (как «кольцо» в отчёте)."""

    def __init__(self, value: float = 0.0, height: int = 6, parent=None):
        super().__init__(parent)
        self._v = max(0.0, min(1.0, value))
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, v: float):
        self._v = max(0.0, min(1.0, v or 0.0))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 0, self.width(), self.height())
        rad = self.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(theme.COOL, 0.08))
        p.drawRoundedRect(r, rad, rad)
        if self._v > 0:
            g = QLinearGradient(0, 0, self.width(), 0)
            g.setColorAt(0, QColor(theme.CYAN))
            g.setColorAt(1, QColor(theme.GOLD))
            p.setBrush(g)
            p.drawRoundedRect(QRectF(0, 0, max(self.height(), self.width() * self._v), self.height()),
                              rad, rad)
