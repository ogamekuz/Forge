"""Справочник локаций: где физически лежит предмет (система → станция/структура → контейнер).

Без сети — только локальная БД: NPC-станции и системы (SDE), структуры игроков
(``universe_structures`` — заполняет синк персонажей через ESI), ассеты чаров (контейнеры и
корабли — промежуточные локации) и конфиг как фолбэк-подписи для структур, которые ESI ещё не
резолвил (facilities/[structures] — они по определению в системе стройки или рынка C-J6MT).

Используется складом (``core.stock``), подписями «Где искать чертежи» и вкладкой «Склад» пульта.
"""

from __future__ import annotations

import functools
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from ..config import Config
from ..i18n import tr

# Диапазоны id EVE: солнечные системы (k-space + w-space + Abyss/Pochven) — 30M..33M;
# NPC-станции — 60M..64M. Структуры игроков и предметы — ~1e12 (различимы только по данным).
_SYSTEM_RANGE = (30_000_000, 33_000_000)
_STATION_RANGE = (60_000_000, 64_000_000)

# Флаги ассетов, которые бывают только у содержимого КОРАБЛЯ: слоты фита и отсеки. (Риги структур
# — корпоративные ассеты, в ассеты персонажа не попадают.) Корень, где лежит только такое, а
# самого корабля в ассетах нет, — «корабль вне ассетов»: ESI отдаёт лишь его модули/риги (так
# бывает, напр., с кораблём, выставленным в контракт). Это не структура: резолвить через ESI
# бессмысленно (403 жжёт error limit), системы у него нет, в склад он не входит.
SHIP_FLAG_PREFIXES = (
    "HiSlot", "MedSlot", "LoSlot", "RigSlot", "SubSystemSlot", "SubSystemBay", "Cargo", "DroneBay",
    "FleetHangar", "ShipHangar", "Specialized", "FighterBay", "FighterTube", "FrigateEscapeBay",
    "BoosterBay", "CorpseBay", "QuafeBay",
)


def is_system_id(loc_id: int) -> bool:
    return _SYSTEM_RANGE[0] <= loc_id < _SYSTEM_RANGE[1]


def is_station_id(loc_id: int) -> bool:
    return _STATION_RANGE[0] <= loc_id < _STATION_RANGE[1]


def is_ship_flag(flag: str | None) -> bool:
    return flag is not None and flag.startswith(SHIP_FLAG_PREFIXES)


@dataclass
class LocationInfo:
    """Описание локации для людей и для группировки склада по системам."""

    location_id: int
    kind: str                 # 'station' | 'structure' | 'system' | 'container' | 'ghost' | 'unknown'
    name: str                 # собственное имя (станция/структура/«Контейнер …»)
    label: str                # полная подпись: «имя · где лежит»
    root_id: int              # верхняя локация (станция/структура/система)
    system_id: int | None = None
    system_name: str | None = None
    region_id: int | None = None
    security: float | None = None
    resolved: bool = True     # False — структура ещё не резолвнута ESI (нет имени/системы)


