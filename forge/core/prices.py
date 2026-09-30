"""Чтение данных из БД для расчётов core (без сети).

Тонкие хелперы поверх storage: рыночные цены, adjusted price (для EIV), cost index,
объём типа и лучший ME из реально имеющихся чертежей персонажей.
"""

from __future__ import annotations

import sqlite3


def adjusted_price(conn: sqlite3.Connection, type_id: int) -> float | None:
    """CCP adjusted_price — база для EIV (НЕ рыночная цена)."""
    row = conn.execute(
        "SELECT adjusted_price FROM market_adjusted_prices WHERE type_id = ?", (type_id,)
    ).fetchone()
    return row["adjusted_price"] if row else None


def sell_min(conn: sqlite3.Connection, type_id: int, region_id: int) -> float | None:
    """Минимальная цена продажи (по ней ты ПОКУПАЕШЬ материал)."""
    row = conn.execute(
        "SELECT sell_min FROM market_snapshot WHERE type_id = ? AND region_id = ?",
        (type_id, region_id),
    ).fetchone()
    return row["sell_min"] if row and row["sell_min"] else None


def sell(conn: sqlite3.Connection, type_id: int, region_id: int) -> tuple[float | None, int | None]:
    """Цена sell_min и доступный объём sell_volume одним чтением.

    sell_volume — сколько единиц выставлено в sell-ордерах на хабе (сколько можно купить).
    Возвращает (None, None), если нет sell-цены.
    """
    row = conn.execute(
        "SELECT sell_min, sell_volume FROM market_snapshot WHERE type_id = ? AND region_id = ?",
        (type_id, region_id),
    ).fetchone()
    if not row or not row["sell_min"]:
        return None, None
    return row["sell_min"], row["sell_volume"]


def buy_max(conn: sqlite3.Connection, type_id: int, region_id: int) -> float | None:
    """Максимальная цена покупки (по ней ты мгновенно ПРОДАЁШЬ продукт)."""
    row = conn.execute(
        "SELECT buy_max FROM market_snapshot WHERE type_id = ? AND region_id = ?",
        (type_id, region_id),
    ).fetchone()
    return row["buy_max"] if row and row["buy_max"] else None


def sale_price(conn: sqlite3.Connection, type_id: int, region_id: int, mode: str = "sell_min") -> float | None:
    """Цена, по которой оцениваем ПРОДАЖУ продукта (настройка ``[market] sell_price``):
    ``sell_min`` — выставить sell-ордер по лучшей цене (по умолчанию);
    ``buy_max`` — продать сразу в лучший buy-ордер."""
    if mode == "buy_max":
        return buy_max(conn, type_id, region_id)
    return sell_min(conn, type_id, region_id)


def cost_index(conn: sqlite3.Connection, system_id: int, activity_id: int) -> float:
    """System cost index по активности; 0.0 если нет данных."""
    row = conn.execute(
        "SELECT cost_index FROM system_cost_indices WHERE system_id = ? AND activity_id = ?",
        (system_id, activity_id),
    ).fetchone()
    return row["cost_index"] if row else 0.0


SHIP_CATEGORY = 6

# Упакованные объёмы по классу корпуса (м³). Дамп Fuzzwork хранит у кораблей объём в
# СОБРАННОМ виде (invTypes.volume), а invVolumes пуст — поэтому для логистики берём
# стандартные упакованные значения CCP по группе. Не-корабли уже «упакованы» (volume верен).
PACKAGED_BY_SHIP_GROUP = {
    # фрегат-класс
    "Frigate": 2500, "Assault Frigate": 2500, "Covert Ops": 2500, "Interceptor": 2500,
    "Stealth Bomber": 2500, "Electronic Attack Ship": 2500, "Logistics Frigate": 2500,
    "Expedition Frigate": 2500, "Corvette": 2500, "Shuttle": 500,
    # эсминец-класс
    "Destroyer": 5000, "Interdictor": 5000, "Command Destroyer": 5000, "Tactical Destroyer": 5000,
    # крейсер-класс
    "Cruiser": 10000, "Heavy Assault Cruiser": 10000, "Heavy Interdiction Cruiser": 10000,
    "Logistics": 10000, "Force Recon Ship": 10000, "Combat Recon Ship": 10000,
    "Strategic Cruiser": 10000, "Flag Cruiser": 10000,
    # линейный крейсер-класс
    "Combat Battlecruiser": 15000, "Attack Battlecruiser": 15000, "Command Ship": 15000,
    # линкор-класс
    "Battleship": 50000, "Black Ops": 50000, "Marauder": 50000,
    # добыча
    "Mining Barge": 3750, "Exhumer": 3750,
    # грузовики / транспорт
    "Industrial": 20000, "Blockade Runner": 20000, "Deep Space Transport": 50000,
    # фрейтеры (самоходом/гейтами; капиталы уходят прыжком — фрахт по топливу)
    "Freighter": 1300000, "Jump Freighter": 1300000, "Capital Industrial Ship": 1300000,
}


