"""Иконки предметов EVE с Image Server CCP (https://images.evetech.net) — асинхронно, с кэшем.

Иконки грузит сам интерфейс (docs/ARCHITECTURE.md: «Иконки — с Image
Server CCP, выводятся из type_id»), расчётов это не касается. Скачанное кладём на диск
(``%LOCALAPPDATA%/Forge/cache/icons``) — повторно по сети не ходим; в памяти — QPixmap.
Пока иконки нет, виджеты показывают нейтральную заглушку и обновляются по сигналу ``loaded``.
Тем же путём (``EveImages``) — логотипы альянса/корпорации и портрет персонажа для шапки.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import theme

URL = "https://images.evetech.net/types/{tid}/icon?size={size}"
ENTITY_URLS = {
    "alliance": "https://images.evetech.net/alliances/{id}/logo?size={size}",
    "corporation": "https://images.evetech.net/corporations/{id}/logo?size={size}",
    "character": "https://images.evetech.net/characters/{id}/portrait?size={size}",
}
USER_AGENT = "forge/3.0 (personal EVE industry helper)"


def _get(net: QNetworkAccessManager, url: str) -> QNetworkReply:
    req = QNetworkRequest(QUrl(url))
    req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
    return net.get(req)


def _pixmap_from(reply: QNetworkReply) -> tuple[QPixmap, bytes] | None:
    """Картинка из ответа Image Server; None — сбой сети или не картинка."""
    if reply.error() != QNetworkReply.NetworkError.NoError:
        return None
    data = reply.readAll()
    pm = QPixmap()
    if not pm.loadFromData(data) or pm.isNull():
        return None
    return pm, bytes(data.data())


def _cache_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
    d = Path(base or ".") / "Forge" / "icons"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = Path(".forge") / "icons"
        d.mkdir(parents=True, exist_ok=True)
    return d


class TypeIcons(QObject):
    """Кэш иконок по type_id. ``pixmap(tid)`` — сразу (заглушка, если ещё грузится);
    сигнал ``loaded(tid)`` — когда настоящая иконка готова."""

    loaded = Signal(int)
    _instance: TypeIcons | None = None
    # Тесты (и офлайн-режим) выключают сеть: иконки — только из дискового кэша/заглушка.
    network_enabled: bool = os.environ.get("FORGE_NO_ICON_NET") != "1"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._net = QNetworkAccessManager(self)
        self._mem: dict[int, QPixmap] = {}
        self._pending: set[int] = set()
        self._failed: set[int] = set()
        self._dir = _cache_dir()
        self._placeholder = self._make_placeholder()

    @classmethod
    def get(cls) -> TypeIcons:
        if cls._instance is None:
            cls._instance = TypeIcons()
        return cls._instance

    @staticmethod
    def _make_placeholder() -> QPixmap:
        pm = QPixmap(64, 64)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(theme.qcolor("steel", 0.25))
        p.setBrush(theme.qcolor("steel", 0.08))
        p.drawRoundedRect(4, 4, 56, 56, 12, 12)
        p.end()
        return pm

    def has(self, tid: int) -> bool:
        return tid in self._mem

    def pixmap(self, tid: int | None) -> QPixmap:
        if not tid:
            return self._placeholder
        tid = int(tid)
        pm = self._mem.get(tid)
        if pm is not None:
            return pm
        f = self._dir / f"{tid}_64.png"
        if f.exists():
            pm = QPixmap(str(f))
            if not pm.isNull():
                self._mem[tid] = pm
                return pm
        self._request(tid)
        return self._placeholder

    def _request(self, tid: int) -> None:
        if tid in self._pending or tid in self._failed or not TypeIcons.network_enabled:
            return
        self._pending.add(tid)
        reply = _get(self._net, URL.format(tid=tid, size=64))
        reply.finished.connect(lambda r=reply, t=tid: self._done(r, t))

    def _done(self, reply: QNetworkReply, tid: int) -> None:
        self._pending.discard(tid)
        try:
            got = _pixmap_from(reply)
            if got is None:
                self._failed.add(tid)
                return
            self._mem[tid], data = got
            try:
                (self._dir / f"{tid}_64.png").write_bytes(data)
            except OSError:
                pass
            self.loaded.emit(tid)
        finally:
            reply.deleteLater()


class EveImages(QObject):
    """Логотипы альянсов/корпораций и портреты персонажей (``ENTITY_URLS``) — тот же дисковый
    кэш и выключатель сети, что у иконок предметов. ``pixmap(kind, id)`` — None, пока не
    загружено (виджет держит место пустым); сигнал ``loaded(kind, id)`` — когда готово."""

    loaded = Signal(str, int)
    SIZE = 128
    _instance: EveImages | None = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._net = QNetworkAccessManager(self)
        self._mem: dict[tuple[str, int], QPixmap] = {}
        self._pending: set[tuple[str, int]] = set()
        self._failed: set[tuple[str, int]] = set()
        self._dir = _cache_dir()

    @classmethod
    def get(cls) -> EveImages:
        if cls._instance is None:
            cls._instance = EveImages()
        return cls._instance

    def _file(self, key: tuple[str, int]) -> Path:
        return self._dir / f"{key[0]}_{key[1]}_{self.SIZE}.png"

    def pixmap(self, kind: str, eid: int) -> QPixmap | None:
        key = (kind, int(eid))
        pm = self._mem.get(key)
        if pm is not None:
            return pm
        f = self._file(key)
        if f.exists():
            pm = QPixmap(str(f))
            if not pm.isNull():
                self._mem[key] = pm
                return pm
        self._request(key)
        return None

    def _request(self, key: tuple[str, int]) -> None:
        if key in self._pending or key in self._failed or not TypeIcons.network_enabled:
            return
        self._pending.add(key)
        reply = _get(self._net, ENTITY_URLS[key[0]].format(id=key[1], size=self.SIZE))
        reply.finished.connect(lambda r=reply, k=key: self._done(r, k))

    def _done(self, reply: QNetworkReply, key: tuple[str, int]) -> None:
        self._pending.discard(key)
        try:
            got = _pixmap_from(reply)
            if got is None:
                self._failed.add(key)
                return
            self._mem[key], data = got
            try:
                self._file(key).write_bytes(data)
            except OSError:
                pass
            self.loaded.emit(*key)
        finally:
            reply.deleteLater()
