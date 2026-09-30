"""Шанс инвенты со скиллами инвентора (Forge 3.0, [industry] invention_use_skills).

EVE: шанс = база SDE × (1 + Encryption/40 + (наука1 + наука2)/30) × множитель декриптора, ≤ 1.0.
Скиллы — те, что требует инвента T1-чертежа-источника; инвентор — лучший из пула «наука».
"""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core
from forge.core import blueprint as bp
from forge.core import sourcing
from forge.web import report

BASE = """
db_path = "x.db"
{top}
[industry]
{industry}
[locations.jita]
name = "Jita"
region_id = 10000002
[locations.c_j6mt]
name = "C-J6MT"
region_id = 10000009
[locations.gplb_c]
name = "GPLB-C"
region_id = 10000006
"""

ENC, SCI1, SCI2 = 23121, 11452, 11450   # Gallente Encryption Methods, Mechanical Eng., Gallente SE
MANUF_BP, PRODUCT, T1_BP = 2186, 2185, 2184
FULL = 1 + 5 / 40 + 10 / 30              # Encryption 5, науки 5/5


def cfg(top: str = "", industry: str = "") -> config_mod.Config:
    return config_mod.loads(BASE.format(top=top, industry=industry))


def _seed(conn, probability: float = 0.3):
    conn.executescript(
        f"""
        INSERT INTO sde_types(type_id,name,volume) VALUES (1001,'DC A',0.0),(1002,'DC B',0.0),
            (2185,'T2 Item',1.0),(40,'Mineral',0.01),
            ({ENC},'Gallente Encryption Methods',0.01),({SCI1},'Mechanical Engineering',0.01),
            ({SCI2},'Gallente Starship Engineering',0.01);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (1001,10000009,100000.0,1000),(1002,10000009,50000.0,1000),(40,10000009,10.0,1000000);
        -- инвента: T1 BP 2184 -> T2 BP 2186, 10 ранов/копия
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,8,2186,10,{probability}),(2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,8,1001,2),(2184,8,1002,2),(2186,1,40,100);
        INSERT INTO sde_blueprint_skills(blueprint_type_id,activity_id,skill_type_id,level)
            VALUES (2184,8,{ENC},1),(2184,8,{SCI1},1),(2184,8,{SCI2},1);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor'),(8,'Alt'),(9,'Third');
        """
    )
    conn.commit()


def _skills(conn, cid: int, enc: int, sci1: int, sci2: int) -> None:
    conn.executemany(
        "INSERT INTO character_skills(character_id,skill_type_id,active_level) VALUES (?,?,?)",
        [(cid, ENC, enc), (cid, SCI1, sci1), (cid, SCI2, sci2)],
    )
    conn.commit()


def _node(conn, c: config_mod.Config, runs: int = 1):
    params = core.build_params_from_config(c, conn)
    return sourcing.build_node_cost(conn, PRODUCT, runs, 1, params, allow_build=False)


# ------------------------------------------------------------------ множитель скиллов

def test_default_is_on():
    assert config_mod.Config().industry.invention_use_skills is True


def test_zero_skills_keep_base_chance(conn):
    _seed(conn)
    _skills(conn, 7, 0, 0, 0)
    params = core.build_params_from_config(cfg(), conn)
    sk = bp.invention_skills(conn, MANUF_BP, params)
    assert sk.enabled and sk.mult == 1.0
    base = next(o for o in bp.invention_options(conn, MANUF_BP, params) if o.decryptor is None)
    assert base.prob == 0.3 and base.base_prob == 0.3 and base.skill_mult == 1.0
    n = _node(conn, cfg())
    assert n.invention_breakdown["attempts"] == 4           # ceil(1/0.3) — как без скиллов


def test_encryption5_sciences55_multiplier(conn):
    _seed(conn)
    _skills(conn, 7, 5, 5, 5)
    params = core.build_params_from_config(cfg(), conn)
    sk = bp.invention_skills(conn, MANUF_BP, params)
    assert sk.mult == pytest.approx(1 + 5 / 40 + 10 / 30)
    assert sk.character_id == 7 and sk.character_name == "Igor"
    base = next(o for o in bp.invention_options(conn, MANUF_BP, params) if o.decryptor is None)
    assert base.prob == pytest.approx(0.3 * FULL)
    # per_run инвенты считается от итогового шанса
    assert bp.invention_per_run(conn, MANUF_BP, params) == pytest.approx(300000.0 / (0.3 * FULL * 10))