def volume(conn: sqlite3.Connection, type_id: int) -> float:
    """Объём для логистики (м³): упакованный.

    Приоритет: явный packaged_volume → стандартный упакованный объём по группе корабля →
    volume (для не-кораблей это и есть упакованный; для неизвестных групп кораблей —
    собранный объём, приблизительно завышает фрахт).
    """
    row = conn.execute(
        "SELECT t.volume, t.packaged_volume, t.category_id, g.name AS group_name "
        "FROM sde_types t LEFT JOIN sde_groups g ON g.group_id = t.group_id "
        "WHERE t.type_id = ?",
        (type_id,),
    ).fetchone()
    if not row:
        return 0.0
    if row["packaged_volume"] is not None:
        return row["packaged_volume"]
    if row["category_id"] == SHIP_CATEGORY:
        packaged = PACKAGED_BY_SHIP_GROUP.get(row["group_name"])
        if packaged is not None:
            return float(packaged)
    return row["volume"] or 0.0


def group_id(conn: sqlite3.Connection, type_id: int) -> int | None:
    """group_id предмета (для маршрутизации джоба по станции)."""
    row = conn.execute("SELECT group_id FROM sde_types WHERE type_id = ?", (type_id,)).fetchone()
    return int(row["group_id"]) if row and row["group_id"] is not None else None


def category_id(conn: sqlite3.Connection, type_id: int) -> int | None:
    """category_id предмета (для маршрутизации джоба по станции — грубее group_id: напр.
    «Ship» category=6 разом покрывает Frigate/Cruiser/Battleship/… группы, удобно для ригов
    Engineering Complex, которые в EVE даются по КАТЕГОРИИ продукта, не по группе — см.
    Standup XL-Set Ship/Equipment and Consumable/Structure and Component Manufacturing
    Efficiency, каждый действует ТОЛЬКО на свою категорию, а не на всё производство разом)."""
    row = conn.execute("SELECT category_id FROM sde_types WHERE type_id = ?", (type_id,)).fetchone()
    return int(row["category_id"]) if row and row["category_id"] is not None else None


def type_name(conn: sqlite3.Connection, type_id: int) -> str:
    row = conn.execute("SELECT name FROM sde_types WHERE type_id = ?", (type_id,)).fetchone()
    return row["name"] if row else f"type#{type_id}"


def max_production_limit(conn: sqlite3.Connection, blueprint_type_id: int) -> int | None:
    """Максимум прогонов в ОДНОМ джобе для этого чертежа — реальное ограничение EVE
    (``industryBlueprints.maxProductionLimit`` из SDE), не эвристика. У кораблей — единицы,
    у боеприпасов — сотни тысяч; используется, чтобы не предлагать нереализуемо большие партии."""
    row = conn.execute(
        "SELECT max_production_limit FROM sde_blueprints WHERE blueprint_type_id = ?",
        (blueprint_type_id,),
    ).fetchone()
    return int(row["max_production_limit"]) if row and row["max_production_limit"] else None


def resolve_type_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute(
        "SELECT type_id FROM sde_types WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return int(row["type_id"]) if row else None


def loc_in_clause(location_ids) -> tuple[str, list[int]]:
    """Фрагмент ``AND location_id IN (…)`` и его параметры. Пусто → ('', []) (искать везде)."""
    ids = [int(x) for x in (location_ids or [])]
    if not ids:
        return "", []
    return " AND location_id IN ({})".format(",".join("?" * len(ids))), ids


def best_owned_me(
    conn: sqlite3.Connection, blueprint_type_id: int, location_ids=None
) -> int | None:
    """Лучший (максимальный) ME среди принадлежащих персонажам копий этого чертежа.

    ``location_ids`` (если задан) ограничивает поиск чертежами в этих локациях.
    """
    clause, loc_params = loc_in_clause(location_ids)
    row = conn.execute(
        "SELECT MAX(me) AS me FROM character_blueprints WHERE type_id = ?" + clause,
        (blueprint_type_id, *loc_params),
    ).fetchone()
    return int(row["me"]) if row and row["me"] is not None else None
