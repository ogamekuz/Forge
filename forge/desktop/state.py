"""Общее состояние Калькулятора и Расписания — корзина и её настройки.

Корзина хранится в ``.forge/basket.json`` рядом с
forge.toml (переживает перезапуск). Обе вкладки редактируют ОДНУ модель: изменение в одной
сразу видно в другой (сигнал ``changed``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..web.service import Basket


@dataclass
class BasketItem:
    type_id: int
    name: str
    runs: int = 1
    streams: int = 1
    buildable: bool = True
    buy: bool = False          # решено КУПИТЬ целиком (готовым)
    exact: bool = False        # «Точный ME/TE» для этого товара
    me: int | None = None
    te: int | None = None


@dataclass
class BasketOptions:
    max_days: float | None = None   # «Макс. дней/поток» для под-компонентов
    me: int = 0
    te: int = 0
    build: bool = True
    consolidate: bool = True
    auto_streams: bool = False
    buy_components: list[int] = field(default_factory=list)


class BasketState(QObject):
    changed = Signal()

    def __init__(self, path: Path, defaults: BasketOptions | None = None, parent=None):
        super().__init__(parent)
        self.path = path
        self.items: list[BasketItem] = []
        self.opts = defaults or BasketOptions()
        self._load()

    # ------------------------------------------------------------------ файл
    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            self.items = [BasketItem(**{k: v for k, v in it.items() if k in BasketItem.__dataclass_fields__})
                          for it in data.get("items", [])]
            o = data.get("opts") or {}
            self.opts = BasketOptions(**{k: v for k, v in o.items() if k in BasketOptions.__dataclass_fields__})
        except TypeError:
            self.items = []

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"items": [asdict(i) for i in self.items],
                                             "opts": asdict(self.opts)}, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except OSError:
            pass

    def _emit(self):
        self.save()
        self.changed.emit()

    # --------------------------------------------------------------- правка
    def find(self, type_id: int) -> BasketItem | None:
        return next((i for i in self.items if i.type_id == type_id), None)

    def add(self, type_id: int, name: str, runs: int = 1, buildable: bool = True) -> bool:
        """Добавить предмет; если уже в корзине — ничего не менять."""
        if self.find(type_id) is not None:
            return False
        self.items.append(BasketItem(int(type_id), name, max(1, int(runs)), 1, bool(buildable)))
        self._emit()
        return True

    def add_many(self, rows: list[tuple[int, str, int, bool]]):
        for tid, name, qty, buildable in rows:
            it = self.find(tid)
            if it is None:
                self.items.append(BasketItem(int(tid), name, max(1, int(qty)), 1, bool(buildable)))
            else:
                it.runs += max(1, int(qty))
        self._emit()

    def remove(self, type_id: int):
        self.items = [i for i in self.items if i.type_id != type_id]
        self._emit()

    def clear(self):
        self.items = []
        self.opts.buy_components = []
        self._emit()

    def update(self, type_id: int, **patch):
        it = self.find(type_id)
        if it is None:
            return
        for k, v in patch.items():
            setattr(it, k, v)
        self._emit()

    def set_opts(self, **patch):
        for k, v in patch.items():
            setattr(self.opts, k, v)
        self._emit()

    def toggle_component(self, type_id: int):
        comps = list(self.opts.buy_components)
        if type_id in comps:
            comps.remove(type_id)
        else:
            comps.append(type_id)
        self.set_opts(buy_components=comps)

    # --------------------------------------------------------------- расчёт
    def to_basket(self, consolidate: bool | None = None) -> Basket:
        o = self.opts
        return Basket(
            types=[i.type_id for i in self.items],
            runs=[max(1, i.runs) for i in self.items],
            streams=[max(1, i.streams) for i in self.items],
            me=o.me, te=o.te, build=o.build, max_stream_days=o.max_days or None,
            consolidate=o.consolidate if consolidate is None else consolidate,
            auto_streams=bool(o.consolidate and o.auto_streams),
            buy=[i.type_id for i in self.items if i.buy],
            buy_components=list(o.buy_components),
            me_override=[(i.me if i.exact and i.me is not None else None) for i in self.items],
            te_override=[(i.te if i.exact and i.te is not None else None) for i in self.items],
        )