def test_encryption_and_sciences_have_different_divisors(conn):
    _seed(conn)
    _skills(conn, 7, 4, 3, 0)          # 1 + 4/40 + 3/30 = 1.2
    params = core.build_params_from_config(cfg(), conn)
    assert bp.invention_skills(conn, MANUF_BP, params).mult == pytest.approx(1.2)


def test_best_inventor_is_chosen_from_science_pool(conn):
    _seed(conn)
    _skills(conn, 7, 1, 1, 1)
    _skills(conn, 8, 4, 4, 4)
    _skills(conn, 9, 5, 5, 5)          # лучший, но не в пуле «наука»
    c = cfg("manufacturing_character_ids = [9]\nscience_character_ids = [7, 8]")
    sk = bp.invention_skills(conn, MANUF_BP, core.build_params_from_config(c, conn))
    assert sk.character_id == 8 and sk.mult == pytest.approx(1 + 4 / 40 + 8 / 30)
    # наука пуста — наукой занимаются те, кто на производстве (как role_allowed в расписании)
    legacy = cfg("manufacturing_character_ids = [9]")
    assert bp.invention_skills(conn, MANUF_BP, core.build_params_from_config(legacy, conn)).character_id == 9
    # ни одного списка ролей — все персонажи, лучший из всех
    assert bp.invention_skills(conn, MANUF_BP, core.build_params_from_config(cfg(), conn)).character_id == 9
    # роли заданы, но на науку/производство — никого: кандидатов нет, базовый шанс
    rx_only = cfg("reaction_character_ids = [9]")
    sk2 = bp.invention_skills(conn, MANUF_BP, core.build_params_from_config(rx_only, conn))
    assert sk2.enabled and sk2.character_id is None and sk2.mult == 1.0


def test_science_character_ids_matches_scheduler_roles(conn):
    _seed(conn)
    assert sorted(core.science_character_ids(cfg(), conn)) == [7, 8, 9]
    assert core.science_character_ids(cfg("manufacturing_character_ids = [8]"), conn) == [8]
    assert core.science_character_ids(
        cfg("manufacturing_character_ids = [8]\nscience_character_ids = [9]"), conn) == [9]
    assert core.science_character_ids(cfg("reaction_character_ids = [7]"), conn) == []


# ------------------------------------------------------------------ себестоимость / попытки

def test_flag_off_gives_old_numbers(conn):
    _seed(conn)
    _skills(conn, 7, 5, 5, 5)
    off = _node(conn, cfg(industry="invention_use_skills = false"))
    # базовый шанс SDE (см. test_blueprint::test_invention_charges_whole_bpc_and_whole_attempts)
    assert off.invention_breakdown["probability"] == 0.3
    assert off.invention_breakdown["attempts"] == 4
    assert off.material_cost == pytest.approx(980.0 + 1_200_000.0)
    assert off.invention_breakdown["skills_enabled"] is False
    # и ровно то же, что с включённой опцией, но без скиллов в БД вообще
    conn.execute("DELETE FROM character_skills")
    conn.commit()
    no_skills = _node(conn, cfg())
    assert no_skills.total_cost == off.total_cost
    assert no_skills.invention_breakdown["attempts"] == off.invention_breakdown["attempts"]


def test_skills_mean_fewer_attempts_and_lower_cost(conn):
    _seed(conn)
    before = _node(conn, cfg())                     # скиллов в БД нет — базовый шанс
    _skills(conn, 7, 5, 5, 5)
    after = _node(conn, cfg())
    assert before.invention_breakdown["attempts"] == 4   # ceil(1/0.3)
    assert after.invention_breakdown["attempts"] == 3    # ceil(1/0.4375)
    assert after.total_cost < before.total_cost
    b = after.invention_breakdown
    assert b["base_probability"] == 0.3 and b["skill_mult"] == pytest.approx(FULL)
    assert b["probability"] == pytest.approx(0.3 * FULL)
    assert b["inventor_name"] == "Igor" and b["skills_enabled"] is True and b["decryptor_mult"] == 1.0
    dc = {ln.type_id: ln.quantity for ln in after.lines if ln.type_id in (1001, 1002)}
    assert dc == {1001: 6, 1002: 6}                      # 2 шт./попытку × 3 попытки


