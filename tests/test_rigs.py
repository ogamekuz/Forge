"""forge.core.rigs — бонусы фитованных ригов из SDE dogma-атрибутов + стэкинг-пенальти EVE."""

from __future__ import annotations

import math

import pytest

from forge.core import rigs


def _seed_dogma(conn, rows):
    """rows: list[(type_id, attribute_id, value_float)]."""
    conn.executescript(
        "INSERT INTO sde_dogma_attribute_types(attribute_id,name,display_name,stackable,high_is_good) "
        "VALUES (2593,'attributeEngRigTimeBonus','Time Reduction Bonus',1,0),"
        "       (2594,'attributeEngRigMatBonus','Material Reduction Bonus',1,0),"
        "       (2713,'RefRigTimeBonus','Time Bonus',1,0),"
        "       (2714,'RefRigMatBonus','Material Reduction Bonus',1,0),"
        "       (2355,'hiSecModifier','High Security Bonus Multiplier',0,1),"
        "       (2356,'lowSecModifier','Low Security Bonus Multiplier',0,1),"
        "       (2357,'nullSecModifier','Nullsec and Wormhole Bonus Multiplier',0,1),"
        "       (2600,'strEngMatBonus',NULL,0,1),"
        "       (2601,'strEngCostBonus',NULL,0,1),"
        "       (2602,'strEngTimeBonus',NULL,0,1),"
        "       (2721,'strReactionTimeMultiplier',NULL,0,1);"
    )
    for type_id, attribute_id, value in rows:
        conn.execute(
            "INSERT INTO sde_dogma_type_attributes(type_id,attribute_id,value_float) VALUES (?,?,?)",
            (type_id, attribute_id, value),
        )
    conn.commit()


def test_fitted_bonuses_filters_zero_and_missing(conn):
    _seed_dogma(conn, [(46486, rigs.REF_MAT_BONUS, -2.0), (46486, rigs.REF_TIME_BONUS, 0.0)])
    # Ноль (нет бонуса этого типа у рига) должен отфильтроваться.
    assert rigs.fitted_bonuses(conn, [46486], rigs.REF_TIME_BONUS) == []
    got = rigs.fitted_bonuses(conn, [46486], rigs.REF_MAT_BONUS)
    assert len(got) == 1 and got[0].value == pytest.approx(-2.0)
    # Пустой список фитованных type_id → пусто, без запроса к БД.
    assert rigs.fitted_bonuses(conn, [], rigs.REF_MAT_BONUS) == []


def test_stacking_penalty_mult_known_values():
    assert rigs.stacking_penalty_mult([]) == 1.0
    # Один риг −2%: множитель 0.98 ровно.
    assert rigs.stacking_penalty_mult([-2.0]) == pytest.approx(0.98)
    # Два одинаковых рига −2%/−2%: ранг1 получает вес exp(-(1/2.67)^2) (стандартная пенальти EVE).
    w1 = math.exp(-((1 / 2.67) ** 2))
    expected = (1 + (-2.0 / 100) * 1.0) * (1 + (-2.0 / 100) * w1)
    assert rigs.stacking_penalty_mult([-2.0, -2.0]) == pytest.approx(expected)
    assert rigs.stacking_penalty_mult([-2.0, -2.0]) < 0.98  # больше экономии, чем один риг,
    assert rigs.stacking_penalty_mult([-2.0, -2.0]) > 0.96  # но не вдвое (пенальти на второй ранг)


def test_facility_bonus_pct_single_reaction_rig(conn):
    _seed_dogma(conn, [(46486, rigs.REF_MAT_BONUS, -2.0)])
    pct = rigs.facility_bonus_pct(conn, [46486], "reaction", "material")
    assert pct == pytest.approx(2.0)  # (1 - 0.98) * 100


