"""Имена и системы структур игроков (Upwell) — авторизованный ESI ``/universe/structures/{id}/``.

В SDE структур игроков нет, а склад и чертежи лежат в основном в них (Engineering Complex,
Refinery, Keepstar рынка…). Без имени и системы их нельзя выбрать «по системе» во вкладке
«Склад» и нельзя понятно подписать. Эндпоинт требует скоуп ``esi-universe.read_structures.v1``
и доступ к докингу у того чара, чьим токеном спрашиваем — поэтому сначала пробуем тех, у кого
в этой структуре лежат ассеты/чертежи (они там точно докались), потом остальных.

Бережём error limit ESI: 403 (нет доступа) тоже считается ошибкой, поэтому на одну структуру
пробуем не больше ``MAX_TRIES`` токенов, а структуру без доступа ни у кого перепроверяем не
чаще раза в ``RETRY_FORBIDDEN_DAYS`` дней.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ...storage import repositories as repo
from ...storage.db import transaction
from ..esi import EsiClient

STRUCTURE_SCOPE = "esi-universe.read_structures.v1"
MAX_TRIES = 3
RETRY_FORBIDDEN_DAYS = 7

# Диапазоны id EVE и флаги содержимого корабля (та же логика, что core.locations — ingest не
# импортирует core, слои).
_SYSTEM_RANGE = (30_000_000, 33_000_000)
_STATION_RANGE = (60_000_000, 64_000_000)
_SHIP_FLAG_PREFIXES = (
    "HiSlot", "MedSlot", "LoSlot", "RigSlot", "SubSystemSlot", "SubSystemBay", "Cargo", "DroneBay",
    "FleetHangar", "ShipHangar", "Specialized", "FighterBay", "FighterTube", "FrigateEscapeBay",
    "BoosterBay", "CorpseBay", "QuafeBay",
)


def _items(conn: sqlite3.Connection) -> dict[int, int]:
    return {int(r["item_id"]): int(r["location_id"] or 0)
            for r in conn.execute("SELECT item_id, location_id FROM character_assets")}


def ghost_ships(conn: sqlite3.Connection, items: dict[int, int] | None = None) -> set[int]:
    """Локации вне ассетов, где лежит только содержимое корабля (слоты фита, отсеки), — корабли,
    которых нет в ассетах (ESI отдаёт лишь их модули/риги). Это не структуры: спрашивать их у
    /universe/structures/ бессмысленно — только 403, которые жгут error limit ESI."""
    items = _items(conn) if items is None else items
    flags: dict[int, set[str | None]] = {}
    for r in conn.execute(
        "SELECT ca.location_id AS loc, af.location_flag AS flag FROM character_assets ca "
        "LEFT JOIN character_asset_flags af ON af.item_id = ca.item_id WHERE ca.location_id IS NOT NULL"
    ):
        loc = int(r["loc"])
        if loc not in items:
            flags.setdefault(loc, set()).add(r["flag"])
    return {loc for loc, fs in flags.items()
            if all(f is not None and f.startswith(_SHIP_FLAG_PREFIXES) for f in fs)}


def _root(items: dict[int, int], loc: int) -> int:
    seen = {loc}
    while loc in items:
        parent = items[loc]
        if not parent or parent in seen:
            break
        seen.add(parent)
        loc = parent
    return loc


def candidate_structure_ids(conn: sqlite3.Connection) -> list[int]:
    """Корневые локации ассетов/чертежей, похожие на структуры игроков (не станции, не
    системы, не сами предметы, не корабли вне ассетов) — кандидаты на резолв через ESI."""
    items = _items(conn)
    roots: set[int] = set()
    for loc in items.values():
        if loc:
            roots.add(_root(items, loc))
    for r in conn.execute("SELECT DISTINCT location_id FROM character_blueprints WHERE location_id IS NOT NULL"):
        roots.add(_root(items, int(r["location_id"])))
    ghosts = ghost_ships(conn, items)
    return sorted(
        r for r in roots
        if not (_SYSTEM_RANGE[0] <= r < _SYSTEM_RANGE[1] or _STATION_RANGE[0] <= r < _STATION_RANGE[1]
                or r in items or r in ghosts)
    )


def parse_structure(structure_id: int, data: dict[str, Any], now: str) -> dict:
    """Ответ ESI → строка universe_structures."""
    return {
        "structure_id": structure_id,
        "name": data.get("name"),
        "solar_system_id": data.get("solar_system_id"),
        "type_id": data.get("type_id"),
        "owner_id": data.get("owner_id"),
        "status": "ok",
        "updated_at": now,
    }


def owners_by_root(conn: sqlite3.Connection) -> dict[int, list[int]]:
    """root_id → персонажи, у которых там что-то лежит (самые вероятные с доступом)."""
    items = _items(conn)
    out: dict[int, dict[int, int]] = {}
    for r in conn.execute("SELECT character_id, location_id FROM character_assets WHERE location_id IS NOT NULL"):
        root = _root(items, int(r["location_id"]))
        bucket = out.setdefault(root, {})
        cid = int(r["character_id"])
        bucket[cid] = bucket.get(cid, 0) + 1
    for r in conn.execute("SELECT character_id, location_id FROM character_blueprints WHERE location_id IS NOT NULL"):
        root = _root(items, int(r["location_id"]))
        bucket = out.setdefault(root, {})
        cid = int(r["character_id"])
        bucket[cid] = bucket.get(cid, 0) + 1
    return {root: [c for c, _n in sorted(b.items(), key=lambda kv: -kv[1])] for root, b in out.items()}


def _recently_forbidden(conn: sqlite3.Connection, structure_id: int, now: datetime) -> bool:
    row = conn.execute(
        "SELECT status, updated_at FROM universe_structures WHERE structure_id = ?", (structure_id,)
    ).fetchone()
    if row is None or row["status"] != "forbidden" or not row["updated_at"]:
        return False
    try:
        ts = datetime.fromisoformat(row["updated_at"])
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return now - ts < timedelta(days=RETRY_FORBIDDEN_DAYS)


def sync_structures(
    conn: sqlite3.Connection,
    esi: EsiClient,
    tokens: dict[int, str],
    extra_ids: list[int] | None = None,
) -> dict[str, int]:
    """Резолвнуть неизвестные структуры, где лежат ассеты/чертежи чаров (и ``extra_ids`` —
    структуры из конфига). ``tokens`` — access-токены чаров со скоупом структур. Счётчики:
    resolved / forbidden / errors / skipped."""
    counts = {"resolved": 0, "forbidden": 0, "errors": 0, "skipped": 0}
    if not tokens:
        return counts
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    todo = list(dict.fromkeys(candidate_structure_ids(conn) + [int(x) for x in (extra_ids or []) if x]))
    known_ok = {
        int(r["structure_id"]) for r in conn.execute(
            "SELECT structure_id FROM universe_structures WHERE status = 'ok' AND name IS NOT NULL")
    }
    owners = owners_by_root(conn)
    rows: list[dict] = []
    for sid in todo:
        if sid in known_ok:
            continue
        if _recently_forbidden(conn, sid, now):
            counts["skipped"] += 1
            continue
        order = [c for c in owners.get(sid, []) if c in tokens]
        order += [c for c in tokens if c not in order]
        status = "forbidden"
        for cid in order[:MAX_TRIES]:
            try:
                resp = esi.get(f"/universe/structures/{sid}/", token=tokens[cid])
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                if code in (401, 403, 404):
                    continue  # у этого чара нет доступа (или структуры уже нет) — следующий
                status = "error"
                break
            except httpx.HTTPError:
                status = "error"
                break
            rows.append(parse_structure(sid, resp.data or {}, now_iso))
            status = "ok"
            break
        if status == "ok":
            counts["resolved"] += 1
        else:
            counts["forbidden" if status == "forbidden" else "errors"] += 1
            rows.append({"structure_id": sid, "name": None, "solar_system_id": None,
                         "type_id": None, "owner_id": None, "status": status,
                         "updated_at": now_iso})
    if rows:
        with transaction(conn):
            repo.universe_structures(conn).upsert_many(rows)
    return counts
