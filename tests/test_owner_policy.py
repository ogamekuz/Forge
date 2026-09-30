"""Расписание: приоритет владельца чертежа ([planner] owner_policy / owner_slack_hours)."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge.planner import schedule
from forge.web import serializers

BASE = """
db_path = "x.db"
{top}
[planner]
account_running_jobs = false
{planner}
[locations.gplb_c]
name = "GPLB-C"
"""

ALPHA, BETA = 7, 8   # Alpha владеет чертежами X и Y; Beta — ничем


def cfg(top: str = "", planner: str = "") -> config_mod.Config:
    return config_mod.loads(BASE.format(top=top, planner=planner))


def _seed(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (2000,'Y'),(2100,'X');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1100,1,2100,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,3600),(1100,1,3600);
        INSERT INTO characters(character_id,name) VALUES (7,'Alpha'),(8,'Beta');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0),(2,7,1100,0,0,-1,-1,0);
        """
    )
    conn.commit()


def _jobs() -> list[schedule.Job]:
    # Y: 10 ч — занимает единственный слот Alpha; X: 1 ч — у Beta закончится через 1 ч,
    # у владельца Alpha — только через 11 ч (после Y).
    return [schedule.Job(1, 2000, 1000, 1, "Y", 10, frozenset()),
            schedule.Job(2, 2100, 1100, 1, "X", 1, frozenset())]


def _who(sched: schedule.Schedule, name: str) -> schedule.Scheduled:
    return next(it for it in sched.items if it.name == name)


def test_any_is_old_schedule(conn):
    _seed(conn)
    default = schedule.schedule_jobs(conn, cfg(), _jobs())
    explicit = schedule.schedule_jobs(conn, cfg(planner='owner_policy = "any"'), _jobs())
    assert [(it.character_id, it.start, it.end) for it in default.items] == \
           [(it.character_id, it.start, it.end) for it in explicit.items]
    x = _who(default, "X")
    assert x.character_id == BETA and x.end == pytest.approx(3600)       # самое раннее окончание
    assert x.owner_status == "transfer" and x.owners == ("Alpha",)
    assert _who(default, "Y").owner_status == "owner"
    assert default.transfer_jobs == 1
    assert any(w.startswith("Нужна передача") and "X" in w for w in default.warnings)


def test_prefer_owner_within_slack_goes_to_owner(conn):
    _seed(conn)
    s = schedule.schedule_jobs(conn, cfg(planner='owner_policy = "prefer_owner"\nowner_slack_hours = 24'),
                               _jobs())
    x = _who(s, "X")
    assert x.character_id == ALPHA and x.end == pytest.approx(11 * 3600)   # 11 ч ≤ 1 ч + 24 ч
    assert x.owner_status == "owner" and s.transfer_jobs == 0
    assert not any(w.startswith("Нужна передача") for w in s.warnings)
    assert s.makespan == pytest.approx(11 * 3600)


def test_prefer_owner_beyond_slack_takes_earliest(conn):
    _seed(conn)
    s = schedule.schedule_jobs(conn, cfg(planner='owner_policy = "prefer_owner"\nowner_slack_hours = 5'),
                               _jobs())
    assert _who(s, "X").character_id == BETA                                # 11 ч > 1 ч + 5 ч
    assert s.transfer_jobs == 1


def test_owner_only_waits_for_owner(conn):
    _seed(conn)
    s = schedule.schedule_jobs(conn, cfg(planner='owner_policy = "owner_only"\nowner_slack_hours = 0'), _jobs())
    assert _who(s, "X").character_id == ALPHA and s.transfer_jobs == 0


def test_owner_only_falls_back_with_warning_when_owner_not_in_role(conn):
    _seed(conn)
    s = schedule.schedule_jobs(conn, cfg("manufacturing_character_ids = [8]", 'owner_policy = "owner_only"'),
                               _jobs())
    assert {it.character_id for it in s.items} == {BETA}
    assert any("«Только владелец»" in w and "X" in w and "Y" in w for w in s.warnings)
    assert s.transfer_jobs == 2


def test_no_owner_at_all_is_not_a_transfer(conn):
    _seed(conn)
    conn.execute("DELETE FROM character_blueprints")
    conn.commit()
    s = schedule.schedule_jobs(conn, cfg(planner='owner_policy = "owner_only"'), _jobs())
    assert all(it.owner_status == "" for it in s.items) and s.transfer_jobs == 0
    assert not any("«Только владелец»" in w for w in s.warnings)


def test_pick_candidate_unit():
    cands = [(100.0, 0.0, 8, "Beta", 0, 0), (130.0, 0.0, 7, "Alpha", 0, 0)]
    assert schedule.pick_candidate(cands, {7}, "any", 0)[0][2] == 8
    assert schedule.pick_candidate(cands, {7}, "prefer_owner", 30)[0][2] == 7
    assert schedule.pick_candidate(cands, {7}, "prefer_owner", 29.9)[0][2] == 8
    assert schedule.pick_candidate(cands, {7}, "owner_only", 0) == (cands[1], False)
    assert schedule.pick_candidate(cands, {9}, "owner_only", 0) == (cands[0], True)
    assert schedule.pick_candidate(cands, set(), "owner_only", 0) == (cands[0], False)


def test_serializer_exposes_owner_status(conn):
    _seed(conn)
    d = serializers.schedule_to_dict(schedule.schedule_jobs(conn, cfg(), _jobs()))
    assert d["transfer_jobs"] == 1
    x = next(it for it in d["items"] if it["name"] == "X")
    assert x["owner_status"] == "transfer" and x["owners"] == ["Alpha"]