def test_facility_bonus_pct_role_dispatch_ignores_wrong_attribute_family(conn):
    """Реакторный риг (RefRigMatBonus) не должен давать бонус, если facility имеет роль
    'manufacturing' (та смотрит на attributeEngRigMatBonus — другой атрибут)."""
    _seed_dogma(conn, [(46486, rigs.REF_MAT_BONUS, -2.0)])
    assert rigs.facility_bonus_pct(conn, [46486], "reaction", "material") == pytest.approx(2.0)
    assert rigs.facility_bonus_pct(conn, [46486], "manufacturing", "material") == 0.0


def test_facility_bonus_pct_empty_fit_returns_zero(conn):
    assert rigs.facility_bonus_pct(conn, [], "reaction", "material") == 0.0


def test_security_modifier_scales_reaction_rig_bonus(conn):
    """Реальный кейс юзера: Standup L-Set Reactor Efficiency II даёт -2.4%
    материала «в вакууме», но структура стоит в null-sec — риг там даёт БОЛЬШЕ базового
    бонуса (nullSecModifier=1.1): -2.4% × 1.1 = -2.64%, что и показывает клиент EVE (9736 из
    10000 базовых единиц, не 9760 от «сырых» -2.4%)."""
    _seed_dogma(conn, [
        (46497, rigs.REF_MAT_BONUS, -2.4),
        (46497, rigs.NULLSEC_MOD, 1.1),
        (46497, rigs.LOWSEC_MOD, 1.0),
    ])
    # Без security (None) — сырое значение рига без модификатора.
    assert rigs.facility_bonus_pct(conn, [46497], "reaction", "material") == pytest.approx(2.4)
    # Null-sec (0.0) — умножаем на nullSecModifier.
    pct_null = rigs.facility_bonus_pct(conn, [46497], "reaction", "material", system_security=0.0)
    assert pct_null == pytest.approx(2.64)
    # Low-sec (0.3) — свой модификатор (тут 1.0, без изменений).
    pct_low = rigs.facility_bonus_pct(conn, [46497], "reaction", "material", system_security=0.3)
    assert pct_low == pytest.approx(2.4)


def test_security_modifier_missing_defaults_to_one(conn):
    """Нет security-атрибута у конкретного рига (напр. hiSecModifier не задан) → множитель 1.0,
    не падаем и не искажаем бонус."""
    _seed_dogma(conn, [(46497, rigs.REF_MAT_BONUS, -2.4)])  # без hi/low/nullSecModifier вообще
    pct = rigs.facility_bonus_pct(conn, [46497], "reaction", "material", system_security=0.0)
    assert pct == pytest.approx(2.4)  # множитель 1.0 — как без security вовсе


def test_security_band_thresholds():
    assert rigs._security_attribute_id(0.9) == rigs.HISEC_MOD
    assert rigs._security_attribute_id(0.45) == rigs.HISEC_MOD  # округляется до отображаемых 0.5
    assert rigs._security_attribute_id(0.44) == rigs.LOWSEC_MOD
    assert rigs._security_attribute_id(0.1) == rigs.LOWSEC_MOD
    assert rigs._security_attribute_id(0.0) == rigs.NULLSEC_MOD
    assert rigs._security_attribute_id(-0.5) == rigs.NULLSEC_MOD  # WH делит модификатор с null-sec


def test_structure_mult_reaction_time_only_tatara(conn):
    """Tatara даёт встроенный -25% времени реакции (strReactionTimeMultiplier=0.75); материала
    на реакции структура НЕ даёт вообще (нет такого атрибута в EVE) — мультипликатор 1.0."""
    TATARA = 35836
    _seed_dogma(conn, [(TATARA, rigs.STR_REACTION_TIME_MULT, 0.75)])
    assert rigs.structure_mult(conn, TATARA, "reaction", "time") == pytest.approx(0.75)
    assert rigs.structure_mult(conn, TATARA, "reaction", "material") == 1.0  # нет атрибута
    assert rigs.structure_mult(conn, 0, "reaction", "time") == 1.0  # structure_type_id не задан


