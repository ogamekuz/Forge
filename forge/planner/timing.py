"""Длительность джоба (без сети).

Время = базовое (SDE) × runs × (1 − TE/100) × множители скиллов и структуры.
- Industry (4%/ур.) — ТОЛЬКО производство (описание скилла: "reduction in manufacturing time").
- Advanced Industry (3%/ур.) — ТОЛЬКО production/invention/copy ("all manufacturing and
  research times" — реакции сюда НЕ входят, подтверждено описанием скилла в SDE).
- Reactions (4%/ур.) — ТОЛЬКО реакции (свой скилл, dogma-атрибут reactionTimeBonus=-4.0/ур.,
  описание "4% reduction in reaction time per skill level"; аналог Industry, но для реакций).
- «Специализированные» скиллы (Electronic Engineering, Nanite Engineering, Graviton Physics
  и ещё ~23 подобных, dogma-атрибут manufactureTimePerLevel, обычно -1%/ур.) — ТОЛЬКО для
  конкретных чертежей, которые их ТРЕБУЮТ (sde_blueprint_skills), и ТОЛЬКО производство —
  см. ``specialization_mult()``. В отличие от Industry/Advanced Industry/Reactions, это не
  один универсальный скилл, а любое подмножество из ~26 — зависит от чертежа.
- Science (5%/ур.) — ТОЛЬКО копирование чертежей (описание скилла: "5% reduction in blueprint
  copying time per level"); время копи-джоба = база SDE (activity 5, на 1 прогон 1 копии) ×
  копий × прогонов на копию × скиллы × станция роли ``copy``.
- TE — ТОЛЬКО производство: реакции TE не имеют; у инвенты нет своей инвентируемой копии (ME/TE
  итоговой BPC задаёт декриптор/база, а не TE входного T1-чертежа); копирование TE исходника не
  ускоряет (TE — исследование времени ПРОИЗВОДСТВА).
"""

from __future__ import annotations

import sqlite3

from ..core.bom import MANUFACTURING, REACTION
from ..core.cost import COPYING

INDUSTRY_PER_LEVEL = 0.04
ADV_INDUSTRY_PER_LEVEL = 0.03
REACTIONS_PER_LEVEL = 0.04
SCIENCE_COPY_PER_LEVEL = 0.05
MANUFACTURE_TIME_PER_LEVEL_ATTR = 1982  # manufactureTimePerLevel — на самом скилле


def base_time_per_run(conn: sqlite3.Connection, blueprint_type_id: int, activity_id: int) -> int:
    row = conn.execute(
        "SELECT time_seconds FROM sde_blueprint_activities "
        "WHERE blueprint_type_id = ? AND activity_id = ?",
        (blueprint_type_id, activity_id),
    ).fetchone()
    return int(row["time_seconds"]) if row and row["time_seconds"] is not None else 0


def best_owned_te(
    conn: sqlite3.Connection, character_id: int, blueprint_type_id: int, location_ids=None
) -> int:
    """Лучший (макс) TE среди копий этого чертежа у персонажа; 0 если нет.

    ``location_ids`` (если задан) ограничивает поиск чертежами в этих локациях.
    """
    from ..core.prices import loc_in_clause
    clause, loc_params = loc_in_clause(location_ids)
    row = conn.execute(
        "SELECT MAX(te) AS te FROM character_blueprints WHERE character_id = ? AND type_id = ?"
        + clause,
        (character_id, blueprint_type_id, *loc_params),
    ).fetchone()
    return int(row["te"]) if row and row["te"] is not None else 0


def specialization_mult(
    conn: sqlite3.Connection, character_id: int, blueprint_type_id: int, activity_id: int
) -> float:
    """Множитель от «специализированных» скиллов сокращения времени производства (Electronic
    Engineering и т.п.) — ТОЛЬКО для тех скиллов, которые ТРЕБУЕТ этот конкретный чертёж
    (sde_blueprint_skills), и ТОЛЬКО для производства. Каждый такой скилл даёт свой %/уровень
    (dogma-атрибут manufactureTimePerLevel, обычно -1%, у Mutagenic Stabilization -2%) —
    независимая группа стэкинга, комбинируется с остальными факторами перемножением. Нет
    прокачанного уровня (0) → эффекта нет, но множитель всё равно валиден."""
    if activity_id != MANUFACTURING:
        return 1.0
    rows = conn.execute(
        "SELECT da.value_float AS pct, COALESCE(cs.active_level, 0) AS lvl "
        "FROM sde_blueprint_skills bs "
        "JOIN sde_dogma_type_attributes da "
        "  ON da.type_id = bs.skill_type_id AND da.attribute_id = ? "
        "LEFT JOIN character_skills cs "
        "  ON cs.character_id = ? AND cs.skill_type_id = bs.skill_type_id "
        "WHERE bs.blueprint_type_id = ? AND bs.activity_id = ?",
        (MANUFACTURE_TIME_PER_LEVEL_ATTR, character_id, blueprint_type_id, activity_id),
    ).fetchall()
    mult = 1.0
    for r in rows:
        mult *= 1.0 + (r["pct"] / 100.0) * r["lvl"]
    return mult


def job_seconds(
    base_per_run: int,
    runs: int,
    activity_id: int,
    te: int,
    industry_level: int,
    adv_industry_level: int,
    time_mult: float = 1.0,
    reactions_level: int = 0,
    specialization: float = 1.0,
    science_level: int = 0,
) -> float:
    """Длительность одного джоба в секундах. Для копирования ``runs`` — копий × прогонов на
    копию (см. ``planner.schedule``), ``science_level`` — уровень Science (−5%/ур.)."""
    # TE — только производство (реакции/инвента/копирование — без TE, см. докстринг модуля).
    te_factor = (1.0 - te / 100.0) if activity_id == MANUFACTURING else 1.0
    # Industry — ТОЛЬКО производство (описание скилла: "reduction in manufacturing time"), а
    # НЕ «всё кроме реакций»: к invention/copy/research не применяется.
    industry_factor = (
        1.0 - INDUSTRY_PER_LEVEL * industry_level if activity_id == MANUFACTURING else 1.0
    )
    # Advanced Industry — НЕ для реакций (описание скилла: "all manufacturing and research
    # times", реакции отдельная активность); для реакций своя пара — Reactions.
    adv_factor = (
        1.0 if activity_id == REACTION else (1.0 - ADV_INDUSTRY_PER_LEVEL * adv_industry_level)
    )
    reactions_factor = (
        1.0 - REACTIONS_PER_LEVEL * reactions_level if activity_id == REACTION else 1.0
    )
    science_factor = (
        1.0 - SCIENCE_COPY_PER_LEVEL * science_level if activity_id == COPYING else 1.0
    )
    return (
        base_per_run * runs * te_factor * industry_factor * adv_factor
        * reactions_factor * science_factor * specialization * time_mult
    )
