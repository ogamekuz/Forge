"""Бонусы фитованных ригов/сервис-модулей структуры — материал/время/стоимость из SDE dogma
(без сети).

Заменяет ручной ввод material_bonus_pct/time_bonus_pct/cost_bonus_pct на facility (см.
``config.Facility``): пользователь выбирает фактически фитованные type_id (риги реакций/
инженерные риги, сервис-модули), а множитель считается из атрибутов SDE
(``sde_dogma_type_attributes``) + формулы стэкинг-пенальти EVE. tax_pct остаётся ручным полем
(это налог структуры от владельца, не риг). Инженерные риги МОГУТ давать cost-бонус
(``attributeEngRigCostBonus``, напр. «Standup M-Set Invention Cost Optimization II» = -12% ×
nullSecModifier); у реакторных ригов такого атрибута в SDE нет вообще (естественно даёт 0%).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

# Атрибуты material/time/cost для инженерных (production/invention/copy) ригов и сервис-модулей.
ENG_TIME_BONUS = 2593   # attributeEngRigTimeBonus
ENG_MAT_BONUS = 2594    # attributeEngRigMatBonus
ENG_COST_BONUS = 2595   # attributeEngRigCostBonus (напр. Invention Cost Optimization риг = -12%)
# Атрибуты material/time для реакторных (reaction) ригов. Cost-атрибута у реакторных ригов
# в SDE нет вообще — для role="reaction" kind="cost" естественно даёт 0% (нет строки в БД).
REF_TIME_BONUS = 2713   # RefRigTimeBonus
REF_MAT_BONUS = 2714    # RefRigMatBonus

# По роли facility — какой attribute_id считать «материал»/«время»/«стоимость» (реакции
# используют отдельные Ref*-атрибуты для материала/времени и не имеют cost-атрибута вовсе;
# всё остальное — Eng*-атрибуты инженерных ригов).
_ATTR_MAP_BY_KIND: dict[str, dict[str, int]] = {
    "material": {"reaction": REF_MAT_BONUS},
    "time": {"reaction": REF_TIME_BONUS},
    "cost": {},
}
_DEFAULT_ATTR_BY_KIND = {
    "material": ENG_MAT_BONUS,
    "time": ENG_TIME_BONUS,
    "cost": ENG_COST_BONUS,
}

# Модификатор рига по security-статусу СИСТЕМЫ, где стоит структура: риги реакций/переработки
# в EVE дают БОЛЬШЕ базового бонуса в null-sec/WH, чем в hi/low-sec (напр. базовый -2.4% ×
# nullSecModifier 1.1 = -2.64% в null-sec). Значение — множитель к «сырому» value рига; нет
# атрибута у конкретного рига → множитель 1.0 (без изменений).
HISEC_MOD = 2355    # hiSecModifier
LOWSEC_MOD = 2356   # lowSecModifier
NULLSEC_MOD = 2357  # nullSecModifier (Nullsec and Wormhole Bonus Multiplier)


def _security_attribute_id(system_security: float) -> int:
    """Полоса security (как в клиенте EVE: >=0.45 raw округляется до отображаемых 0.5 хайсека)."""
    if system_security >= 0.45:
        return HISEC_MOD
    if system_security > 0.0:
        return LOWSEC_MOD
    return NULLSEC_MOD  # null-sec и WH используют один и тот же множитель


def _security_multiplier(conn: sqlite3.Connection, type_id: int, system_security: float) -> float:
    attr = _security_attribute_id(system_security)
    row = conn.execute(
        "SELECT value_float FROM sde_dogma_type_attributes WHERE type_id = ? AND attribute_id = ?",
        (type_id, attr),
    ).fetchone()
    value = row["value_float"] if row else None
    return value if value else 1.0


# Встроенные (не риговые) бонусы САМОЙ структуры. В отличие от ригов, значения здесь —
# ГОТОВЫЕ множители (напр. 0.85 = экономия 15%), а не «сырые» проценты. Отдельная от ригов
# группа стэкинга — комбинируется с риговым множителем простым перемножением, без
# стэкинг-пенальти между собой (структурный бонус ОДИН на структуру, стэкать нечего).
STR_ENG_MAT_BONUS = 2600         # strEngMatBonus — Engineering Complex (production/invention/copy/component)
STR_ENG_COST_BONUS = 2601        # strEngCostBonus — Engineering Complex (растёт по тиру: Raitaru/Azbel/Sotiyo)
STR_ENG_TIME_BONUS = 2602        # strEngTimeBonus — Engineering Complex
STR_REACTION_TIME_MULT = 2721    # strReactionTimeMultiplier — ТОЛЬКО у Tatara (у Athanor нет)

# По роли+kind — какой attribute_id структуры смотреть. У реакций своя пара (только время;
# бонуса материала/стоимости от структуры на реакции в EVE нет вообще — только Ref*TimeMultiplier
# у Tatara); у остальных ролей — Eng*-атрибуты (материал/стоимость/время).
_STRUCTURE_ATTR_BY_ROLE_KIND: dict[tuple[str, str], int] = {
    ("reaction", "time"): STR_REACTION_TIME_MULT,
}
_STRUCTURE_ENG_ATTR_BY_KIND = {
    "material": STR_ENG_MAT_BONUS,
    "cost": STR_ENG_COST_BONUS,
    "time": STR_ENG_TIME_BONUS,
}


def structure_mult(conn: sqlite3.Connection, structure_type_id: int, role: str, kind: str) -> float:
    """Множитель встроенного бонуса структуры (0 или нет атрибута → 1.0, без бонуса)."""
    if not structure_type_id:
        return 1.0
    attr = _STRUCTURE_ATTR_BY_ROLE_KIND.get((role, kind))
    if attr is None and role != "reaction":
        attr = _STRUCTURE_ENG_ATTR_BY_KIND.get(kind)
    if attr is None:
        return 1.0
    row = conn.execute(
        "SELECT value_float FROM sde_dogma_type_attributes WHERE type_id = ? AND attribute_id = ?",
        (structure_type_id, attr),
    ).fetchone()
    value = row["value_float"] if row else None
    return value if value else 1.0


@dataclass(frozen=True)
class FittedBonus:
    type_id: int
    attribute_id: int
    value: float  # обычно отрицательное (снижение), напр. -2.0


def fitted_bonuses(
    conn: sqlite3.Connection,
    fitted_type_ids: list[int],
    attribute_id: int,
    system_security: float | None = None,
) -> list[FittedBonus]:
    """Значения ОДНОГО атрибута у фитованных type_id (для стэкинга внутри одной «семьи»).

    ``system_security`` — security-статус системы, где стоит структура (0.0 = null-sec). Если
    задан, «сырое» значение рига домножается на его security-модификатор (риги реакций/
    переработки дают БОЛЬШЕ базового бонуса в null-sec/WH — см. ``_security_multiplier``).
    Без ``system_security`` (None) — сырое значение как есть."""
    if not fitted_type_ids:
        return []
    ph = ",".join("?" for _ in fitted_type_ids)
    rows = conn.execute(
        f"SELECT type_id, attribute_id, value_float FROM sde_dogma_type_attributes "
        f"WHERE attribute_id = ? AND type_id IN ({ph})",
        [attribute_id, *fitted_type_ids],
    ).fetchall()
    out = []
    for r in rows:
        value = r["value_float"]
        if value is None or value == 0.0:
            continue
        if system_security is not None:
            value *= _security_multiplier(conn, r["type_id"], system_security)
        out.append(FittedBonus(r["type_id"], r["attribute_id"], value))
    return out


def stacking_penalty_mult(bonus_pcts: list[float]) -> float:
    """Композиция ОДНОРОДНЫХ %-бонусов (одна «семья») по формуле стэкинг-пенальти EVE.

    Сортируем по убыванию |величины|; ранг r (0-based) получает вес exp(-(r/2.67)^2)
    (1.0, 0.869, 0.571, 0.283, 0.106, ...). Каждый бонус даёт мультипликатор
    (1 + (pct/100) × вес); итоговый — их произведение. Пустой список → 1.0 (без бонуса).
    """
    if not bonus_pcts:
        return 1.0
    ordered = sorted(bonus_pcts, key=abs, reverse=True)
    mult = 1.0
    for rank, pct in enumerate(ordered):
        weight = math.exp(-((rank / 2.67) ** 2))
        mult *= 1.0 + (pct / 100.0) * weight
    return mult


def facility_bonus_pct(
    conn: sqlite3.Connection,
    fitted_type_ids: list[int],
    role: str,
    kind: str,
    system_security: float | None = None,
    structure_type_id: int = 0,
) -> float:
    """Эффективный %-бонус (материал, время или стоимость джоба) для роли facility из
    фитованных type_id + встроенного бонуса самой структуры.

    ``kind`` — 'material' | 'time' | 'cost'. Возвращает % в ТОЙ ЖЕ конвенции, что ручные
    material_bonus_pct/time_bonus_pct/cost_bonus_pct (положительное число = экономия), чтобы
    бесшовно подставляться в тот же конвейер Facility/FacilityMult (cost.py).
    ``system_security`` — security-статус системы структуры (для null-sec/WH security-
    модификатора рига, см. ``fitted_bonuses``); None — без модификатора.
    ``structure_type_id`` — тип структуры (Tatara/Athanor/Raitaru/Azbel/Sotiyo); её встроенный
    бонус (``structure_mult``) перемножается с риговым — отдельная от ригов группа стэкинга.
    """
    attribute_id = _ATTR_MAP_BY_KIND[kind].get(role, _DEFAULT_ATTR_BY_KIND[kind])
    bonuses = fitted_bonuses(conn, fitted_type_ids, attribute_id, system_security)
    rig_mult = stacking_penalty_mult([b.value for b in bonuses])  # value уже отрицательный = снижение
    str_mult = structure_mult(conn, structure_type_id, role, kind)
    total_mult = rig_mult * str_mult
    if total_mult == 1.0:
        return 0.0
    return (1.0 - total_mult) * 100.0  # обратно в конвенцию "% экономии" у Facility
