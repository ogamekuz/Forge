"""Копирование T1-чертежа для инвенты как джобы расписания ([planner] schedule_copy_jobs)."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core
from forge.core import cost, sourcing
from forge.planner import schedule, schedule_basket, timing
from forge.web import report

BASE = """
db_path = "x.db"
[industry]
invention_use_skills = false
[planner]
account_running_jobs = false
schedule_copy_jobs = {flag}
[locations.jita]
name = "Jita"
region_id = 10000002
[locations.c_j6mt]
name = "C-J6MT"
region_id = 10000009
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006
{extra}
"""

T1, T2BP, T2 = 2184, 2186, 2185          # T1-чертёж → (инвента) T2-чертёж → T2-продукт
T2BP_B, T2_B = 2196, 2195                # второй T2 с того же T1 (общий BPO)
COPY_BASE, INV_BASE = 1000, 3600
SCIENCE, ADV_IND = 4, 5


def cfg(flag: bool = True, extra: str = "") -> config_mod.Config:
    return config_mod.loads(BASE.format(flag=str(flag).lower(), extra=extra))


def _seed(conn, bpos: int = 1):
    conn.executescript(
        f"""
        INSERT INTO sde_types(type_id,name,volume) VALUES (1001,'Datacore',0.0),(2185,'T2 Item',1.0),
            (2195,'T2 Item B',1.0),(40,'Mineral',0.01),(41,'T1 Mineral',0.01),(2184,'T1 Blueprint',0.01),
            (500,'T1 Item',1.0),(3402,'Science',0.0),(3406,'Laboratory Operation',0.0),
            (3388,'Advanced Industry',0.0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (1001,10000009,1000.0,100000),(40,10000009,10.0,10000000);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES ({T1},8,{T2BP},1,0.5),({T1},8,{T2BP_B},1,0.5),({T2BP},1,{T2},1,1.0),
                   ({T2BP_B},1,{T2_B},1,1.0),({T1},1,500,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES ({T1},8,1001,1),({T2BP},1,40,100),({T2BP_B},1,40,100),({T1},1,41,1000);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES
            ({T1},5,{COPY_BASE}),({T1},8,{INV_BASE}),({T2BP},1,600),({T2BP_B},1,600),({T1},1,500);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (41,500.0),(40,10.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES
            (30000552,5,0.05),(30000552,8,0.05),(30000552,1,0.05);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_skills(character_id,skill_type_id,active_level)
            VALUES (7,3406,5),(7,3402,{SCIENCE}),(7,3388,{ADV_IND});
        """
    )
    for i in range(bpos):   # свои T1 BPO; TE 20 — на копирование не влияет
        conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                     "VALUES (?,7,?,10,20,-1,-1,0)", (100 + i, T1))
    conn.commit()


def _node(conn, c, product=T2, runs=1):
    return sourcing.build_node_cost(conn, product, runs, 1, core.build_params_from_config(c, conn),
                                    allow_build=False)


def _sched(conn, c, products=(T2,)):
    nodes = [_node(conn, c, p) for p in products]
    return schedule_basket(conn, c, nodes), nodes


def _copy_seconds(copies: int, runs: int = 1) -> float:
    return COPY_BASE * copies * runs * (1 - 0.05 * SCIENCE) * (1 - 0.03 * ADV_IND)


def test_node_knows_t1_comes_from_own_bpo(conn):
    _seed(conn, bpos=2)
    n = _node(conn, cfg())
    assert n.blueprint_source == "invention" and n.invention_t1_kind == "bpo"
    assert n.invention_t1_bpos == 2 and n.invention_t1_copy > 0
    assert n.invention_t1_copy == pytest.approx(n.invention_breakdown["t1_copy"])
    assert n.invention_breakdown["attempts"] == 2                    # ceil(1/0.5)


def test_copy_jobs_only_with_flag_and_default_schedule_unchanged(conn):
    _seed(conn)
    off, _ = _sched(conn, cfg(flag=False))
    assert not any(it.activity_id == cost.COPYING for it in off.items)
    default, _ = _sched(conn, config_mod.loads(BASE.format(flag="false", extra="")))
    assert [(it.name, it.start, it.end) for it in off.items] == [(it.name, it.start, it.end) for it in default.items]
    assert config_mod.Config().planner.schedule_copy_jobs is False
    on, _ = _sched(conn, cfg(flag=True))
    copies = [it for it in on.items if it.activity_id == cost.COPYING]
    assert len(copies) == 1 and copies[0].name == "Копия: T1 Blueprint"
    assert copies[0].runs == 2 and copies[0].copy_runs == 1 and copies[0].pool == "science"


def test_invention_waits_for_copies_and_copy_time_has_no_te(conn):
    _seed(conn)
    s, _ = _sched(conn, cfg())
    copy = next(it for it in s.items if it.activity_id == cost.COPYING)
    inv = [it for it in s.items if it.activity_id == cost.INVENTION]
    assert copy.start == 0.0 and copy.te == 0
    assert copy.end == pytest.approx(_copy_seconds(2))               # TE 20 BPO не ускоряет копию
    assert len(inv) == 2 and all(it.start >= copy.end for it in inv)  # инвента ждёт копии
    off, _ = _sched(conn, cfg(flag=False))
    assert s.makespan == pytest.approx(off.makespan + copy.end)      # время копий входит в срок


def test_one_bpo_copies_are_not_parallel(conn):
    _seed(conn, bpos=1)
    s, _ = _sched(conn, cfg(), products=(T2, T2_B))
    copies = sorted((it for it in s.items if it.activity_id == cost.COPYING), key=lambda x: x.start)
    assert len(copies) == 2
    assert copies[1].start == pytest.approx(copies[0].end)           # слоты науки есть, BPO — один


def test_two_bpos_copy_in_parallel(conn):
    _seed(conn, bpos=2)
    s, _ = _sched(conn, cfg(), products=(T2, T2_B))
    copies = [it for it in s.items if it.activity_id == cost.COPYING]
    # на каждый продукт — 2 копии на 2 BPO: по копи-джобу на BPO, все четыре джоба ≤ 2 параллельно
    assert len(copies) == 4
    starts = sorted(it.start for it in copies)
    assert starts[:2] == [0.0, 0.0] and starts[2] > 0.0


def test_plan_copy_jobs_groups_and_splits():
    assert schedule.plan_copy_jobs([1] * 6, 1) == [(6, 1, [0, 1, 2, 3, 4, 5])]
    assert schedule.plan_copy_jobs([1] * 6, 2) == [(3, 1, [0, 1, 2]), (3, 1, [3, 4, 5])]
    assert schedule.plan_copy_jobs([2, 2, 1], 1) == [(2, 2, [0, 1]), (1, 1, [2])]   # копии — с ранами джоба


def test_manual_t1_price_means_no_copy_jobs(conn):
    _seed(conn)
    c = cfg(extra=f"[[blueprint_overrides]]\ntype_id = {T1}\nper_run = 5000.0\n")
    n = _node(conn, c)
    assert n.invention_t1_kind == "manual"
    s, _ = _sched(conn, c)
    assert not any(it.activity_id == cost.COPYING for it in s.items)


def test_shortfall_invention_also_gets_copies(conn):
    _seed(conn)
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 f"VALUES (200,7,{T2BP},2,4,-2,1,1)")    # своя T2-копия на 1 прогон, нужно 3
    conn.commit()
    c = cfg()
    n = _node(conn, c, runs=3)
    assert n.blueprint_source == "owned_bpc_insufficient" and n.invention_t1_kind == "bpo"
    s = schedule_basket(conn, c, [n])
    copy = next(it for it in s.items if it.activity_id == cost.COPYING)
    inv = [it for it in s.items if it.activity_id == cost.INVENTION]
    assert inv and all(it.start >= copy.end for it in inv)
    assert all(it.name.startswith("Инвента (недостача)") for it in inv)


def test_job_seconds_copying_uses_science_not_te():
    base = timing.job_seconds(1000, 3, cost.COPYING, te=20, industry_level=5, adv_industry_level=0)
    assert base == pytest.approx(3000)                               # ни TE, ни Industry
    sk = timing.job_seconds(1000, 3, cost.COPYING, te=0, industry_level=0, adv_industry_level=5,
                            science_level=4)
    assert sk == pytest.approx(3000 * 0.85 * 0.8)
    # производство — с TE, Science на него не влияет
    assert timing.job_seconds(1000, 1, 1, te=20, industry_level=0, adv_industry_level=0,
                              science_level=5) == pytest.approx(800)


def test_report_copy_rows_carry_copy_cost_without_double_counting(conn):
    _seed(conn)
    c = cfg()
    p = report.build_report_payload(conn, c, [(T2, 1, 1)], consolidate=False)
    rows = p["jobs"]
    copy_rows = [j for j in rows if j["activity_id"] == cost.COPYING]
    inv_rows = [j for j in rows if j["activity_id"] == cost.INVENTION]
    assert len(copy_rows) == 1 and copy_rows[0]["resource_name"] == "T1 Blueprint"
    n = _node(conn, c)
    t1 = n.invention_breakdown["t1_copy"]
    job_fee = n.invention_breakdown["job_fee"]
    assert copy_rows[0]["plan_cost"] == pytest.approx(t1 * 2)             # t1_copy × копий
    assert sum(j["plan_cost"] for j in inv_rows) == pytest.approx(job_fee * 2)
    bp_bucket = next(b for b in p["cost_control"]["buckets"] if b["key"] == "blueprints")
    # статья «Чертежи/инвента» = сумма строк копий + инвенты = blueprint_cost узла — без задвоения
    assert bp_bucket["plan"] == pytest.approx(n.blueprint_cost)
    assert sum(j["plan_cost"] for j in copy_rows + inv_rows) == pytest.approx(n.blueprint_cost)
    off = report.build_report_payload(conn, cfg(flag=False), [(T2, 1, 1)], consolidate=False)
    assert next(b for b in off["cost_control"]["buckets"] if b["key"] == "blueprints")["plan"] == \
        pytest.approx(bp_bucket["plan"])
    steps = [s["text"] for ch in p["characters"] for s in ch["steps"]]
    assert any(t.startswith("Запусти копирование: T1 Blueprint — 2 коп. × 1 прог.") for t in steps)