class LocationResolver:
    """Резолвер локаций одного соединения (кэширует справочники на время жизни объекта).

    ``root_of`` поднимается по цепочке контейнеров/кораблей (``character_assets``: item_id →
    location_id) до станции/структуры/системы. ``describe`` даёт подпись и систему.
    """

    def __init__(self, conn: sqlite3.Connection, cfg: Config | None = None):
        self.conn = conn
        self.cfg = cfg
        self._items: dict[int, tuple[int, int]] | None = None     # item_id -> (location_id, type_id)
        self._child_flags: dict[int, set[str | None]] | None = None  # вне ассетов: loc -> флаги содержимого
        self._structures: dict[int, sqlite3.Row] | None = None
        self._systems: dict[int, tuple[str, int, float]] = {}
        self._stations: dict[int, tuple[str, int] | None] = {}
        self._type_names: dict[int, str] = {}
        self._config_hints = self._build_config_hints(cfg)

    # --------------------------------------------------------------- справочники
    @staticmethod
    def _build_config_hints(cfg: Config | None) -> dict[int, tuple[str, int]]:
        """Подписи и система для структур, известных из конфига: {id: (имя, system_id)}."""
        if cfg is None:
            return {}
        hints: dict[int, tuple[str, int]] = {}
        build = cfg.locations.get("gplb_c")
        market = cfg.locations.get("c_j6mt")
        build_sys = build.system_id if build else 0
        market_sys = market.system_id if market else 0
        build_name = build.name if build else tr("стройка")
        s = cfg.structures
        if s.gplb_engineering_complex_id:
            hints[s.gplb_engineering_complex_id] = (f"{build_name} · Engineering Complex", build_sys)
        if s.gplb_refinery_id:
            hints[s.gplb_refinery_id] = (f"{build_name} · Refinery", build_sys)
        if s.taj_mahgoon_market_id:
            m_name = market.name if market else tr("рынок")
            hints[s.taj_mahgoon_market_id] = (tr("{name} · рынок (структура)", name=m_name), market_sys)
        for f in cfg.facilities:
            if f.location_id and f.location_id not in hints:
                # своя система станции (Facility.system_id), если задана; иначе — место стройки
                hints[f.location_id] = (f.name.strip(), f.system_id or build_sys)
        return hints

    def items(self) -> dict[int, tuple[int, int]]:
        if self._items is None:
            self._items = {
                int(r["item_id"]): (int(r["location_id"] or 0), int(r["type_id"]))
                for r in self.conn.execute("SELECT item_id, location_id, type_id FROM character_assets")
            }
        return self._items

    def _flags_outside_assets(self) -> dict[int, set[str | None]]:
        """Локации, которых нет среди ассетов (станции, структуры, «пропавшие» корабли), →
        флаги предметов, лежащих прямо в них."""
        if self._child_flags is None:
            items = self.items()
            out: dict[int, set[str | None]] = {}
            for r in self.conn.execute(
                "SELECT ca.location_id AS loc, af.location_flag AS flag FROM character_assets ca "
                "LEFT JOIN character_asset_flags af ON af.item_id = ca.item_id "
                "WHERE ca.location_id IS NOT NULL"
            ):
                loc = int(r["loc"])
                if loc not in items:
                    out.setdefault(loc, set()).add(r["flag"])
            self._child_flags = out
        return self._child_flags

    def is_ghost_ship(self, root: int) -> bool:
        """Корень — корабль, которого нет в ассетах (см. ``SHIP_FLAG_PREFIXES``)."""
        if is_system_id(root) or is_station_id(root) or root in self.items():
            return False
        flags = self._flags_outside_assets().get(root)
        return bool(flags) and all(is_ship_flag(f) for f in flags or ())

    def structures(self) -> dict[int, sqlite3.Row]:
        if self._structures is None:
            try:
                rows = self.conn.execute("SELECT * FROM universe_structures").fetchall()
            except sqlite3.OperationalError:  # БД старше схемы v6 — ещё не мигрирована
                rows = []
            self._structures = {int(r["structure_id"]): r for r in rows}
        return self._structures

    def system(self, system_id: int | None) -> tuple[str, int, float] | None:
        if not system_id:
            return None
        if system_id not in self._systems:
            r = self.conn.execute(
                "SELECT name, region_id, security FROM sde_systems WHERE system_id = ?", (system_id,)
            ).fetchone()
            if r is None:
                return None
            self._systems[system_id] = (r["name"], r["region_id"], r["security"])
        return self._systems[system_id]

    def station(self, station_id: int) -> tuple[str, int] | None:
        if station_id not in self._stations:
            r = self.conn.execute(
                "SELECT name, system_id FROM sde_stations WHERE station_id = ?", (station_id,)
            ).fetchone()
            self._stations[station_id] = (r["name"], int(r["system_id"])) if r else None
        return self._stations[station_id]

    def type_name(self, type_id: int) -> str:
        if type_id not in self._type_names:
            r = self.conn.execute("SELECT name FROM sde_types WHERE type_id = ?", (type_id,)).fetchone()
            self._type_names[type_id] = r["name"] if r else f"type#{type_id}"
        return self._type_names[type_id]

    # --------------------------------------------------------------- резолв
    def chain(self, loc_id: int) -> list[int]:
        """Цепочка локаций от ``loc_id`` вверх до корня включительно (защита от циклов)."""
        items = self.items()
        out = [loc_id]
        seen = {loc_id}
        cur = loc_id
        while cur in items:
            parent = items[cur][0]
            if not parent or parent in seen:
                break
            out.append(parent)
            seen.add(parent)
            cur = parent
        return out

    def root_of(self, loc_id: int) -> int:
        return self.chain(loc_id)[-1]

    def system_of(self, loc_id: int) -> tuple[int | None, str]:
        """Система станции/структуры/системы ``loc_id`` — БЕЗ подъёма по контейнерам ассетов
        (дёшево: не грузит ``character_assets``; для станций из конфига этого и не нужно) — и
        откуда она известна: ``'system'`` | ``'station'`` (SDE) | ``'structure'`` (ESI
        ``universe_structures``) | ``'config'`` (лишь подсказка конфига — место стройки/рынка, для
        ещё не резолвленной структуры) | ``'unknown'``."""
        if is_system_id(loc_id):
            return loc_id, "system"
        if is_station_id(loc_id):
            st = self.station(loc_id)
            return (st[1], "station") if st else (None, "unknown")
        us = self.structures().get(loc_id)
        if us is not None and us["status"] == "ok" and us["solar_system_id"]:
            return int(us["solar_system_id"]), "structure"
        hint = self._config_hints.get(loc_id)
        if hint is not None and hint[1]:
            return hint[1], "config"
        return None, "unknown"

    def _root_info(self, root: int) -> LocationInfo:
        if is_system_id(root):
            sysinfo = self.system(root)
            name = sysinfo[0] if sysinfo else tr("система {id}", id=root)
            label = tr("{name} (в космосе)", name=name)
            return LocationInfo(root, "system", label, label, root,
                                root, name, sysinfo[1] if sysinfo else None,
                                sysinfo[2] if sysinfo else None)
        st = self.station(root) if is_station_id(root) else None
        if st is not None:
            sysinfo = self.system(st[1])
            return LocationInfo(root, "station", st[0], st[0], root, st[1],
                                sysinfo[0] if sysinfo else None, sysinfo[1] if sysinfo else None,
                                sysinfo[2] if sysinfo else None)
        us = self.structures().get(root)
        hint = self._config_hints.get(root)
        if us is not None and us["status"] == "ok" and us["name"]:
            sys_id = int(us["solar_system_id"]) if us["solar_system_id"] else None
            sysinfo = self.system(sys_id)
            return LocationInfo(root, "structure", us["name"], us["name"], root, sys_id,
                                sysinfo[0] if sysinfo else None, sysinfo[1] if sysinfo else None,
                                sysinfo[2] if sysinfo else None)
        if hint is not None:
            name, sys_id = hint
            sysinfo = self.system(sys_id)
            return LocationInfo(root, "structure", name, name, root, sys_id or None,
                                sysinfo[0] if sysinfo else None, sysinfo[1] if sysinfo else None,
                                sysinfo[2] if sysinfo else None)
        if self.is_ghost_ship(root):
            name = tr("Корабль вне ассетов {id}", id=root)
            return LocationInfo(root, "ghost", name, name, root, resolved=False)
        if us is not None and us["status"] == "forbidden":
            name = tr("Структура {id} (нет доступа)", id=root)
        else:
            name = tr("Структура {id}", id=root)
        return LocationInfo(root, "unknown", name, name, root, resolved=False)

    def describe(self, loc_id: int) -> LocationInfo:
        """Подпись и система любой локации: станция/структура/система — сами; контейнер или
        корабль — «Тип · где лежит» (система — от корня)."""
        chain = self.chain(loc_id)
        root = chain[-1]
        base = self._root_info(root)
        if loc_id == root:
            return base
        type_id = self.items()[loc_id][1]
        name = self.type_name(type_id)
        return LocationInfo(loc_id, "container", name, f"{name} · {base.name}", root,
                            base.system_id, base.system_name, base.region_id, base.security,
                            base.resolved)