def test_decryptor_multiplies_skilled_chance(conn):
    _seed(conn)
    _skills(conn, 7, 5, 5, 5)
    conn.execute("INSERT INTO sde_types(type_id,name,volume) VALUES (34207,'Optimized Attainment Decryptor',0.1)")
    conn.execute("INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34207,10000009,1.0,100)")
    conn.commit()
    opts = bp.invention_options(conn, MANUF_BP, core.build_params_from_config(cfg(), conn))
    oa = next(o for o in opts if o.decryptor == "Optimized Attainment")
    assert oa.prob == pytest.approx(0.3 * FULL * 1.9)
    assert oa.decryptor_mult == 1.9 and oa.skill_mult == pytest.approx(FULL)


def test_chance_capped_at_one(conn):
    _seed(conn, probability=0.9)
    _skills(conn, 7, 5, 5, 5)                       # 0.9 × 1.458 > 1
    conn.execute("INSERT INTO sde_types(type_id,name,volume) VALUES (34207,'Optimized Attainment Decryptor',0.1)")
    conn.execute("INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34207,10000009,1.0,100)")
    conn.commit()
    opts = bp.invention_options(conn, MANUF_BP, core.build_params_from_config(cfg(), conn))
    assert all(o.prob <= 1.0 for o in opts)
    assert next(o for o in opts if o.decryptor is None).prob == 1.0
    n = _node(conn, cfg(), runs=10)
    assert n.invention_breakdown["attempts"] == 1        # 1 копия × шанс 100%


def test_shortfall_invention_uses_skills(conn):
    """Недостача ранов своей копии закрывается той же инвентой — с тем же шансом со скиллами."""
    _seed(conn)
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,2186,0,0,1,5,1)")   # копия на 5 прогонов, нужно 10
    conn.commit()
    before = _node(conn, cfg(), runs=10)
    assert before.blueprint_source == "owned_bpc_insufficient"
    assert before.bpc_shortfall_invention_attempts == 4
    assert before.bpc_shortfall_probability == 0.3
    _skills(conn, 7, 5, 5, 5)
    after = _node(conn, cfg(), runs=10)
    assert after.bpc_shortfall_invention_attempts == 3
    assert after.bpc_shortfall_probability == pytest.approx(0.3 * FULL)
    off = _node(conn, cfg(industry="invention_use_skills = false"), runs=10)
    assert off.bpc_shortfall_invention_attempts == 4


def test_attempts_for_rounds_up_without_float_noise():
    assert bp.attempts_for(1, 0.3) == 4
    assert bp.attempts_for(9, 0.3 * 1.5) == 20        # 9/0.45 = 20.000000000000004 → 20, не 21
    assert bp.attempts_for(2, 1.0) == 2
    assert bp.attempts_for(3, 0.0) == 0


# ------------------------------------------------------------------ отчёт

def test_report_prep_shows_final_chance():
    p = {"places": {"jita": "Jita", "cj": "C-J6MT", "build": "GPLB-C"},
         "prep": {"transfers": []},
         "shopping": {"jita": [], "cj": [], "on_hand": [], "own_build": [], "issues": [],
                      "invention": [{"type_id": PRODUCT, "name": "T2 Item", "produced": 1,
                                     "decryptor": None, "attempts": 3, "probability": 0.442}],
                      "totals": {"jita_isk": 0.0, "cj_isk": 0.0, "on_hand_isk": 0.0,
                                 "freight_inbound": 0.0, "stock_haul_isk": 0.0}}}
    html = report._shopping_html(p)
    assert "3 попыт. Т1-копий, шанс 44.2%" in html
