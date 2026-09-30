"""Главное окно пульта Forge: шапка (логотип, название, флаг автора — корпорация и альянс, «Поддержать»,
статусы, язык RUS/ENG), вкладки-сегменты, страницы.

Каркас: статус (синк, данные, сервер отчётов) собирается
раз в 3 с в фоне — StatusHub раздаёт его страницам сигналом; сами проверки UI не блокируют.
Контекст для страниц — само окно: ``svc`` (ForgeService), ``hub``, ``basket``, ``server``,
``go(key)``, ``flash(text)``, ``open_report(url)``.

Язык (``forge.i18n``) — из ``[ui] lang`` конфига. Переключатель RUS/ENG пересобирает шапку,
вкладки и страницы на месте (``_build_ui``): тексты берутся через ``tr()`` при постройке, так что
новый язык виден сразу, без перезапуска. Остаются корзина (``basket``), хаб статуса, сервис и
сервер отчётов; несохранённые правки на вкладках при смене языка теряются.
"""

from __future__ import annotations

import ctypes
import sys
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
)

from .. import config as config_mod
from .. import i18n
from ..web.service import ForgeService
from . import branding, theme
from .donate import DonateDialog
from .server import ReportServer
from .state import BasketOptions, BasketState
from .widgets import (
    N_,
    Backdrop,
    BrandTitle,
    EveImage,
    LinkPill,
    Logo,
    Pill,
    Segmented,
    err_text,
    fade_in,
    label,
    run_bg,
    tr,
)

TABS = (
    ("dash", N_("Обзор"), "gauge"),
    ("recommend", N_("Что строить"), "target"),
    ("calc", N_("Калькулятор"), "calc"),
    ("plan", N_("Расписание"), "gantt"),
    ("builds", N_("Стройки"), "clipboard"),
    ("stock", N_("Склад"), "boxes"),
    ("chars", N_("Персонажи"), "users"),
    ("settings", N_("Настройки"), "sliders"),
)

# Подписи источников синка (показывать через tr()).
SOURCE_LABEL = {"character": N_("персонажи"), "public": N_("рынок и индексы"), "sde": "SDE"}


def _hours(iso: str | None) -> float:
    """Сколько часов прошло с момента ``iso`` (∞ — ни разу/не разобрать)."""
    if not iso:
        return float("inf")
    try:
        ts = datetime.fromisoformat(iso)
    except ValueError:
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds() / 3600


