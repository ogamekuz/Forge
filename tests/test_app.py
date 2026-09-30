"""forge.web.app — FastAPI-роуты поверх core/planner (Фаза 2).

Каждый роут открывает СВОЁ sqlite-соединение через ``ctx()`` на каждый запрос (см.
``create_app``), поэтому ``:memory:`` (как у остальных тестов, см. conftest.py) не годится —
БД нужна файловая, чтобы данные переживали между внутренними ``open_conn`` вызовами одного
запроса и между запросами теста. ``config_path`` может не существовать вообще (``load_cfg``
ловит ``FileNotFoundError`` и падает на ``Config()`` по умолчанию) — тестовый конфиг пишем
явно, чтобы задать ``db_path``/локации/фрахт, как и остальные core-тесты (см. test_core.py).
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from forge import config as config_mod
from forge import core, storage
from forge.web import create_app

CFG_TOML = """
db_path = "forge.db"
[industry]
rig_material_mult = 1.0
rig_cost_mult = 1.0
facility_tax = 0.0
scc_surcharge = 0.0
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


def _seed(conn: sqlite3.Connection) -> None:
    """Widget(2000): 100x Tritanium(34) + 50x Pyerite(35) — тот же минимальный кейс, что и
    core/test_core.py, чтобы сверять ответы API с прямым расчётом через ``core`` напрямую."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id, name, volume) VALUES
            (34,'Tritanium',0.01),(35,'Pyerite',0.01),(2000,'Widget',5.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,100),(1000,1,35,50);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0),(35,18.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,1,0.05);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max) VALUES
            (34,10000002,5.0,4.0),
            (35,10000002,19.0,18.0),(35,10000009,18.0,17.0),
            (2000,10000009,5000.0,4500.0);
        """
    )
    conn.commit()


@pytest.fixture
def client(tmp_path) -> TestClient:
    config_path = tmp_path / "forge.toml"
    config_path.write_text(CFG_TOML, encoding="utf-8")

    db_path = tmp_path / "forge.db"
    conn = storage.connect(str(db_path))
    storage.init_db(conn)
    _seed(conn)
    conn.close()

    app = create_app(str(config_path))
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_search_finds_seeded_type_by_name(client: TestClient):
    res = client.get("/api/search", params={"q": "Widget"})
    assert res.status_code == 200
    rows = res.json()
    assert any(r["type_id"] == 2000 and r["name"] == "Widget" for r in rows)


def test_search_unknown_name_returns_empty(client: TestClient):
    res = client.get("/api/search", params={"q": "Nonexistent Zzyzx"})
    assert res.status_code == 200
    assert res.json() == []


def test_resolve_names_exact_case_insensitive_match(client: TestClient):
    res = client.post("/api/resolve-names", json={"names": ["widget", "Tritanium", "Ghost Item"]})
    assert res.status_code == 200
    rows = res.json()
    by_name = {r["name"]: r for r in rows}
    assert by_name["widget"]["type_id"] == 2000
    assert by_name["Tritanium"]["type_id"] == 34
    assert by_name["Ghost Item"]["type_id"] is None  # не найден — не падает, просто null


def test_cost_basket_matches_direct_core_calculation(client: TestClient):
    """API — тонкая обёртка: /api/cost-basket на 3 прогона Widget обязан дать РОВНО ту же
    total_cost/material_cost, что и core.estimate_build напрямую (ту же формулу, что уже
    проверяют core-тесты) — не только «не упал», а совпадение с источником истины."""
    res = client.get("/api/cost-basket", params={"types": "2000", "runs": "3"})
    assert res.status_code == 200
    data = res.json()
    assert data["items"][0]["node"]["produced"] == 3

    cfg = config_mod.loads(CFG_TOML)
    conn = storage.connect(":memory:")
    storage.init_db(conn)
    _seed(conn)
    config_mod.resolve_locations(cfg, conn)
    est = core.estimate_build(conn, cfg, 2000, runs=3, streams=1, whole_blueprint=True)
    conn.close()

    assert data["totals"]["total_cost"] == pytest.approx(est.node.total_cost)
    assert data["totals"]["material_cost"] == pytest.approx(est.node.material_cost)
    assert data["items"][0]["node"]["unit_cost"] == pytest.approx(est.node.unit_cost)


def test_cost_basket_unknown_type_returns_404(client: TestClient):
    res = client.get("/api/cost-basket", params={"types": "Definitely Not A Real Item"})
    assert res.status_code == 404


def test_plan_basket_returns_schedule_with_jobs(client: TestClient):
    res = client.get("/api/plan-basket", params={"types": "2000", "runs": "3"})
    assert res.status_code == 200
    sched = res.json()["schedule"]
    assert len(sched["items"]) == 1
    job = sched["items"][0]
    assert job["product_type_id"] == 2000
    assert job["runs"] == 3


def test_config_get_returns_defaults_then_put_persists_changes(client: TestClient, tmp_path):
    res = client.get("/api/config")
    assert res.status_code == 200
    assert res.json()["industry"]["broker_fee"] == 0.0

    put = client.put("/api/config", json={"industry": {"broker_fee": 0.025}})
    assert put.status_code == 200
    assert put.json() == {"ok": True}

    # Персистится на диск — перечитываем конфиг-файл напрямую, не только через GET.
    saved = config_mod.load(str(tmp_path / "forge.toml"))
    assert saved.industry.broker_fee == pytest.approx(0.025)

    res2 = client.get("/api/config")
    assert res2.json()["industry"]["broker_fee"] == pytest.approx(0.025)
