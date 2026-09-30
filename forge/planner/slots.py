"""Производственные слоты персонажа из скиллов (без сети).

Слоты = 1 базовый + уровни соответствующих скиллов. Пулы независимы: производство,
реакции, наука могут идти одновременно.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Скиллы, дающие доп. слоты (резолвятся в type_id по имени — устойчиво к смене id).
SLOT_SKILLS = {
    "manufacturing": ["Mass Production", "Advanced Mass Production"],
    "reaction": ["Mass Reactions", "Advanced Mass Reactions"],
    "science": ["Laboratory Operation", "Advanced Laboratory Operation"],
}

# Скиллы сокращения времени. Industry — только производство; Advanced Industry — только
# production/invention/copy (НЕ реакции, см. описание скилла в SDE); Reactions — только реакции
# (аналог Industry, свой отдельный скилл, см. forge/planner/timing.py).
TIME_SKILLS = ["Industry", "Advanced Industry", "Reactions"]


def _skill_id(conn: sqlite3.Connection, name: str, _cache: dict[str, int] = {}) -> int | None:  # noqa: B006 — deliberate cross-call memoization (skill name -> type_id is static SDE data)
    if name in _cache:
        return _cache[name]
    row = conn.execute(
        "SELECT type_id FROM sde_types WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row is None:
        return None  # не кэшируем промахи — id появится после загрузки SDE
    _cache[name] = int(row["type_id"])
    return _cache[name]


def skill_level(conn: sqlite3.Connection, character_id: int, skill_name: str) -> int:
    sid = _skill_id(conn, skill_name)
    if sid is None:
        return 0
    row = conn.execute(
        "SELECT active_level FROM character_skills WHERE character_id = ? AND skill_type_id = ?",
        (character_id, sid),
    ).fetchone()
    return int(row["active_level"]) if row and row["active_level"] is not None else 0


@dataclass
class SlotProfile:
    character_id: int
    name: str
    manufacturing: int
    reaction: int
    science: int

    def slots_for_activity(self, activity_id: int) -> int:
        return self.reaction if activity_id == 11 else self.manufacturing


def slots_for(conn: sqlite3.Connection, character_id: int, name: str = "") -> SlotProfile:
    """Слоты персонажа по пулам (1 базовый + уровни скиллов)."""
    counts = {}
    for pool, skills in SLOT_SKILLS.items():
        counts[pool] = 1 + sum(skill_level(conn, character_id, s) for s in skills)
    return SlotProfile(
        character_id=character_id,
        name=name,
        manufacturing=counts["manufacturing"],
        reaction=counts["reaction"],
        science=counts["science"],
    )


def all_profiles(conn: sqlite3.Connection) -> list[SlotProfile]:
    """Профили слотов по всем персонажам в БД."""
    rows = conn.execute("SELECT character_id, name FROM characters ORDER BY name").fetchall()
    return [slots_for(conn, int(r["character_id"]), r["name"]) for r in rows]
