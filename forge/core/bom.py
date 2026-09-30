"""Разрешение графа постройки из SDE.

Граф не хранится отдельно — выводится из sde_blueprint_products (что чем производится)
и sde_blueprint_materials (что нужно на 1 run). Рекурсию make-or-buy делает sourcing/cost;
здесь — только «плоские» запросы к одному уровню.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

MANUFACTURING = 1
REACTION = 11
BUILDABLE_ACTIVITIES = (MANUFACTURING, REACTION)


@dataclass
class Blueprint:
    blueprint_type_id: int
    activity_id: int
    product_type_id: int
    product_qty_per_run: int  # сколько единиц продукта даёт 1 run (у реакций > 1)


@dataclass
class Material:
    type_id: int
    base_quantity: int  # на 1 run, до ME


def blueprint_for_product(
    conn: sqlite3.Connection,
    product_type_id: int,
    activities: tuple[int, ...] = BUILDABLE_ACTIVITIES,
) -> Blueprint | None:
    """Найти чертёж/реакцию, производящие данный тип. Производство приоритетнее реакции."""
    placeholders = ", ".join("?" for _ in activities)
    row = conn.execute(
        f"""
        SELECT blueprint_type_id, activity_id, quantity
        FROM sde_blueprint_products
        WHERE product_type_id = ? AND activity_id IN ({placeholders})
        ORDER BY CASE activity_id WHEN {MANUFACTURING} THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (product_type_id, *activities),
    ).fetchone()
    if not row:
        return None
    return Blueprint(
        blueprint_type_id=row["blueprint_type_id"],
        activity_id=row["activity_id"],
        product_type_id=product_type_id,
        product_qty_per_run=row["quantity"] or 1,
    )


def materials(conn: sqlite3.Connection, blueprint_type_id: int, activity_id: int) -> list[Material]:
    """Входные материалы на 1 run (до ME)."""
    rows = conn.execute(
        """
        SELECT material_type_id, quantity FROM sde_blueprint_materials
        WHERE blueprint_type_id = ? AND activity_id = ?
        ORDER BY material_type_id
        """,
        (blueprint_type_id, activity_id),
    ).fetchall()
    return [Material(type_id=r["material_type_id"], base_quantity=r["quantity"]) for r in rows]
