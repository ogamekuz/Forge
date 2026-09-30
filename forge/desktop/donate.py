"""Окно «Поддержать Forge»: как принято в EVE — перевод ISK персонажу автора в игре.

Без сети и без денег в самом Forge: окно лишь говорит, кому и как перевести, и копирует имя.
Кто автор и куда слать ISK — ``branding``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLayout, QVBoxLayout

from . import branding, theme
from .widgets import N_, EveImage, button, label, section, tr

STEPS = (
    N_("В игре найди персонажа: поиск или «Люди и места» → «{name}»."),
    N_("ПКМ по нему → Give Money (перевести ISK), сумма — сколько не жалко."),
    N_("В назначении можно написать «{reason}» — так донат не потеряется среди других переводов."),
)


class DonateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        to = branding.DONATE_TO
        self.setWindowTitle(tr("Поддержать Forge"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        v = QVBoxLayout(self)
        # Размер — по содержимому (переносы строк учтены); setMinimumWidth на окне здесь нельзя:
        # с ним Qt перестаёт брать минимальную высоту из раскладки, и строки наезжают друг на друга.
        v.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(EveImage("character", to.id, 96, to.name), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        title = label(tr("Поддержать Forge"))
        title.setFont(theme.font(20, 600, display=True))
        col.addWidget(title)
        col.addWidget(label(tr("Forge бесплатный и работает только у тебя на компьютере. Если он экономит тебе "
                               "время и ISK — переведи автору сколько не жалко. В EVE так и принято: донат — "
                               "это ISK персонажу, прямо в игре."), "muted", wrap=True))
        col.addStretch(1)
        top.addLayout(col, 1)
        v.addLayout(top)

        v.addWidget(section(tr("Кому")))
        who = QHBoxLayout()
        self.name = label(to.name, selectable=True)
        self.name.setFont(theme.font(16, 600))
        who.addWidget(self.name)
        who.addStretch(1)
        self.copy = button(tr("Скопировать имя"), "copy", "primary")
        self.copy.clicked.connect(self._copy)
        who.addWidget(self.copy)
        v.addLayout(who)
        self._copied = QTimer(self)  # живёт с окном: закрыли раньше — вернуть подпись некому
        self._copied.setSingleShot(True)
        self._copied.setInterval(2500)
        self._copied.timeout.connect(lambda: self.copy.setText(tr("Скопировать имя")))

        v.addWidget(section(tr("Как перевести")))
        for i, step in enumerate(STEPS, 1):
            v.addWidget(label(f"{i}. " + tr(step, name=to.name, reason=branding.DONATE_REASON), wrap=True))

        flag = QHBoxLayout()
        flag.setSpacing(8)
        corp, alliance = branding.CORPORATION, branding.ALLIANCE
        flag.addWidget(EveImage("corporation", corp.id, 24, f"{corp.name} [{corp.ticker}]"))
        flag.addWidget(EveImage("alliance", alliance.id, 24, f"{alliance.name} <{alliance.ticker}>"))
        flag.addWidget(label(f"{corp.name} [{corp.ticker}] · {alliance.name} <{alliance.ticker}>", "faint"))
        flag.addStretch(1)
        close = button(tr("Закрыть"))
        close.clicked.connect(self.accept)
        flag.addWidget(close)
        v.addLayout(flag)

    def _copy(self):
        QGuiApplication.clipboard().setText(branding.DONATE_TO.name)
        self.copy.setText(tr("Скопировано"))
        self._copied.start()
