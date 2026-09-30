"""Репозитории — единственный способ читать/писать доменные таблицы.

Публичный API намеренно портируемый: ``INSERT ... ON CONFLICT(pk) DO UPDATE`` валиден
и в SQLite, и в Postgres, поэтому upsert идемпотентен на обеих СУБД (правило 3 — без дублей).
Никакой SQLite-специфики наружу не торчит.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def upsert(
    conn: sqlite3.Connection,
    table: str,
    rows: Iterable[Mapping[str, Any]],
    pk_cols: Sequence[str],
) -> int:
    """Вставить или обновить строки по первичному ключу. Возвращает число строк.

    Все строки должны иметь одинаковый набор ключей. Пустой вход → 0.
    """
    rows = list(rows)
    if not rows:
        return 0

    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(columns)

    update_cols = [c for c in columns if c not in pk_cols]
    if update_cols:
        set_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
        conflict = (
            f"ON CONFLICT({', '.join(pk_cols)}) DO UPDATE SET {set_clause}"
        )
    else:
        # Только PK-колонки → нечего обновлять, просто игнорируем дубль.
        conflict = f"ON CONFLICT({', '.join(pk_cols)}) DO NOTHING"

    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) {conflict}"
    params = [tuple(row[c] for c in columns) for row in rows]
    conn.executemany(sql, params)
    return len(params)


def count(conn: sqlite3.Connection, table: str) -> int:
    """Число строк в таблице."""
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])


class Repository:
    """Тонкая обёртка над одной таблицей: upsert + чтение."""

    def __init__(self, conn: sqlite3.Connection, table: str, pk_cols: Sequence[str]):
        self.conn = conn
        self.table = table
        self.pk_cols = list(pk_cols)

    def upsert_many(self, rows: Iterable[Mapping[str, Any]]) -> int:
        return upsert(self.conn, self.table, rows, self.pk_cols)

    def count(self) -> int:
        return count(self.conn, self.table)

    def all(self) -> list[sqlite3.Row]:
        return self.conn.execute(f"SELECT * FROM {self.table}").fetchall()

    def get(self, **pk: Any) -> sqlite3.Row | None:
        where = " AND ".join(f"{c}=?" for c in pk)
        params = tuple(pk.values())
        return self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {where}", params
        ).fetchone()


# ---------------------------------------------------------------------------
# Фабрики репозиториев по доменным таблицам (фиксируют первичные ключи).
# ---------------------------------------------------------------------------
def sde_categories(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "sde_categories", ["category_id"])


def sde_groups(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "sde_groups", ["group_id"])


def sde_types(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "sde_types", ["type_id"])


def sde_systems(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "sde_systems", ["system_id"])


def sde_blueprints(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "sde_blueprints", ["blueprint_type_id"])


def sde_stations(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "sde_stations", ["station_id"])


def sde_reprocessing_materials(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "sde_reprocessing_materials", ["type_id", "material_type_id"])


def sde_blueprint_activities(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "sde_blueprint_activities", ["blueprint_type_id", "activity_id"])


def sde_blueprint_materials(conn: sqlite3.Connection) -> Repository:
    return Repository(
        conn, "sde_blueprint_materials", ["blueprint_type_id", "activity_id", "material_type_id"]
    )


def sde_blueprint_products(conn: sqlite3.Connection) -> Repository:
    return Repository(
        conn, "sde_blueprint_products", ["blueprint_type_id", "activity_id", "product_type_id"]
    )


def sde_blueprint_skills(conn: sqlite3.Connection) -> Repository:
    return Repository(
        conn, "sde_blueprint_skills", ["blueprint_type_id", "activity_id", "skill_type_id"]
    )


def market_history(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "market_history", ["type_id", "region_id", "day"])


def market_snapshot(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "market_snapshot", ["type_id", "region_id"])


def market_adjusted_prices(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "market_adjusted_prices", ["type_id"])


def system_cost_indices(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "system_cost_indices", ["system_id", "activity_id"])


# --- Character (часть B) ----------------------------------------------------
def characters(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "characters", ["character_id"])


def character_skills(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "character_skills", ["character_id", "skill_type_id"])


def character_blueprints(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "character_blueprints", ["item_id"])


def character_assets(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "character_assets", ["item_id"])


def character_asset_flags(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "character_asset_flags", ["item_id"])


def character_industry_jobs(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "character_industry_jobs", ["job_id"])


def universe_structures(conn: sqlite3.Connection) -> Repository:
    return Repository(conn, "universe_structures", ["structure_id"])


def replace_character_children(
    conn: sqlite3.Connection, table: str, character_id: int, rows: list[dict]
) -> int:
    """Полная замена строк персонажа в таблице (для skills/blueprints/assets/jobs).

    Удаляет прежние строки этого character_id и вставляет новые — иначе проданные/
    перемещённые предметы остались бы «призраками» при простом upsert.

    Вставка идёт ``INSERT OR REPLACE`` (правило 3 — идемпотентность): PK у blueprints/assets
    глобальный (``item_id``), а удаление — только по своему ``character_id``. Когда предмет
    переезжает между персонажами игрока (частое при инвенте/копировании/перевозке), строка
    нового владельца замещает прежнюю, а не падает с ``UNIQUE constraint failed``. Это же
    гасит дубликаты ``item_id`` внутри одного ответа ESI (перекрытие страниц на «живом» ангаре).
    """
    conn.execute(f"DELETE FROM {table} WHERE character_id = ?", (character_id,))
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)


# --- Резолв имён → id (для конфига) ----------------------------------------
def resolve_system_id(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute(
        "SELECT system_id FROM sde_systems WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return int(row["system_id"]) if row else None


def resolve_region_id_by_system(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute(
        "SELECT region_id FROM sde_systems WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return int(row["region_id"]) if row else None