def test_structure_mult_engineering_complex_material_time(conn):
    """Engineering Complex (Raitaru/Azbel/Sotiyo) даёт strEngMatBonus/strEngCostBonus/
    strEngTimeBonus для production/invention/copy/component — НЕ для reaction (там другой
    атрибут, см. выше)."""
    SOTIYO = 35827
    _seed_dogma(conn, [
        (SOTIYO, rigs.STR_ENG_MAT_BONUS, 0.99),
        (SOTIYO, rigs.STR_ENG_COST_BONUS, 0.95),
        (SOTIYO, rigs.STR_ENG_TIME_BONUS, 0.7),
    ])
    assert rigs.structure_mult(conn, SOTIYO, "manufacturing", "material") == pytest.approx(0.99)
    assert rigs.structure_mult(conn, SOTIYO, "manufacturing", "cost") == pytest.approx(0.95)
    assert rigs.structure_mult(conn, SOTIYO, "manufacturing", "time") == pytest.approx(0.7)
    assert rigs.structure_mult(conn, SOTIYO, "invention", "material") == pytest.approx(0.99)
    # У reaction — свой атрибут (strReactionTimeMultiplier), Sotiyo (Engineering Complex) его не
    # несёт вообще → 1.0, не путается с Eng-бонусом. Cost для reaction структура тоже не даёт.
    assert rigs.structure_mult(conn, SOTIYO, "reaction", "time") == 1.0
    assert rigs.structure_mult(conn, SOTIYO, "reaction", "cost") == 1.0


def test_facility_bonus_pct_component_role_real_azbel_scenario(conn):
    """Реальный кейс юзера (Capital Gravimetric Sensor Cluster, facility role=
    component, риг + структура Azbel): риг −24%/−2.4% (× nullSecModifier 2.1 — у инженерных
    ригов свой, гораздо больший множитель, чем у реакторных 1.1) + встроенный бонус Azbel
    (время −20%, материал −1%, cost −4%). Подтверждено точным совпадением с клиентом EVE:
    Installed Rig −50.4%, Structure Role Bonus −20.0% (время) и −4.0% (cost)."""
    RIG = 37175
    AZBEL = 35826
    _seed_dogma(conn, [
        (RIG, rigs.ENG_MAT_BONUS, -2.4),
        (RIG, rigs.ENG_TIME_BONUS, -24.0),
        (RIG, rigs.NULLSEC_MOD, 2.1),
        (AZBEL, rigs.STR_ENG_MAT_BONUS, 0.99),
        (AZBEL, rigs.STR_ENG_COST_BONUS, 0.96),
        (AZBEL, rigs.STR_ENG_TIME_BONUS, 0.8),
    ])
    time_pct = rigs.facility_bonus_pct(conn, [RIG], "component", "time", system_security=0.0, structure_type_id=AZBEL)
    mat_pct = rigs.facility_bonus_pct(conn, [RIG], "component", "material", system_security=0.0, structure_type_id=AZBEL)
    # риг: -24% × 2.1 = -50.4% (сам по себе, это и есть "Installed Rig" в тултипе EVE)
    rig_only_time = rigs.facility_bonus_pct(conn, [RIG], "component", "time", system_security=0.0)
    assert rig_only_time == pytest.approx(50.4)
    # комбинация с Azbel (время): mult = (1-0.504)*0.8=0.3968 → 60.32% суммарной экономии
    assert time_pct == pytest.approx(60.32)
    # материал: риг -2.4%×2.1=-5.04% (mult 0.9496) × структура 0.99 = 0.940104 → 5.9896%
    assert mat_pct == pytest.approx(5.9896, rel=1e-3)
    cost_mult = rigs.structure_mult(conn, AZBEL, "component", "cost")
    assert cost_mult == pytest.approx(0.96)  # -4%, структура; этот конкретный риг cost не даёт
    # (ENG_COST_BONUS не заряжен в _seed_dogma для RIG выше) — см. отдельный тест ниже про
    # инвент-риг, который РЕАЛЬНО даёт cost-бонус (attributeEngRigCostBonus != 0).
    assert rigs.facility_bonus_pct(conn, [RIG], "component", "cost", system_security=0.0, structure_type_id=AZBEL) == pytest.approx(4.0)


