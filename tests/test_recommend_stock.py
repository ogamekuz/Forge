"""recommend.stock: рекомендации «что строить», приоритет — использование остатков склада."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import recommend as rec

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
[structures]
gplb_engineering_complex_id = 60005000
"""
)


def _seed(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (34,'Tritanium',0.01);
        INSERT INTO sde_types(type_id,name) VALUES (2000,'Alpha'),(2001,'Beta');
        -- Alpha: 10 трита/прогон, Beta: 100 трита/прогон — обе продаются по 1000
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
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0),(2,7,1001,0,0,-1,-1,0);
        """
    )
    conn.commit()


def test_recommend_by_stock_only_shows_items_overlapping_stock(conn):
    """Без остатков на складе — ни один кандидат не проходит фильтр (это вкладка ИМЕННО про
    использование остатков, не дублирует обычный ``recommend``)."""
    _seed(conn)
    assert rec.recommend_by_stock(conn, CFG, top=10) == []


def test_recommend_by_stock_computes_real_profit_and_utilization(conn):
    """5 из 10 нужных Тританиума уже на складе GPLB-C — реальный расход и прибыль должны
    учитывать это (не полное замещение, как в обычном ``core.estimate_build``/``engine.recommend``)."""
    _seed(conn)
    conn.execute(
        "INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) "
        "VALUES (900,7,34,60005000,5)"
    )
    conn.commit()
    recs = rec.recommend_by_stock(conn, CFG, top=10)
    ids = [r.product_type_id for r in recs]
    assert 2000 in ids and 2001 in ids   # оба используют Тританиум — оба проходят фильтр

    plain = {r.product_type_id: r for r in rec.recommend(conn, CFG, owned=True, runs=1, top=10)}
    alpha = next(r for r in recs if r.product_type_id == 2000)
    # Alpha: нужно 10 трита, 5 со склада, landed-цена трита = 5.0 (sell_min C-J6MT, фрахт=0).
    assert alpha.stock_value == pytest.approx(5 * 5.0)                          # 5 шт. × 5.0 ISK
    assert alpha.capital == pytest.approx(plain[2000].capital)                  # полная себестоимость та же
    assert alpha.real_capital == pytest.approx(alpha.capital - alpha.stock_value)
    # реальная прибыль ВЫШЕ обычной (полностью-платной) РОВНО на ISK-стоимость списанного склада —
    # это и есть смысл вкладки: не «магическая» прибыль, а честно посчитанная экономия.
    # produced=1 в этом фикстуре (product qty=1/run, runs=1) => unit_profit == вся прибыль партии.
    assert alpha.real_profit == pytest.approx(plain[2000].unit_profit + alpha.stock_value, rel=1e-6)
    assert alpha.stock_utilization == pytest.approx(alpha.stock_value / alpha.capital)

    beta = next(r for r in recs if r.product_type_id == 2001)
    # Beta нужно 100 трита — ТЕ ЖЕ 5 шт. со склада (независимая оценка на кандидата, копия
    # остатков не делится МЕЖДУ кандидатами) — доля покрытия у Beta МЕНЬШЕ, чем у Alpha.
    assert beta.stock_value == pytest.approx(alpha.stock_value)
    assert beta.stock_utilization < alpha.stock_utilization


def test_recommend_by_stock_auto_scales_runs_to_exhaust_stock(conn):
    """Нет ручного «прогонов» — при избытке остатка партия должна сама вырасти, чтобы вычерпать
    самый дефицитный пересекающийся со складом материал, а не остановиться на 1 прогоне."""
    _seed(conn)
    # Alpha: 10 трита/прогон. 55 на складе => 55/10 = 5.5 => 5 прогонов (не 1, не 6).
    conn.execute(
        "INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) "
        "VALUES (900,7,34,60005000,55)"
    )
    conn.commit()
    recs = rec.recommend_by_stock(conn, CFG, top=10)
    alpha = next(r for r in recs if r.product_type_id == 2000)
    assert alpha.runs == 5
    assert alpha.stock_value == pytest.approx(50 * 5.0)   # 5 прогонов * 10 трита * 5.0 ISK
    beta = next(r for r in recs if r.product_type_id == 2001)
    # Beta: 100 трита/прогон, 55 на складе => 55/100 = 0.55 => floor => 0 => зажимается до 1.
    assert beta.runs == 1


def test_recommend_by_stock_caps_runs_at_max_production_limit(conn):
    """Реальный лимит EVE на прогоны в одном джобе (``sde_blueprints.max_production_limit``)
    должен резать авто-подобранную партию — иначе обильный склад минералов даёт нереализуемо
    большое число прогонов (в игре просто нельзя поставить больше лимита в один джоб)."""
    _seed(conn)
    conn.execute("INSERT INTO sde_blueprints(blueprint_type_id,max_production_limit) VALUES (1000,3)")
    conn.execute(
        "INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) "
        "VALUES (900,7,34,60005000,1000)"  # хватило бы на 100 прогонов Alpha без лимита
    )
    conn.commit()
    recs = rec.recommend_by_stock(conn, CFG, top=10)
    alpha = next(r for r in recs if r.product_type_id == 2000)
    assert alpha.runs == 3   # зажато лимитом чертежа, а не 100


def test_recommend_by_stock_excludes_item_without_stock_overlap(conn):
    """Остаток есть только по Тританиуму — кандидат, который его вообще не использует
    (другой материал), не проходит фильтр ``stock_value > 0``."""
    _seed(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (35,'Pyerite'),(2002,'Gamma');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1002,1,2002,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1002,1,35,10);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1002,1,600);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (35,3.0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES
            (35,10000009,5.0,4.0,1000000),(2002,10000009,1000.0,900.0,5000);
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (3,7,1002,0,0,-1,-1,0);
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity)
            VALUES (900,7,34,60005000,5);
        """
    )
    conn.commit()
    ids = [r.product_type_id for r in rec.recommend_by_stock(conn, CFG, top=10)]
    assert 2002 not in ids       # Gamma не тянет Тританиум — остаток ей не помогает
    assert 2000 in ids and 2001 in ids
