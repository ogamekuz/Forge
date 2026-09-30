"""Остатки материалов на складе (без сети, чистое чтение через storage).

Живёт в ``core`` (а не в ``web.report``), чтобы ``recommend`` тоже мог читать остатки склада
(рекомендации по максимальному использованию запасов) — ``recommend`` не имеет права
импортировать ``web``/``interface`` (слой выше, см. docs/ARCHITECTURE.md), а ``core`` для него доступен.

Что считать складом — настройка ``[stock]`` (см. ``config.Stock``): режим ``auto``
(структуры стройки + локации чертежей) или ``custom`` — выбранные системы и
локации (вкладка «Склад» пульта), плюс исключения, фильтр по персонажам, «не трогать» и т.д.
Каждый остаток помнит, где лежит (``StockLot``) — отчёт по стройке показывает, откуда брать, и
добавляет объём своих остатков в C-J6MT/Jita к доставке до места стройки.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ..config import Config, Stock
from ..i18n import N_, tr
from .locations import LocationResolver

# Префиксы ESI location_flag, означающие «предмет физически зафитован в слот» (модуль на
# корабле/структуре, риг, сабсистема) — НЕ доступный складской остаток, пока его не снимут,
# даже если формально лежит внутри структуры/корабля на складе GPLB-C. Пример: Multispectrum
# Shield Hardener II, прикрученный к стоящему в ангаре Viator, — не остаток «на складе GPLB-C»:
# в ангаре его не найти, он на корабле. Всё остальное (Cargo/Hangar/DroneBay/специализированные
# трюмы и т.п.) — реально доступно, считаем как обычно (если не исключено в [stock]).
_FITTED_SLOT_PREFIXES = (
    "HiSlot",
    "MedSlot",
    "LoSlot",
    "RigSlot",
    "SubSystemSlot",
    "ServiceSlot",
)

SHIP_CATEGORY = 6

# Где лежит остаток относительно логистики Forge: на месте стройки, в хабах закупки (оттуда
# довозим тем же фрахтом, что и покупки) или где-то ещё (доставка не известна).
HUB_BUILD = "gplb_c"
HUB_MARKET = "c_j6mt"
HUB_JITA = "jita"
HUB_OTHER = "other"
# Порядок, в котором стройка «берёт» остатки: сначала то, что уже на месте.
HUB_PRIORITY = (HUB_BUILD, HUB_MARKET, HUB_JITA, HUB_OTHER)


def is_fitted_slot(location_flag: str | None) -> bool:
    """True, если ``location_flag`` — слот фитинга (не свободный остаток). ``None``/пусто —
    флаг не известен (напр. запись ассета без ``location_flag``) — считаем ДОСТУПНЫМ, а не
    молча исключаем предмет из остатков."""
    return location_flag is not None and location_flag.startswith(_FITTED_SLOT_PREFIXES)


def gplb_on_hand(conn: sqlite3.Connection, cfg: Config) -> dict[int, int]:
    """Упрощённый склад (для сверки): остатки по ``type_id`` на складе GPLB-C (сумма по всем персонажам).

    «Склад GPLB-C» = структуры игрока (Engineering Complex + Refinery) И все
    ``blueprint_location_ids``, плюс контейнеры, лежащие прямо в этих локациях (один уровень
    вложенности). Зафитованное (см. ``is_fitted_slot``) не считается. Расчёты берут склад
    через ``on_hand``/``stock_view`` (настройка ``[stock]``); эта функция — для сверки и тестов.
    """
    s = cfg.structures
    base = {x for x in (s.gplb_engineering_complex_id, s.gplb_refinery_id) if x}
    base |= {int(x) for x in cfg.blueprint_location_ids}
    structure_ids = list(base)
    if not structure_ids:
        return {}
    ph = ",".join("?" * len(structure_ids))
    containers = [
        int(r["item_id"])
        for r in conn.execute(
            f"SELECT item_id FROM character_assets WHERE location_id IN ({ph})", structure_ids
        )
    ]
    loc_ids = structure_ids + containers
    ph2 = ",".join("?" * len(loc_ids))
    on_hand: dict[int, int] = {}
    for r in conn.execute(
        f"SELECT ca.type_id AS type_id, ca.quantity AS quantity, af.location_flag AS location_flag "
        f"FROM character_assets ca "
        f"LEFT JOIN character_asset_flags af ON af.item_id = ca.item_id "
        f"WHERE ca.location_id IN ({ph2})",
        loc_ids,
    ):
        if is_fitted_slot(r["location_flag"]):
            continue
        tid = int(r["type_id"])
        on_hand[tid] = on_hand.get(tid, 0) + int(r["quantity"] or 0)
    return on_hand


@dataclass
class StockLot:
    """Партия остатка одного типа в одной верхней локации (станция/структура/система)."""

    type_id: int
    quantity: int
    root_id: int
    system_id: int | None
    hub: str


@dataclass
class StockView:
    """Склад по настройке ``[stock]``: партии с местом хранения и итог по типам.

    ``totals`` — сколько можно брать (после «не трогать»); ``lots`` — те же остатки по
    локациям, уже уменьшенные на «не трогать», в порядке ``HUB_PRIORITY`` для каждого типа.
    """

    lots: dict[int, list[StockLot]] = field(default_factory=dict)
    totals: dict[int, int] = field(default_factory=dict)
    kept: dict[int, int] = field(default_factory=dict)

    def allocate(self, type_id: int, quantity: int) -> list[tuple[StockLot, int]]:
        """Разложить списание ``quantity`` единиц типа по партиям в порядке приоритета
        (сначала местные). Ничего не мутирует — для подсчёта «откуда брать» и доставки."""
        out: list[tuple[StockLot, int]] = []
        left = quantity
        for lot in self.lots.get(type_id, []):
            if left <= 0:
                break
            take = min(lot.quantity, left)
            if take > 0:
                out.append((lot, take))
                left -= take
        return out


def _hub_by_system(cfg: Config) -> dict[int, str]:
    out: dict[int, str] = {}
    for key, hub in (("jita", HUB_JITA), ("c_j6mt", HUB_MARKET), ("gplb_c", HUB_BUILD)):
        loc = cfg.locations.get(key)
        if loc and loc.system_id:
            out[loc.system_id] = hub  # стройка — последней: при совпадении систем важнее всего
    return out


def auto_location_ids(cfg: Config) -> set[int]:
    """Локации режима ``auto``: структуры стройки + локации чертежей."""
    s = cfg.structures
    base = {x for x in (s.gplb_engineering_complex_id, s.gplb_refinery_id) if x}
    base |= {int(x) for x in cfg.blueprint_location_ids}
    return base


def _flag_excluded(flag: str | None, prefixes: tuple[str, ...]) -> bool:
    return bool(flag) and bool(prefixes) and flag.startswith(prefixes)  # type: ignore[union-attr]


# Ассеты со всем, что нужно фильтрам склада: флаг, группа и категория типа.
ASSET_ROWS_SQL = (
    "SELECT ca.item_id AS item_id, ca.character_id AS character_id, ca.type_id AS type_id, "
    "ca.location_id AS location_id, ca.quantity AS quantity, af.location_flag AS flag, "
    "t.group_id AS group_id, t.category_id AS category_id "
    "FROM character_assets ca "
    "LEFT JOIN character_asset_flags af ON af.item_id = ca.item_id "
    "LEFT JOIN sde_types t ON t.type_id = ca.type_id"
)

# Почему фильтр склада отсёк предмет — для подсказок дерева «Где что лежит». Константы —
# русские ключи; ``ItemFilters.reason`` отдаёт их на языке пульта (``tr``).
REASON_CHARACTER = N_("персонаж не отмечен")
REASON_LOCATION = N_("исключённая локация")
REASON_FITTED = N_("модули в слотах фита")
REASON_SHIP = N_("собранный корабль")
REASON_TYPE = N_("запрет: предмет")
REASON_GROUP = N_("запрет: группа")


class ItemFilters:
    """Фильтры склада по самим предметам — всё, кроме выбора мест: персонажи, исключённые
    локации, фит, флаги (у предмета или у содержащего его контейнера/корабля), собранные
    корабли, запреты типов/групп. Одни и те же для ``stock_view`` и для дерева «Где что лежит»
    (там — по несохранённым галкам: сразу видно, что фильтры отсекают целиком)."""

    def __init__(self, sc: Stock, rows: Sequence[sqlite3.Row], items: Mapping[int, object]):
        self.chars = {int(x) for x in sc.character_ids}
        self.excl_locations = {int(x) for x in sc.exclude_location_ids}
        self.flag_prefixes = tuple(f for f in (x.strip() for x in sc.exclude_flags) if f)
        self.excl_types = {int(x) for x in sc.exclude_type_ids}
        self.excl_groups = {int(x) for x in sc.exclude_group_ids}
        self.exclude_fitted = sc.exclude_fitted
        self.exclude_ships = sc.exclude_assembled_ships
        self.flag_of = {int(r["item_id"]): r["flag"] for r in rows}
        self.parents = {int(r["location_id"]) for r in rows if r["location_id"] is not None}
        self.items = items

    def reason(self, r: sqlite3.Row, chain: Sequence[int]) -> str | None:
        """Почему предмет — не склад (``None`` — проходит все фильтры), на языке пульта. ``chain`` —
        цепочка от его локации до корня (``LocationResolver.chain``)."""
        item_id = int(r["item_id"])
        if self.chars and int(r["character_id"]) not in self.chars:
            return tr(REASON_CHARACTER)
        if item_id in self.excl_locations or any(loc in self.excl_locations for loc in chain):
            return tr(REASON_LOCATION)
        flag = r["flag"]
        if self.exclude_fitted and is_fitted_slot(flag):
            return tr(REASON_FITTED)
        if self.flag_prefixes:
            if _flag_excluded(flag, self.flag_prefixes):
                return tr("флаг {flag}", flag=flag)
            for loc in chain:
                if loc in self.items and _flag_excluded(self.flag_of.get(loc), self.flag_prefixes):
                    return tr("флаг {flag}", flag=self.flag_of.get(loc))
        if self.exclude_ships and r["category_id"] == SHIP_CATEGORY and item_id in self.parents:
            return tr(REASON_SHIP)
        if int(r["type_id"]) in self.excl_types:
            return tr(REASON_TYPE)
        if r["group_id"] is not None and int(r["group_id"]) in self.excl_groups:
            return tr(REASON_GROUP)
        return None


def stock_view(conn: sqlite3.Connection, cfg: Config,
               resolver: LocationResolver | None = None) -> StockView:
    """Склад по настройке ``cfg.stock`` (см. ``config.Stock``).

    Предмет идёт в склад, если его цепочка хранения (контейнер → корабль → … → станция/
    структура/система) проходит через выбранную локацию ИЛИ корень лежит в выбранной системе
    (в ``auto`` — только локации режима «Авто»), и при этом:
    - ни одно звено цепочки (и сам предмет) не в ``exclude_location_ids``;
    - владелец в ``character_ids`` (если список задан);
    - предмет не в слоте фита (``exclude_fitted``), ни его флаг, ни флаг содержащего его
      контейнера/корабля не начинается с ``exclude_flags``;
    - это не собранный корабль с чем-то внутри (``exclude_assembled_ships``);
    - тип/группа не в «запретах» (``exclude_type_ids``/``exclude_group_ids``).
    В конце из итога вычитается «не трогать» (``keep``), в первую очередь из местных партий.
    """
    sc = cfg.stock
    resolver = resolver or LocationResolver(conn, cfg)
    custom = sc.mode == "custom"
    incl_locations = {int(x) for x in sc.location_ids} if custom else auto_location_ids(cfg)
    incl_systems = {int(x) for x in sc.system_ids} if custom else set()
    if not incl_locations and not incl_systems:
        return StockView()
    hub_of_system = _hub_by_system(cfg)

    rows = conn.execute(ASSET_ROWS_SQL).fetchall()
    filters = ItemFilters(sc, rows, resolver.items())

    root_meta: dict[int, tuple[int | None, str]] = {}

    def meta(root: int) -> tuple[int | None, str]:
        if root not in root_meta:
            info = resolver.describe(root)
            sys_id = info.system_id
            if sys_id is not None and sys_id in hub_of_system:
                hub = hub_of_system[sys_id]
            elif sys_id is None and not custom:
                hub = HUB_BUILD  # auto: все локации склада считаются «на месте стройки»
            else:
                hub = HUB_OTHER
            root_meta[root] = (sys_id, hub)
        return root_meta[root]

    agg: dict[tuple[int, int], int] = {}
    for r in rows:
        if r["location_id"] is None:
            continue
        if filters.chars and int(r["character_id"]) not in filters.chars:
            continue  # дёшево и до цепочки: чужих персонажей большинство
        chain = resolver.chain(int(r["location_id"]))
        root = chain[-1]
        sys_id, _hub = meta(root)
        if not (any(loc in incl_locations for loc in chain)
                or (sys_id is not None and sys_id in incl_systems)):
            continue
        if filters.reason(r, chain) is not None:
            continue
        qty = int(r["quantity"] or 0)
        if qty <= 0:
            continue
        tid = int(r["type_id"])
        agg[(tid, root)] = agg.get((tid, root), 0) + qty

    view = StockView()
    for (tid, root), qty in agg.items():
        sys_id, hub = meta(root)
        view.lots.setdefault(tid, []).append(StockLot(tid, qty, root, sys_id, hub))
    order = {h: i for i, h in enumerate(HUB_PRIORITY)}
    for lots in view.lots.values():
        lots.sort(key=lambda lot: (order.get(lot.hub, 99), -lot.quantity))

    keep = {int(k.type_id): int(k.quantity) for k in sc.keep if k.quantity > 0}
    for tid, lots in view.lots.items():
        total = sum(lot.quantity for lot in lots)
        reserve = min(keep.get(tid, 0), total)
        if reserve:
            view.kept[tid] = reserve
            left = reserve
            for lot in lots:  # «не трогать» — из местных партий в первую очередь
                cut = min(lot.quantity, left)
                lot.quantity -= cut
                left -= cut
                if left <= 0:
                    break
            view.lots[tid] = [lot for lot in lots if lot.quantity > 0]
        if total - reserve > 0:
            view.totals[tid] = total - reserve
    view.lots = {t: lots for t, lots in view.lots.items() if lots}
    return view


def on_hand(conn: sqlite3.Connection, cfg: Config) -> dict[int, int]:
    """Сколько каждого ``type_id`` можно взять со склада (по настройке ``[stock]``)."""
    return dict(stock_view(conn, cfg).totals)


def subtract_reserved(totals: dict[int, int], reserved: dict[int, int] | None) -> dict[int, int]:
    """Остатки за вычетом резерва других строек (``report.external_reservations``)."""
    out = dict(totals)
    for tid, q in (reserved or {}).items():
        if tid in out:
            out[tid] = max(0, out[tid] - int(q))
    return out


def lots_label_summary(lots: Iterable[tuple[StockLot, int]], names: dict[int, str]) -> str:
    """«GPLB-C ×120, C-J6MT ×40» — откуда брать (``names``: root_id → подпись)."""
    parts = [f"{names.get(lot.root_id, str(lot.root_id))} ×{qty:,}".replace(",", " ")
             for lot, qty in lots]
    return ", ".join(parts)