@functools.lru_cache(maxsize=64)
def _expand_cached(conn: sqlite3.Connection, ids: tuple[int, ...], _changes: int) -> frozenset[int]:
    if not ids:
        return frozenset()
    seeds = " UNION ".join("SELECT ?" for _ in ids)
    rows = conn.execute(
        f"WITH RECURSIVE sub(id) AS ({seeds} UNION "
        "SELECT ca.item_id FROM character_assets ca JOIN sub ON ca.location_id = sub.id) "
        "SELECT id FROM sub",
        ids,
    ).fetchall()
    return frozenset(int(r[0]) for r in rows)


def expand_with_containers(conn: sqlite3.Connection | None, location_ids: Iterable[int]) -> frozenset[int]:
    """Выбранные локации + все контейнеры/корабли внутри них (на любой глубине) — чтобы
    «Где искать чертежи» видел и чертежи, лежащие в контейнере в выбранной структуре (при
    строгом сравнении location_id чертежа такие чертежи «пропадали» бы).

    Кэш на соединение (``build_params_from_config`` зовётся на каждый ``estimate_build``);
    ``total_changes`` в ключе — чтобы запись ассетов через ЭТО ЖЕ соединение (тесты, синк)
    не оставляла устаревший результат."""
    ids = tuple(sorted({int(x) for x in location_ids}))
    if conn is None or not ids:
        return frozenset(ids)
    return _expand_cached(conn, ids, conn.total_changes)


def unresolved_structure_ids(conn: sqlite3.Connection) -> list[int]:
    """Корневые локации ассетов/чертежей, похожие на структуры игроков (не станции, не
    системы, не сами предметы), для которых в ``universe_structures`` ещё нет имени —
    кандидаты на резолв через ESI при синке персонажей."""
    resolver = LocationResolver(conn)
    items = resolver.items()
    roots: set[int] = set()
    for loc, _t in items.values():
        if loc:
            roots.add(resolver.root_of(loc))
    for r in conn.execute("SELECT DISTINCT location_id FROM character_blueprints WHERE location_id IS NOT NULL"):
        roots.add(resolver.root_of(int(r["location_id"])))
    known = resolver.structures()
    out = []
    for root in sorted(roots):
        if is_system_id(root) or is_station_id(root) or root in items or resolver.is_ghost_ship(root):
            continue
        rec = known.get(root)
        if rec is not None and rec["status"] == "ok" and rec["name"]:
            continue
        out.append(root)
    return out
