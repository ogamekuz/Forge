"""Forge 3.0: бывшие «прибитые» решения, ставшие настройками — хабы закупки, цена продажи,
«всегда строить», самоходные группы, роль «наука», запущенные джобы, запреты рекомендаций,
входы инвенты в синке рынка и сервисный слой (частичное сохранение конфига, варианты сравнения)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from forge import config as config_mod
from forge import core, storage
from forge.core import cost
from forge.planner import schedule
from forge.recommend import engine
from forge.sync import orchestrator
from forge.web.service import Basket, ForgeService, ServiceError

BASE = """
db_path = "forge.db"
[industry]
broker_fee = 0.0
sales_tax = 0.0
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
[[freight_routes]]
from = "jita"
to = "c_j6mt"
mode = "per_m3"
isk_per_m3 = 1.0
[[freight_routes]]
from = "c_j6mt"
to = "gplb_c"
mode = "per_m3"
isk_per_m3 = 2.0
[[freight_routes]]
from = "gplb_c"
to = "c_j6mt"
mode = "per_m3"
isk_per_m3 = 2.0
"""


def cfg(extra: str = "") -> config_mod.Config:
    """Ключи верхнего уровня из ``extra`` — ДО таблиц BASE (иначе TOML отнёс бы их к последней
    [[freight_routes]]), секции ``[x]`` — после."""
    top, tables = [], []
    target = top
    for line in extra.splitlines():
        if line.startswith("["):
            target = tables
        target.append(line)
    nl = "\n"
    return config_mod.loads(nl.join(top) + nl + BASE + nl.join(tables) + nl)


def _seed(conn):
    conn.executescript(
        """
        INSERT INTO sde_categories(category_id,name) VALUES (4,'Material'),(6,'Ship'),(35,'Decryptors');
        INSERT INTO sde_groups(group_id,category_id,name) VALUES (18,4,'Mineral'),(25,6,'Frigate'),
            (999,6,'Dreadnought'),(1304,35,'Generic Decryptor'),(333,4,'Datacores');
        INSERT INTO sde_types(type_id,name,group_id,category_id,volume) VALUES
            (34,'Tritanium',18,4,0.01),(35,'Pyerite',18,4,0.01),
            (2000,'Widget',25,6,5.0),(2100,'Widget Mk2',25,6,5.0),(35000,'Subcomponent',18,4,1.0),
            (19720,'Revelation',999,6,100.0),(34201,'Accelerant Decryptor',1304,35,0.1),
            (20424,'Datacore - Mechanical Engineering',333,4,0.1);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1200,1,2100,1,1.0),(1100,1,35000,1,1.0),(1300,1,19720,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,600),(1200,1,600),(1100,1,60),(1300,1,6000),(1000,8,3600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,100),(1000,1,35,50),(1200,1,35000,1),(1100,1,34,10),(1300,1,34,1000),
                   (1000,8,20424,2);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0),(35,18.0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES
            (34,10000002,5.0,4.0,1000000),
            (35,10000002,19.0,18.0,1000000),(35,10000009,18.0,17.0,1000000),
            (35000,10000002,1.0,0.9,1000000),
            (2000,10000009,5000.0,4500.0,100),(19720,10000009,2000000.0,1900000.0,10);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor'),(8,'Alt');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,60005000,0,0,-1,-1,0),(2,7,1200,60009999,0,0,-1,-1,0);
        """
    )
    conn.commit()


# ------------------------------------------------------------------ рынок / сорсинг

@pytest.mark.parametrize(("hubs", "expected"), [('["jita"]', "jita"), ('["cj"]', "cj"), ("[]", "cj")])
def test_buy_hubs_limit_where_materials_are_bought(conn, hubs, expected):
    _seed(conn)
    params = core.build_params_from_config(cfg(f"[market]\nbuy_hubs = {hubs}\n"), conn)
    choice = cost.choose_hub(conn, 35, params)   # Pyerite: C-J 18 + доставка < Jita 19 + доставка
    assert choice is not None and choice.hub == expected


def test_sell_price_mode_buy_max(conn):
    _seed(conn)
    sell = core.estimate_build(conn, cfg(), 2000)
    instant = core.estimate_build(conn, cfg('[market]\nsell_price = "buy_max"\n'), 2000)
    assert sell.profit.sell_unit_price == 5000.0
    assert instant.profit.sell_unit_price == 4500.0


def test_always_build_overrides_cheaper_market(conn):
    _seed(conn)
    normal = core.estimate_build(conn, cfg(), 2100)
    assert normal.node.lines[0].source == "buy"          # на рынке 1 ISK — дешевле постройки
    forced = core.estimate_build(conn, cfg("always_build_types = [35000]\n"), 2100)
    assert forced.node.lines[0].source == "build"
    both = core.estimate_build(conn, cfg("always_build_types = [35000]\nalways_buy_types = [35000]\n"), 2100)
    assert both.node.lines[0].source == "buy"            # «всегда покупать» сильнее


def test_jump_capable_groups_from_config(conn):
    _seed(conn)
    jump = """
[[freight_routes]]
from = "gplb_c"
to = "c_j6mt"
mode = "fixed_jump"
fixed_cost = 7000000.0
vessel_capacity_m3 = 300000.0
load_factor = 1.0
"""
    base = BASE.replace('[[freight_routes]]\nfrom = "gplb_c"\nto = "c_j6mt"\nmode = "per_m3"\nisk_per_m3 = 2.0\n', "")
    c_default = config_mod.loads(base + jump)
    est = core.estimate_build(conn, c_default, 19720)
    assert not est.profit.export_is_jump                  # группа 999 не в списке по умолчанию
    c_custom = config_mod.loads("jump_capable_groups = [999]\n" + base + jump)
    est2 = core.estimate_build(conn, c_custom, 19720)
    assert est2.profit.export_is_jump and est2.profit.export_freight == 7000000.0


# ------------------------------------------------------------------ планировщик

def _job(activity_id: int, jid: int = 1) -> schedule.Job:
    return schedule.Job(jid, 2000, 1000, activity_id, "Job", 1, frozenset())


def test_science_role_is_separate_from_manufacturing(conn):
    _seed(conn)
    c = cfg("manufacturing_character_ids = [7]\nscience_character_ids = [8]\n")
    sched = schedule.schedule_jobs(conn, c, [_job(cost.INVENTION)])
    assert [it.character_id for it in sched.items] == [8]
    legacy = schedule.schedule_jobs(conn, cfg("manufacturing_character_ids = [7]\n"), [_job(cost.INVENTION)])
    assert [it.character_id for it in legacy.items] == [7]   # без списка науки — те, кто на производстве


def test_running_jobs_occupy_slots(conn):
    _seed(conn)
    end = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO character_industry_jobs(job_id,character_id,activity_id,blueprint_type_id,"
        "product_type_id,runs,status,start_date,end_date) VALUES (1,7,1,1000,2000,1,'active',?,?)",
        (end, end),
    )
    conn.commit()
    c = cfg("manufacturing_character_ids = [7]\n")
    sched = schedule.schedule_jobs(conn, c, [_job(1)])
    assert sched.items[0].start == pytest.approx(7200, abs=120)
    assert any("запущенные джобы" in w for w in sched.warnings)
    free = schedule.schedule_jobs(conn, cfg("manufacturing_character_ids = [7]\n[planner]\naccount_running_jobs = false\n"),
                                  [_job(1)])
    assert free.items[0].start == 0.0


def test_pool_for_activity_maps_esi_reactions():
    assert schedule.pool_for_activity(9) == "reaction"
    assert schedule.pool_for_activity(11) == "reaction"
    assert schedule.pool_for_activity(8) == "science"
    assert schedule.pool_for_activity(1) == "manufacturing"


# ------------------------------------------------------------------ рекомендации

def test_recommend_exclusions_and_owned_locations(conn):
    _seed(conn)
    assert set(engine.candidate_products(conn, owned=True)) == {2000, 2100}
    ex = cfg("[recommend]\nexclude_type_ids = [2100]\n")
    assert engine.candidate_products(conn, owned=True, cfg=ex) == [2000]
    exg = cfg("[recommend]\nexclude_group_ids = [25]\n")
    assert engine.candidate_products(conn, owned=False, cfg=exg) == [35000] or \
        set(engine.candidate_products(conn, owned=False, cfg=exg)) == {35000, 19720}
    exc = cfg("[recommend]\nexclude_category_ids = [6]\n")
    assert set(engine.candidate_products(conn, owned=False, cfg=exc)) == {35000}
    # «свои» — только чертежи в выбранных локациях
    assert engine.candidate_products(conn, owned=True, location_ids=[60005000]) == [2000]


# ------------------------------------------------------------------ синк

def test_market_type_ids_include_invention_inputs(conn):
    _seed(conn)
    ids = set(orchestrator.market_type_ids(conn))
    assert 20424 in ids        # датакор (материал инвенты, activity 8)
    assert 34201 in ids        # декриптор (категория Decryptors)
    assert {34, 35, 2000}.issubset(ids)


# ------------------------------------------------------------------ сервис

@pytest.fixture
def svc(tmp_path):
    (tmp_path / "forge.toml").write_text(BASE, encoding="utf-8")
    c = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(c)
    _seed(c)
    c.close()
    return ForgeService(tmp_path / "forge.toml")


def test_put_config_merges_sections_and_validates(svc):
    svc.put_config({"industry": {"broker_fee": 0.02}})
    svc.put_config({"industry": {"sales_tax": 0.03}})
    c = svc.load_cfg()
    assert c.industry.broker_fee == 0.02 and c.industry.sales_tax == 0.03   # не сбросилось
    svc.put_config({"stock": {"mode": "custom", "system_ids": [30000552]}})
    assert svc.load_cfg().stock.system_ids == [30000552]
    with pytest.raises(ServiceError):
        svc.put_config({"nonsense": 1})
    with pytest.raises(ServiceError):
        svc.put_config({"industry": {"broker_fee": "много"}})


def test_compare_candidates_from_config(svc):
    svc.put_config({"planner": {"compare_max_days": [2, 0.5]}})
    rows = svc.compare_basket(Basket(types=[2000]))["rows"]
    labels = [r["label"] for r in rows]
    assert labels == ["Без консолидации", "Консолидация, 1 поток", "Консолидация, авто-потоки",
                      "Макс. 2 дня/поток", "Макс. 12 часов/поток"]


def test_service_unknown_item_is_404(svc):
    with pytest.raises(ServiceError) as ei:
        svc.cost_basket(Basket(types=["Definitely Not A Real Item"]))
    assert ei.value.status == 404


def test_service_stock_pages_smoke(svc):
    loc = svc.stock_locations()
    assert loc["mode"] == "auto" and isinstance(loc["systems"], list)
    contents = svc.stock_contents()
    assert contents["types"] == 0 and contents["rows"] == []
