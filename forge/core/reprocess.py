"""Переработка (reprocessing) как альтернативный источник материала.

Отдельная от индустрии механика EVE — не описывается ``industryActivity*`` (чертежи), а
собственной таблицей ``invTypeMaterials`` (выход при 100% эффективности на 1 ``portionSize``
перерабатываемого предмета). ``core/sourcing.py`` опционально сравнивает этот путь с прямой
постройкой/покупкой материала, если ``params.reprocessing_efficiency > 0`` (по умолчанию 0 —
выключено; игрок должен явно включить и вписать своё реальное число: эффективность зависит от
скиллов/ригов/структуры/налога станции, которые Forge не моделирует отдельно для переработки).

Этот модуль — только чистые функции (SQL-чтение + арифметика выхода), без знания о
buy/build-решениях (``core/sourcing.py`` — единственный источник соответствующей логики, не
дублируем здесь, чтобы не завести цикл импортов sourcing↔reprocess).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from . import prices


@dataclass(frozen=True)
class ReprocessYield:
    material_type_id: int
    name: str
    quantity: int  # выход на 1 portion_size прекурсора при 100% эффективности


@dataclass(frozen=True)
class ReprocessSource:
    """Один способ получить целевой материал: переработать ``source_type_id``."""

    source_type_id: int
    source_name: str
    portion_size: int
    main: ReprocessYield                     # выход, который нам нужен (целевой материал)
    byproducts: tuple[ReprocessYield, ...]    # прочие выходы той же переработки


def reprocessing_sources(conn: sqlite3.Connection, target_type_id: int) -> list[ReprocessSource]:
    """Все известные способы получить ``target_type_id`` переработкой чего-то другого (может
    быть несколько прекурсоров — вызывающий сам выбирает по цене)."""
    source_ids = [
        r["type_id"]
        for r in conn.execute(
            "SELECT DISTINCT type_id FROM sde_reprocessing_materials WHERE material_type_id = ?",
            (target_type_id,),
        ).fetchall()
    ]
    out: list[ReprocessSource] = []
    for sid in source_ids:
        rows = conn.execute(
            "SELECT material_type_id, quantity, portion_size FROM sde_reprocessing_materials "
            "WHERE type_id = ?",
            (sid,),
        ).fetchall()
        if not rows:
            continue
        main: ReprocessYield | None = None
        byprod: list[ReprocessYield] = []
        for r in rows:
            y = ReprocessYield(
                r["material_type_id"], prices.type_name(conn, r["material_type_id"]), r["quantity"]
            )
            if r["material_type_id"] == target_type_id:
                main = y
            else:
                byprod.append(y)
        if main is None or main.quantity <= 0:
            continue  # цель почему-то не входит в собственный список выходов — защита от мусора
        portion_size = rows[0]["portion_size"] or 1
        out.append(ReprocessSource(sid, prices.type_name(conn, sid), portion_size, main, tuple(byprod)))
    return out


def portions_needed(target_quantity: int, yield_per_portion: int, efficiency: float) -> int:
    """Минимум «порций» прекурсора, чтобы переработка дала ХОТЯ БЫ ``target_quantity`` целевого
    материала.

    Реальная формула EVE: ``floor(portions × yield_per_portion × efficiency)`` — округление ОДИН
    раз на итоговое количество, не на каждую порцию отдельно (в отличие от ME-округления
    чертежей, см. правило 4 docs/ARCHITECTURE.md — это другая механика, не путать)."""
    if yield_per_portion <= 0 or efficiency <= 0 or target_quantity <= 0:
        return 0
    portions = max(1, math.ceil(target_quantity / (yield_per_portion * efficiency)))
    # Защита от пограничной погрешности float у ceil (тот же класс проблемы, что и в
    # cost.material_quantity — см. round(...,2) там); здесь достаточно точечной коррекции.
    while portions > 1 and math.floor((portions - 1) * yield_per_portion * efficiency) >= target_quantity:
        portions -= 1
    while math.floor(portions * yield_per_portion * efficiency) < target_quantity:
        portions += 1
    return portions


def reprocess_output(
    src: ReprocessSource, num_portions: int, efficiency: float
) -> tuple[int, list[tuple[ReprocessYield, int]]]:
    """(факт. кол-во целевого материала, [(побочный продукт, кол-во), ...]) от переработки
    ``num_portions`` порций ``src.source_type_id`` при данной эффективности."""
    main_qty = math.floor(num_portions * src.main.quantity * efficiency)
    byproducts = [(y, math.floor(num_portions * y.quantity * efficiency)) for y in src.byproducts]
    return main_qty, byproducts