def _age(iso: str | None) -> str:
    if not iso:
        return tr("ни разу")
    try:
        ts = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    mins = (datetime.now(UTC) - ts).total_seconds() / 60
    if mins < 1:
        return tr("только что")
    if mins < 60:
        return tr("{n} мин назад", n=int(mins))
    if mins < 48 * 60:
        return tr("{n} ч назад", n=int(mins // 60))
    return tr("{n} дн назад", n=int(mins // 1440))


def _config_lang(config_path: str) -> str:
    """Язык из ``[ui] lang`` — до создания сервиса (он сразу пишет страницу-список отчётов)."""
    try:
        return config_mod.load(config_path).ui.lang
    except Exception:  # нет/битый конфиг — сервис сам скажет, что не так; язык по умолчанию
        return i18n.DEFAULT_LANG


class StatusHub(QObject):
    """Раз в 3 с — снимок состояния (в рабочем потоке), раздаётся сигналом ``status``.
    ``data_changed`` — закончился синк или вход персонажа через EVE SSO (данные в БД обновились);
    ``config_changed`` — сохранены настройки; ``reports_changed`` — создан/удалён отчёт."""

    status = Signal(dict)
    data_changed = Signal()
    config_changed = Signal()
    reports_changed = Signal()

    def __init__(self, svc: ForgeService, parent=None):
        super().__init__(parent)
        self.svc = svc
        self.snapshot: dict = {}
        self._busy = self._again = False
        self._was_syncing = self._was_auth = False
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.poll)

    def start(self):
        self._timer.start()
        self.poll()

    def poll(self):
        if self._busy:
            self._again = True
            return
        self._busy = True
        run_bg(self.svc.status, self._done)

    def _done(self, res, err):
        self._busy = False
        if res is not None:
            self.snapshot = res
            self.status.emit(res)
            syncing = bool(res.get("runner", {}).get("busy"))
            auth = (res.get("runner", {}).get("auth") or {}).get("status") == "running"
            if (self._was_syncing and not syncing) or (self._was_auth and not auth):
                self.data_changed.emit()
            self._was_syncing, self._was_auth = syncing, auth
        elif err is not None:
            print(f"[forge] status error: {err}", file=sys.stderr)
        if self._again:
            self._again = False
            self.poll()


def _dark_titlebar(win):
    """Заголовок окна Windows 11 в цвет темы (иначе белая полоса над космосом)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(win.winId())
        dwm = ctypes.windll.dwmapi

        def setattr_(attr, value):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        def colorref(hex_color):
            r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
            return (b << 16) | (g << 8) | r

        setattr_(20, 1)                         # DWMWA_USE_IMMERSIVE_DARK_MODE
        setattr_(35, colorref(theme.BG1))       # DWMWA_CAPTION_COLOR
        setattr_(36, colorref(theme.TEXT))      # DWMWA_TEXT_COLOR
        setattr_(34, colorref("#18293a"))       # DWMWA_BORDER_COLOR
    except Exception:  # noqa: S110 — старый Windows: заголовок останется системным
        pass


class MainWindow(QMainWindow):
    def __init__(self, config_path: str, start_server: bool = True):
        super().__init__()
        self.config_path = str(Path(config_path).resolve())
        i18n.set_lang(_config_lang(self.config_path))
        self.svc = ForgeService(self.config_path)
        self.state_dir = Path(self.config_path).parent / ".forge"
        self.setWindowIcon(theme.app_icon())
        self.resize(1320, 880)
        self.setMinimumSize(1080, 700)
        self._titlebar_done = False

        cfg = self.svc.load_cfg()
        self.basket = BasketState(self.state_dir / "basket.json", BasketOptions(
            me=cfg.ui.default_me, te=cfg.ui.default_te, consolidate=cfg.ui.default_consolidate,
            auto_streams=cfg.ui.default_auto_streams))
        self.server = ReportServer(self.config_path)
        if start_server:
            self.server.start()
        self.hub = StatusHub(self.svc, self)

        # --- строка состояния (переживает смену языка; текст — в _on_server) ---
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.setStatusBar(sb)
        self.sb_right = QLabel("")
        sb.addPermanentWidget(self.sb_right)

        self._build_ui()
        self.hub.status.connect(self._on_status)
        self._restore()
        self.hub.start()
        QTimer.singleShot(1500, self._on_server)

    def _build_ui(self):
        """Шапка, вкладки и страницы на текущем языке. Повторный вызов (смена языка) заменяет
        центральный виджет целиком — прежний Qt удаляет вместе со страницами и их подписками."""
        self.setWindowTitle(tr("Forge 3.0 — индустрия EVE Online"))
        root = Backdrop(star_zone=140)
        v = QVBoxLayout(root)
        v.setContentsMargins(24, 16, 24, 6)
        v.setSpacing(12)

        # --- шапка ---
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(Logo(48))
        brand = QVBoxLayout()
        brand.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(BrandTitle("FORGE", 30))
        ver = label("3.0", "")
        ver.setProperty("chip", "gold")
        title_row.addWidget(ver, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        brand.addLayout(title_row)
        # Без мест (стройка/рынок): они в настройках и меняются, а шапка — вывеска.
        self.tagline = label(tr("индустрия EVE Online"), "tagline")
        brand.addWidget(self.tagline)
        head.addLayout(brand)
        head.addStretch(1)
        flag = QHBoxLayout()
        flag.setSpacing(8)
        corp, alliance = branding.CORPORATION, branding.ALLIANCE
        self.logo_corp = EveImage("corporation", corp.id, 36, f"{corp.name} [{corp.ticker}]")
        self.logo_alliance = EveImage("alliance", alliance.id, 36, f"{alliance.name} <{alliance.ticker}>")
        flag.addWidget(self.logo_corp, 0, Qt.AlignmentFlag.AlignVCenter)
        flag.addWidget(self.logo_alliance, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addLayout(flag)
        self.pill_donate = LinkPill(tr("Поддержать"), "heart", "warn")
        self.pill_donate.setToolTip(tr("Донат автору — ISK персонажу {name} в игре", name=branding.DONATE_TO.name))
        self.pill_donate.clicked.connect(self._donate)
        head.addWidget(self.pill_donate, 0, Qt.AlignmentFlag.AlignVCenter)
        self.pill_sync = Pill(tr("Синк: проверяю…"), "info")
        self.pill_sync.setToolTip(tr("Синхронизация данных — вкладка «Обзор»"))
        self.pill_sync.clicked.connect(lambda: self.go("dash"))
        self.pill_server = Pill(tr("Отчёты: запускаю…"), "info")
        self.pill_server.setToolTip(tr("Локальный сервер HTML-отчётов по стройкам (только 127.0.0.1)"))
        self.pill_server.clicked.connect(self._open_reports_index)
        head.addWidget(self.pill_sync, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.pill_server, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lang = Segmented([(code, i18n.LANG_LABELS[code], None) for code in i18n.LANGS],
                              value=i18n.lang(), small=True)
        self.lang.setToolTip(tr("Язык интерфейса и новых HTML-отчётов"))
        self.lang.changed.connect(self.set_language)
        head.addWidget(self.lang, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(head)

        # --- вкладки ---
        self.tabs = Segmented([(key, tr(text), icon) for key, text, icon in TABS], value="dash")
        for i, (key, text, _icon) in enumerate(TABS, 1):
            self.tabs.button(key).setToolTip(f"{tr(text)}  (Ctrl+{i})")
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), root)  # живут с root: пересборка не дублирует
            sc.activated.connect(lambda k=key: self.go(k))
        self.tabs.changed.connect(self._switch)
        row = QHBoxLayout()
        row.addWidget(self.tabs)
        row.addStretch(1)
        v.addLayout(row)

        # --- страницы ---
        from .pages.builds import BuildsPage
        from .pages.calculator import CalculatorPage
        from .pages.characters import CharactersPage
        from .pages.dashboard import DashboardPage
        from .pages.planner import PlannerPage
        from .pages.recommend import RecommendPage
        from .pages.settings import SettingsPage
        from .pages.stock import StockPage

        self.stack = QStackedWidget()
        self.pages = {
            "dash": DashboardPage(self), "recommend": RecommendPage(self),
            "calc": CalculatorPage(self), "plan": PlannerPage(self), "builds": BuildsPage(self),
            "stock": StockPage(self), "chars": CharactersPage(self), "settings": SettingsPage(self),
        }
        for key, _t, _i in TABS:
            self.stack.addWidget(self.pages[key])
        v.addWidget(self.stack, 1)
        old = self.takeCentralWidget()
        self.setCentralWidget(root)
        if old is not None:  # смена языка: прежние шапку и страницы — прочь из окна сразу, удалить позже
            old.hide()       # (мы внутри сигнала переключателя, который в нём живёт)
            old.setParent(None)
            old.deleteLater()

    # --- контекст для страниц ---
    def go(self, key):
        self.tabs.set_value(key)
        self._switch(key)

    def _switch(self, key):
        page = self.pages.get(key)
        if page is None or self.stack.currentWidget() is page:
            return
        self.stack.setCurrentWidget(page)
        fade_in(page)
        if hasattr(page, "on_show"):
            page.on_show()

    def _show_tab(self, key):
        """Открыть вкладку без анимации (восстановление после запуска и смены языка)."""
        page = self.pages.get(key)
        if page is None:
            return
        self.tabs.set_value(key)
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            QTimer.singleShot(0, page.on_show)

    def set_language(self, code: str):
        """RUS/ENG: язык процесса → пересобрать окно на месте → сохранить ``[ui] lang`` и
        переписать страницу-список отчётов (уже созданные отчёты остаются на своём языке)."""
        code = i18n.normalize(code)
        if code == i18n.lang():
            return
        tab = self.tabs.value() or "dash"
        i18n.set_lang(code)
        self._build_ui()
        self._show_tab(tab)
        if self.hub.snapshot:
            self.hub.status.emit(self.hub.snapshot)  # новые страницы — сразу со статусом
        self._on_server()
        svc = self.svc

        def save():
            svc.put_config({"ui": {"lang": code}})
            svc.refresh_reports_index()

        def done(_res, err):
            if err is not None:
                self.flash(tr("Язык не сохранён в настройках: {error}", error=err_text(err)), "err", 10000)
        run_bg(save, done)

    def flash(self, text, _kind="info", ms=6000):
        self.statusBar().showMessage(text, ms)

    def open_report(self, path: str) -> bool:
        """Открыть отчёт (``/reports/…``) в браузере через встроенный сервер."""
        url = self.server.url(path)
        if not url:
            self.flash(tr("Сервер отчётов не запущен: {error}", error=self.server.error or tr("ещё стартует")),
                       "err")
            return False
        QDesktopServices.openUrl(QUrl(url))
        return True

    def _open_reports_index(self):
        if not self.open_report("/reports/"):
            self.go("builds")

    def _donate(self):
        dlg = DonateDialog(self)
        dlg.open()  # не блокирует цикл событий (и тесты); закрытие удаляет окно

    # --- статус ---
    def _on_status(self, s):
        r = s.get("runner", {})
        if r.get("busy"):
            src = r.get("current")
            self.pill_sync.set_state("info", tr("Синк: {source}…", source=tr(SOURCE_LABEL.get(src, src))),
                                     pulse=True)
        elif r.get("error"):
            self.pill_sync.set_state("err", tr("Синк: ошибка"), pulse=False)
            self.pill_sync.setToolTip(r["error"])
        else:
            src = {x["source"]: x for x in s.get("sources", [])}
            ages = {k: (src.get(k) or {}).get("last_success") for k in ("market", "character")}
            oldest = max(ages.values(), key=_hours, default=None)
            hours = _hours(oldest)
            kind = "ok" if hours < 24 else "warn" if hours < 24 * 7 else "err"
            self.pill_sync.set_state(kind, tr("Данные: {age}", age=_age(oldest)), pulse=False)
            self.pill_sync.setToolTip(tr("Рынок: {market} · персонажи: {chars}\n"
                                         "Обновить — вкладка «Обзор» (или включи автообновление)",
                                         market=_age(ages["market"]), chars=_age(ages["character"])))
        auth = r.get("auth") or {}
        if auth.get("status") == "running":
            self.flash(tr("Жду входа EVE SSO в браузере…"), ms=4000)
        self._on_server()

    def _on_server(self):
        if self.server.running:
            self.pill_server.set_state("ok", tr("Отчёты: 127.0.0.1:{port}", port=self.server.port), pulse=False)
            self.sb_right.setText(tr("{config}  ·  отчёты http://127.0.0.1:{port}/reports/",
                                     config=self.config_path, port=self.server.port))
        elif self.server.error:
            self.pill_server.set_state("err", tr("Отчёты: сервер не запущен"), pulse=False)
            self.pill_server.setToolTip(self.server.error)
            self.sb_right.setText(self.config_path)
        elif self.server.port is None:
            self.pill_server.set_state("warn", tr("Отчёты: сервер выключен"), pulse=False)
            self.sb_right.setText(self.config_path)
        else:
            self.pill_server.set_state("info", tr("Отчёты: запускаю…"), pulse=True)

    # --- окно ---
    def showEvent(self, e):
        super().showEvent(e)
        if not self._titlebar_done:
            self._titlebar_done = True
            _dark_titlebar(self)

    def _settings(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return QSettings(str(self.state_dir / "desktop.ini"), QSettings.Format.IniFormat)

    def _restore(self):
        st = self._settings()
        geo = st.value("geometry")
        if isinstance(geo, QByteArray) and not geo.isEmpty():
            self.restoreGeometry(geo)
        self._show_tab(st.value("tab", "dash"))

    def closeEvent(self, e):
        st = self._settings()
        st.setValue("geometry", self.saveGeometry())
        st.setValue("tab", self.tabs.value() or "dash")
        self.basket.save()
        self.server.stop()
        super().closeEvent(e)
