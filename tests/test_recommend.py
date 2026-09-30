"""recommend: нормализация, ликвидность, фильтры и ранжирование."""

from __future__ import annotations

from forge import config as config_mod
from forge import recommend as rec
from forge.recommend.engine import (
    _normalize,
    _winsorize,
    candidate_products,
    daily_volume,
)

CJ_REGION = 10000009

CFG = config_mod.loads(
    """
db_path = "x.db"
[industry]
[recommend]
w_roi = 1.0
w_isk_hour = 1.0
w_liquidity = 0.0
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
"""
)


def _seed(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (34,'Tritanium',0.01);
        INSERT INTO sde_types(type_id,name) VALUES (2000,'Alpha'),(2001,'Beta');
        -- Alpha: дёшево (10 трита), Beta: дорого (100 трита); обе продаются по 1000
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1001,1,2001,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,10),(1001,1,34,100);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,600),(1001,1,600);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,1,0.05);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES
            (34,10000009,5.0,4.0,1000000),
            (2000,10000009,1000.0,900.0,5000),
            (2001,10000009,1000.0,900.0,5000);
        -- персонаж владеет обоими чертежами
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0),(2,7,1001,0,0,-1,-1,0);
        """
    )
    conn.commit()


def test_normalize():
    assert _normalize([1.0, 1.0]) == [0.5, 0.5]
    assert _normalize([0.0, 10.0]) == [0.0, 1.0]


def test_candidate_products_owned(conn):
    _seed(conn)
    cands = set(candidate_products(conn, owned=True))
    assert cands == {2000, 2001}


def test_candidate_products_group_filter(conn):
    _seed(conn)
    conn.execute("UPDATE sde_types SET group_id = 500 WHERE type_id = 2000")
    conn.execute("UPDATE sde_types SET group_id = 501 WHERE type_id = 2001")
    conn.commit()
    assert set(candidate_products(conn, owned=True, group_ids=[500])) == {2000}
    assert set(candidate_products(conn, owned=True, group_ids=[500, 501])) == {2000, 2001}
    assert set(candidate_products(conn, owned=True, group_ids=[999])) == set()


def test_daily_volume_snapshot_fallback(conn):
    _seed(conn)
    assert daily_volume(conn, 2000, CJ_REGION) == 5000.0


def test_recommend_ranks_cheaper_higher_roi_first(conn):
    _seed(conn)
    recs = rec.recommend(conn, CFG, owned=True, runs=1, top=10)
    assert [r.product_type_id for r in recs] == [2000, 2001]  # Alpha дешевле → выше ROI → первый
    assert recs[0].roi > recs[1].roi


def test_recommend_budget_filter(conn):
    _seed(conn)
    recs = rec.recommend(conn, CFG, owned=True, runs=1, budget=100.0, top=10)
    # Beta (100×5=500 + джоб) дороже бюджета 100 → отсекается; Alpha остаётся
    ids = [r.product_type_id for r in recs]
    assert 2000 in ids and 2001 not in ids


def test_winsorize_clips_outliers():
    w = _winsorize([1.0, 2.0, 3.0, 4.0, 1000.0], 0.2)
    assert max(w) == 4.0 and min(w) == 1.0  # выброс 1000 подрезан к 4.0


def test_recommend_excludes_broken_blueprint(conn):
    """Битый чертёж (1× Tritanium при дорогой цене продажи) — себестоимость << цены → исключён."""
    _seed(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (2002,'Broken');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1002,1,2002,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1002,1,34,1);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1002,1,600);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES (2002,10000009,1000000.0,900000.0,5000);
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (3,7,1002,0,0,-1,-1,0);
        """
    )
    conn.commit()
    ids = [r.product_type_id for r in rec.recommend(conn, CFG, owned=True, runs=1, top=10)]
    assert 2002 not in ids and 2000 in ids and 2001 in ids


def test_buy_cheaper_finds_overpriced_builds(conn):
    """Находит предметы, где рынок дешевле себестоимости; обычные (выгодно строить) и
    артефакты SDE (экономия ~100%) — исключает."""
    _seed(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (2004,'BuyMe'),(2005,'Artifact');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1004,1,2004,1,1.0),(1005,1,2005,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1004,1,34,1000),(1005,1,34,1000000);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1004,1,600),(1005,1,600);
        -- оба продаются по 1000 (купить дёшево), но строить дорого: 1000 трита vs 1млн трита
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES
            (2004,10000009,1000.0,900.0,5000),
            (2005,10000009,1000.0,900.0,5000);
        """
    )
    conn.commit()
    deals = rec.buy_cheaper(conn, CFG, owned=False, min_volume=0, top=50)
    ids = {d.product_type_id for d in deals}
    assert 2004 in ids                          # строить ~5150 > купить 1000 → дешевле купить
    assert 2000 not in ids and 2001 not in ids  # их выгоднее строить
    assert 2005 not in ids                       # ~100% «экономии» — артефакт, отсечён max_savings_pct
    d = next(x for x in deals if x.product_type_id == 2004)
    assert d.buy_unit < d.build_unit and 0 < d.savings_pct <= 0.9 and d.buy_hub == "cj"


def test_buy_cheaper_group_filter(conn):
    """Фильтр по EVE-группам в скане «дешевле купить»: сканируются только выбранные группы."""
    _seed(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,group_id) VALUES (2006,'GrpBuy',600);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1006,1,2006,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1006,1,34,1000);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1006,1,600);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES (2006,10000009,1000.0,900.0,5000);
        """
    )
    conn.commit()
    assert {d.product_type_id for d in rec.buy_cheaper(conn, CFG, owned=False, min_volume=0, group_ids=[600])} == {2006}
    assert rec.buy_cheaper(conn, CFG, owned=False, min_volume=0, group_ids=[999]) == []  # пустая группа


def test_recommend_excludes_no_cj_price(conn):
    """Нет цены в C-J6MT → нет выручки → предмет не попадает в ТОП (без отката на Jita)."""
    _seed(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (2003,'JitaOnly');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1003,1,2003,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1003,1,34,100);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1003,1,600);
        -- цена только в Jita (10000002), в C-J (10000009) нет
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES (2003,10000002,1000.0,900.0,5000);
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (4,7,1003,0,0,-1,-1,0);
        """
    )
    conn.commit()
    ids = [r.product_type_id for r in rec.recommend(conn, CFG, owned=True, runs=1, top=10)]
    assert 2003 not in ids
