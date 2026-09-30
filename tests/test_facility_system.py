"""Своя система у станции ([[facilities]] system_id): индекс стоимости джобов и security-модификатор
бонусов ригов — по системе станции (0 — авто из структуры, не известна — система стройки)."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core, storage
from forge.core import cost, rigs, sourcing
from forge.web.service import ForgeService

BUILD, OTHER, LOWSEC, JITA_SYS = 30000552, 30000772, 30002187, 30000142
STRUCT = 1030000000004
RIG = 43920   # условный инженерный риг M-Set: −2% материала × модификатор security

BASE = """
db_path = "forge.db"
[industry]
invention_use_skills = false
[locations.jita]
name = "Jita"
system_id = 30000142
region_id = 10000002
[locations.c_j6mt]
name = "C-J6MT"
system_id = 30000772
region_id = 10000009
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006
{facilities}
"""


def cfg(facilities: str = "") -> config_mod.Config:
    return config_mod.loads(BASE.format(facilities=facilities))


def fac(role: str = "manufacturing", extra: str = "") -> str:
    return f'[[facilities]]\nname = "Станция"\nrole = "{role}"\n{extra}\n'


def _seed(conn):
    conn.executescript(
        f"""
        INSERT INTO sde_systems(system_id,name,region_id,security) VALUES
            ({BUILD},'GPLB-C',10000006,-0.25),({OTHER},'C-J6MT',10000009,-0.29),
            ({LOWSEC},'Amamake',10000042,0.3),({JITA_SYS},'Jita',10000002,0.95);
        INSERT INTO sde_stations(station_id,name,system_id) VALUES (60003760,'Jita IV - Moon 4',{JITA_SYS});
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',1.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,1000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,5.0);   -- EIV = 5000/прогон
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000009,6.0,10000000);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES
            ({BUILD},1,0.05),({OTHER},1,0.10),({JITA_SYS},1,0.02),
            ({BUILD},8,0.04),({OTHER},8,0.12),({BUILD},5,0.03),({OTHER},5,0.09);
        INSERT INTO sde_dogma_type_attributes(type_id,attribute_id,value_float) VALUES
            ({RIG},{rigs.ENG_MAT_BONUS},-2.0),({RIG},{rigs.HISEC_MOD},1.0),
            ({RIG},{rigs.LOWSEC_MOD},1.9),({RIG},{rigs.NULLSEC_MOD},2.1);
        """
    )
    conn.commit()


def _node(conn, c):
    return sourcing.build_node_cost(conn, 2000, 1, 1, core.build_params_from_config(c, conn), allow_build=False)


def test_default_numbers_unchanged(conn):
    _seed(conn)
    base = _node(conn, cfg())                                     # станций нет — система стройки
    assert base.cost_index == 0.05 and base.cost_system_id == BUILD
    assert base.job_cost == pytest.approx(5000 * 0.05)
    same = _node(conn, cfg(fac(extra=f"location_id = {STRUCT}")))  # структура не резолвлена
    assert same.cost_system_id == BUILD and same.job_cost == base.job_cost
    explicit = _node(conn, cfg(fac(extra=f"system_id = {BUILD}")))
    assert explicit.total_cost == base.total_cost


def test_station_in_other_system_uses_its_cost_index(conn):
    _seed(conn)
    n = _node(conn, cfg(fac(extra=f"system_id = {OTHER}")))
    assert n.cost_system_id == OTHER and n.cost_index == 0.10
    assert n.job_cost == pytest.approx(5000 * 0.10)
    # материалы — «доставлены в систему стройки»: логистика между системами не считается
    assert n.material_cost == _node(conn, cfg()).material_cost


def test_auto_system_from_structure_and_npc_station(conn):
    _seed(conn)
    conn.execute("INSERT INTO universe_structures(structure_id,name,solar_system_id,status) VALUES (?,?,?,'ok')",
                 (STRUCT, "C-J6MT - Research", OTHER))
    conn.commit()
    n = _node(conn, cfg(fac(extra=f"location_id = {STRUCT}")))
    assert n.cost_system_id == OTHER and n.cost_index == 0.10
    # ручная система сильнее авто
    m = _node(conn, cfg(fac(extra=f"location_id = {STRUCT}\nsystem_id = {BUILD}")))
    assert m.cost_system_id == BUILD
    # NPC-станция — система из SDE
    s = _node(conn, cfg(fac(extra="location_id = 60003760")))
    assert s.cost_system_id == JITA_SYS and s.cost_index == 0.02


def test_facility_system_reports_how(conn):
    _seed(conn)
    resolver = core.locations.LocationResolver(conn, cfg())
    f = config_mod.Facility(name="x", role="copy", location_id=STRUCT)
    assert core.facility_system(f, resolver, BUILD) == (BUILD, "build")
    conn.execute("INSERT INTO universe_structures(structure_id,name,solar_system_id,status) VALUES (?,?,?,'ok')",
                 (STRUCT, "Research", OTHER))
    conn.commit()
    resolver = core.locations.LocationResolver(conn, cfg())
    assert core.facility_system(f, resolver, BUILD) == (OTHER, "structure")
    assert core.facility_system(config_mod.Facility(name="x", role="copy", system_id=LOWSEC),
                                resolver, BUILD) == (LOWSEC, "manual")


def test_engineering_rig_bonus_depends_on_station_security(conn):
    _seed(conn)
    low = core.build_params_from_config(cfg(fac(extra=f"fitted_type_ids = [{RIG}]\nsystem_id = {LOWSEC}")), conn)
    null = core.build_params_from_config(cfg(fac(extra=f"fitted_type_ids = [{RIG}]\nsystem_id = {OTHER}")), conn)
    assert low.facilities[0].material_mult == pytest.approx(1 - 0.02 * 1.9)
    assert null.facilities[0].material_mult == pytest.approx(1 - 0.02 * 2.1)
    # по умолчанию — система стройки (null-sec)
    default = core.build_params_from_config(cfg(fac(extra=f"fitted_type_ids = [{RIG}]")), conn)
    assert default.facilities[0].material_mult == pytest.approx(1 - 0.02 * 2.1)


def test_copy_and_invention_fees_use_role_station_system(conn):
    _seed(conn)
    c = cfg(fac("invention", f"system_id = {OTHER}") + fac("copy", f"system_id = {OTHER}"))
    params = core.build_params_from_config(c, conn)
    fee, sys_id, ci = sourcing._bp_job_fee_info(conn, 1000, params, cost.INVENTION)
    assert sys_id == OTHER and ci == 0.12
    assert fee == pytest.approx(0.02 * 5000 * 0.12)
    fee_c, sys_c, ci_c = sourcing._bp_job_fee_info(conn, 1000, params, cost.COPYING)
    assert sys_c == OTHER and ci_c == 0.09 and fee_c == pytest.approx(0.02 * 5000 * 0.09)
    # без станций ролей — система стройки
    base = core.build_params_from_config(cfg(), conn)
    assert sourcing._bp_job_fee(conn, 1000, base, cost.INVENTION) == pytest.approx(0.02 * 5000 * 0.04)


def test_service_shows_resolved_system(tmp_path):
    (tmp_path / "forge.toml").write_text(
        BASE.format(facilities=fac("copy", f"location_id = {STRUCT}\nfitted_type_ids = [{RIG}]")), encoding="utf-8")
    c = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(c)
    _seed(c)
    c.close()
    svc = ForgeService(tmp_path / "forge.toml")
    f = svc.get_config()["facilities"][0]
    assert f["resolved_system_id"] == BUILD and f["resolved_system_how"] == "build"
    svc.put_config({"facilities": [{"name": "Станция", "role": "copy", "location_id": STRUCT,
                                    "fitted_type_ids": [RIG], "system_id": LOWSEC}]})
    f = svc.get_config()["facilities"][0]
    assert f["resolved_system_id"] == LOWSEC and f["resolved_system_how"] == "manual"
    assert f["resolved_system_name"] == "Amamake"
    assert f["computed_material_bonus_pct"] == pytest.approx(2 * 1.9)   # low-sec модификатор рига
