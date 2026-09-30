"""Лимиты слотов Forge по персонажам ([[planner.slot_limits]]): min(слоты по скиллам, лимит),
0 — пул исключён, с запущенными джобами — min(лимит, свободно по скиллам)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from forge import config as config_mod
from forge import core
from forge.planner import schedule

BASE = """
db_path = "x.db"
{top}
[planner]
account_running_jobs = {running}
{limits}
[locations.gplb_c]
name = "GPLB-C"
"""

ALPHA, BETA = 7, 8
MASS_PRODUCTION = 3387


def cfg(limits: str = "", top: str = "", running: str = "false") -> config_mod.Config:
    return config_mod.loads(BASE.format(top=top, running=running, limits=limits))


def lim(cid: int, **pools) -> str:
    body = "\n".join(f"{k} = {v}" for k, v in pools.items())
    return f"[[planner.slot_limits]]\ncharacter_id = {cid}\n{body}\n"


def _seed(conn, mass_production: int = 2):
    """Alpha: 1 + Mass Production = 3 слота производства; Beta — 1 слот."""
    conn.executescript(
        f"""
        INSERT INTO sde_types(type_id,name) VALUES (2000,'Widget'),({MASS_PRODUCTION},'Mass Production');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,3600);
        INSERT INTO characters(character_id,name) VALUES (7,'Alpha'),(8,'Beta');
        INSERT INTO character_skills(character_id,skill_type_id,active_level)
            VALUES (7,{MASS_PRODUCTION},{mass_production});
        """
    )
    conn.commit()


def _jobs(n: int, runs: int = 1) -> list[schedule.Job]:
    return [schedule.Job(i, 2000, 1000, 1, f"W{i}", runs, frozenset()) for i in range(n)]


def _starts(s: schedule.Schedule, cid: int) -> list[float]:
    return sorted(it.start for it in s.items if it.character_id == cid)


def test_default_is_old_schedule(conn):
    _seed(conn)
    s = schedule.schedule_jobs(conn, cfg(top="manufacturing_character_ids = [7]"), _jobs(3))
    assert _starts(s, ALPHA) == [0.0, 0.0, 0.0]                  # 3 слота по скиллам — параллельно
    unlimited = schedule.schedule_jobs(conn, cfg(lim(ALPHA, manufacturing=-1),
                                                 top="manufacturing_character_ids = [7]"), _jobs(3))
    assert [(it.character_id, it.start) for it in unlimited.items] == \
           [(it.character_id, it.start) for it in s.items]        # отрицательный — все по скиллам


def test_limit_one_makes_second_job_wait(conn):
    _seed(conn)
    s = schedule.schedule_jobs(conn, cfg(lim(ALPHA, manufacturing=1), top="manufacturing_character_ids = [7]"),
                               _jobs(2))
    assert _starts(s, ALPHA) == [0.0, pytest.approx(3600)]        # второй ждёт первый
    assert s.makespan == pytest.approx(7200)


def test_limit_zero_excludes_pool(conn):
    _seed(conn)
    s = schedule.schedule_jobs(conn, cfg(lim(ALPHA, manufacturing=0)), _jobs(2))
    assert {it.character_id for it in s.items} == {BETA}          # Alpha в производстве не участвует
    only = schedule.schedule_jobs(conn, cfg(lim(ALPHA, manufacturing=0), top="manufacturing_character_ids = [7]"),
                                  _jobs(1))
    assert only.items == []
    assert any("лимит слотов Forge 0 у: Alpha" in w for w in only.warnings)


def test_running_jobs_plus_limit(conn):
    """3 слота по скиллам, 2 заняты идущими джобами (до +2 ч и +5 ч), лимит 2: свободен 1 — Forge
    получает 1 сразу и 2-й, когда закончится самый ранний из идущих (через 2 ч), а не 3."""
    _seed(conn)
    now = datetime.now(UTC)
    for jid, hours in ((1, 2), (2, 5)):
        end = (now + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("INSERT INTO character_industry_jobs(job_id,character_id,activity_id,status,end_date) "
                     "VALUES (?,?,1,'active',?)", (jid, ALPHA, end))
    conn.commit()
    c = cfg(lim(ALPHA, manufacturing=2), top="manufacturing_character_ids = [7]", running="true")
    s = schedule.schedule_jobs(conn, c, _jobs(3, runs=10))         # каждый по 10 ч
    starts = _starts(s, ALPHA)
    assert starts[0] == 0.0
    assert starts[1] == pytest.approx(2 * 3600, abs=120)            # освободился слот идущего джоба
    assert starts[2] == pytest.approx(10 * 3600, abs=120)           # больше 2 одновременно — нельзя
    # без лимита: слот идущего (+5 ч) тоже достаётся Forge
    free = schedule.schedule_jobs(conn, cfg(top="manufacturing_character_ids = [7]", running="true"),
                                  _jobs(3, runs=10))
    assert _starts(free, ALPHA)[2] == pytest.approx(5 * 3600, abs=120)


def test_apply_slot_limits_keeps_earliest_slots():
    avail = {7: {"manufacturing": [0.0, 500.0, 0.0, 100.0], "reaction": [0.0], "science": [0.0, 0.0]}}
    schedule.apply_slot_limits(avail, cfg(lim(7, manufacturing=2, science=0)))
    assert avail[7] == {"manufacturing": [0.0, 0.0], "reaction": [0.0], "science": []}


def test_slot_limit_lookup_and_science_candidates(conn):
    _seed(conn)
    c = cfg(lim(ALPHA, science=0, manufacturing=3))
    assert c.planner.slot_limit(ALPHA, "science") == 0
    assert c.planner.slot_limit(ALPHA, "manufacturing") == 3
    assert c.planner.slot_limit(ALPHA, "reaction") is None
    assert c.planner.slot_limit(BETA, "science") is None
    assert core.science_character_ids(c, conn) == [BETA]           # лимит науки 0 — не инвентор
    assert config_mod.Config().planner.slot_limits == []