def test_facility_bonus_pct_combines_rig_and_structure_reaction_case(conn):
    """Полный реальный сценарий: риг −2.4%/−24% (× nullSecModifier 1.1 на материал/время) +
    встроенный бонус Tatara −25% времени (только время, материала структура не даёт). Итог —
    именно то, что подтвердил юзер по факту в игре: материал 2.64% (только риг), время —
    комбинация рига (домноженного на security) И структуры перемножением множителей."""
    RIG = 46497
    TATARA = 35836
    _seed_dogma(conn, [
        (RIG, rigs.REF_MAT_BONUS, -2.4),
        (RIG, rigs.REF_TIME_BONUS, -24.0),
        (RIG, rigs.NULLSEC_MOD, 1.1),
        (TATARA, rigs.STR_REACTION_TIME_MULT, 0.75),
    ])
    mat_pct = rigs.facility_bonus_pct(conn, [RIG], "reaction", "material", system_security=0.0, structure_type_id=TATARA)
    time_pct = rigs.facility_bonus_pct(conn, [RIG], "reaction", "time", system_security=0.0, structure_type_id=TATARA)
    assert mat_pct == pytest.approx(2.64)  # структура не участвует (нет атрибута материала)
    # риг: -24% × 1.1 = -26.4% → mult 0.736; структура: 0.75; итог 0.736*0.75=0.552 → 44.8% экономии
    assert time_pct == pytest.approx(44.8)


def test_facility_bonus_pct_cost_kind_real_invention_rig_scenario(conn):
    """Реальный кейс юзера (инвента Nergal, facility role=invention): риг
    «Standup M-Set Invention Cost Optimization II» несёт attributeEngRigCostBonus=-12.0
    (facility_bonus_pct читает kind 'cost', не только
    'material'/'time'). С nullSecModifier=2.1 эффективный риговый cost-бонус -25.2% — ТОЧНО
    совпадает с «Installed Rig: -25.2%» в тултипе EVE (Job Gross Cost > Bonuses). Плюс
    встроенный cost-бонус структуры Raitaru (strEngCostBonus=0.97, -3% — «Structure Role
    Bonus: -3.0%» в том же тултипе), который комбинируется с риговым перемножением."""
    COST_RIG = 43878
    OTHER_RIG_NO_COST = 43881  # даёт время, но cost=0.0 — не должен участвовать в cost-стэке
    RAITARU = 35825
    _seed_dogma(conn, [
        (COST_RIG, rigs.ENG_COST_BONUS, -12.0),
        (COST_RIG, rigs.NULLSEC_MOD, 2.1),
        (OTHER_RIG_NO_COST, rigs.ENG_TIME_BONUS, -24.0),
        (RAITARU, rigs.STR_ENG_COST_BONUS, 0.97),
    ])
    rig_only = rigs.facility_bonus_pct(
        conn, [COST_RIG, OTHER_RIG_NO_COST], "invention", "cost", system_security=0.0
    )
    assert rig_only == pytest.approx(25.2)  # -12.0 × 2.1, ровно "Installed Rig" в тултипе
    combined = rigs.facility_bonus_pct(
        conn, [COST_RIG, OTHER_RIG_NO_COST], "invention", "cost",
        system_security=0.0, structure_type_id=RAITARU,
    )
    # риг: mult 0.748; структура: 0.97; итог 0.72556 → 27.444% суммарной экономии
    assert combined == pytest.approx(27.444)


def test_facility_bonus_pct_cost_kind_reaction_role_has_no_rig_attribute(conn):
    """У реакторных ригов в SDE нет cost-атрибута вовсе (нет строки в БД) — kind='cost' для
    role='reaction' должен естественно давать 0%, без падения и без побочного дефолта на
    инженерный ENG_COST_BONUS атрибут этого же type_id, если он там случайно есть."""
    RIG = 46497
    _seed_dogma(conn, [(RIG, rigs.REF_MAT_BONUS, -2.4)])  # у рига нет ENG_COST_BONUS вообще
    assert rigs.facility_bonus_pct(conn, [RIG], "reaction", "cost", system_security=0.0) == 0.0
