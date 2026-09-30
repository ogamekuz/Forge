"""core: количества (ME/потоки), EIV, landed cost, make-or-buy, прибыль."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core
from forge.core import bom, cost, prices
from forge.core.bom import MANUFACTURING, REACTION

JITA_REGION = 10000002
CJ_REGION = 10000009
GPLB_SYSTEM = 30000552

CFG = config_mod.loads(
    """
db_path = "x.db"
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
)


def _seed(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id, name, volume) VALUES
            (34,'Tritanium',0.01),(35,'Pyerite',0.01),
            (2000,'Widget',5.0),(35000,'Subcomponent',1.0);
        -- Widget (2000): из 34 и 35
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,100),(1000,1,35,50);
        -- adjusted prices (для EIV)
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0),(35,18.0);
        -- cost index GPLB-C, производство
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,1,0.05);
        -- рыночные снапшоты
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max) VALUES
            (34,10000002,5.0,4.0),
            (35,10000002,19.0,18.0),(35,10000009,18.0,17.0),
            (2000,10000009,5000.0,4500.0);
        """
    )
    conn.commit()


def test_split_runs():
    assert cost.split_runs(10, 1) == [10]
    assert cost.split_runs(10, 2) == [5, 5]
    assert cost.split_runs(10, 3) == [4, 3, 3]
    assert cost.split_runs(2, 5) == [1, 1]  # лишние потоки отброшены


def test_material_quantity_me_and_streams():
    # base=3, runs=10, ME10 -> 1 поток: ceil(27)=27; 2 потока: 14+14=28 (округление на джоб)
    assert cost.material_quantity(3, [10], 10, 1.0, MANUFACTURING) == 27
    assert cost.material_quantity(3, [5, 5], 10, 1.0, MANUFACTURING) == 28
    # минимум 1 на прогон
    assert cost.material_quantity(1, [10], 10, 1.0, MANUFACTURING) == 10
    # реакции игнорируют ME
    assert cost.material_quantity(3, [10], 10, 1.0, REACTION) == 30


def test_material_quantity_rounds_before_ceil():
    # EVE округляет произведение модификаторов до 2 знаков ПЕРЕД ceil.
    # base=2, 19 прогонов, ME1%, риг 0.957: 2*19*0.99*0.957 = 36.00234 -> round 36.00 -> 36,
    # а не ceil(36.00234)=37 (классический off-by-one из-за float).
    assert cost.material_quantity(2, [19], 1, 0.957, MANUFACTURING) == 36
    # без округления было бы 37; убеждаемся что пол max(r, …) тут не задействован
    assert cost.material_quantity(2, [19], 1, 0.957, MANUFACTURING) > 19


def test_substream_fn_splits_only_subcomponents(conn):
    """«Макс. дней/поток» (substream_fn) дробит ПОД-компоненты → больше материала из-за ceil
    на каждый джоб; верхний продукт остаётся на явных ``streams``."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',5.0),(35000,'Sub',1.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1035,1,35000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,35000,10),(1035,1,34,7);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (34,10000002,5.0);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    n1 = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=10)
    fn = lambda ptid, runs: 10 if ptid == 35000 else 1   # дробить только Sub
    n10 = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=10, substream_fn=fn)

    def trit_qty(node):
        sub = next(l for l in node.lines if l.type_id == 35000)
        return next(l for l in sub.child.lines if l.type_id == 34).quantity

    # Widget (ME10) требует ceil(10*0.9)=9 шт Sub → у Sub 9 прогонов:
    #   1 поток   → ceil(7*9*0.9)=ceil(56.7)=57
    #   на потоки → 9 джобов по 1 прогону: 9*ceil(7*0.9)=9*7=63
    assert trit_qty(n1) == 57
    assert trit_qty(n10) == 63
    assert n1.streams == 1 and n10.streams == 1   # верхний продукт substream_fn не трогает


def test_me_overrides_keyed_by_type_id_does_not_leak_to_subcomponent(conn):
    """``me_overrides`` (dict по ``product_type_id``, «Точный ME/TE» в корзине /
    ``/api/compare-me``) должен менять ME ТОЛЬКО того товара, чей type_id есть в словаре —
    под-компонент с ДРУГИМ type_id считает своё ME НЕЗАВИСИМО (default_me), даже когда из-за
    оверрайда верхнего товара меняется ТРЕБУЕМОЕ количество под-компонента."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',5.0),(35000,'Sub',1.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1035,1,35000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,35000,10),(1035,1,34,7);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (34,10000002,5.0);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)

    def sub_and_trit_qty(node):
        sub = next(l for l in node.lines if l.type_id == 35000)
        trit = next(l for l in sub.child.lines if l.type_id == 34)
        return sub.quantity, trit.quantity

    # default_me=0 везде (в т.ч. на Sub) — меняем ТОЛЬКО me_overrides для type_id=2000 (Widget).
    n0 = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=0, me_overrides={2000: 0})
    n10 = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=0, me_overrides={2000: 10})

    sub_qty0, trit0 = sub_and_trit_qty(n0)
    sub_qty10, trit10 = sub_and_trit_qty(n10)

    # Верхний узел: override реально работает — ME10 требует меньше Sub, чем ME0.
    assert sub_qty0 == 10
    assert sub_qty10 == 9

    # Sub (type_id=35000, НЕ в me_overrides) — считает СВОИМ default_me=0, не унаследовал
    # ME=10 оверрайда Widget. Если бы утекало, trit10 был бы ceil(7*9*0.9)=57, а не 63.
    assert trit0 == cost.material_quantity(7, [10], 0, 1.0, MANUFACTURING) == 70
    assert trit10 == cost.material_quantity(7, [9], 0, 1.0, MANUFACTURING) == 63


def test_me_te_overrides_none_is_noop(conn):
    """``me_overrides``/``te_overrides`` не заданы (``None``, значение по умолчанию) —
    поведение как до добавления параметров, никаких изменений в обычном расчёте."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',5.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,10);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (34,10000002,5.0);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    without = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=4)
    with_none = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=4,
                                              me_overrides=None, te_overrides=None)
    assert without.total_cost == with_none.total_cost
    # Пустой словарь (не только None) тоже не должен ничего менять — оба пути проверяются
    # одинаковым `if me_overrides and product_type_id in me_overrides` в build_node_cost.
    with_empty = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=4,
                                               me_overrides={}, te_overrides={})
    assert without.total_cost == with_empty.total_cost


def test_te_override_forces_resulting_te_even_when_owned(conn):
    """``te_overrides`` обязан работать и для owned_bpo/owned_bpc — там ``resulting_te``
    обычно ВСЕГДА ``None`` (TE — свойство конкретной копии, планировщик сам её ищет), но
    пользователь может явно задать «Точный ME/TE» поверх этого в BasketEditor."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',5.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,10);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (34,10000002,5.0);
        INSERT INTO characters(character_id,name) VALUES (7,'I');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,7,0,-1,-1,0);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    owned = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=0)
    assert owned.blueprint_source == "owned_bpo"
    assert owned.resulting_te is None  # без оверрайда — планировщик сам ищет TE копии

    overridden = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=0,
                                               te_overrides={2000: 20})
    assert overridden.blueprint_source == "owned_bpo"
    assert overridden.resulting_te == 20
    # ME не тронут (owned ME=7 из фикстуры) — te_overrides не должен влиять на me.
    assert overridden.unit_cost == owned.unit_cost


def test_max_production_limit_forces_extra_job_split(conn):
    """Чертёж не даёт запустить ОДИН джоб больше max_production_limit прогонов (реальный кейс
    нехватки материала: Missile Guidance Enhancer II ×20, Large Shield Extender II
    ×30 — оба max_production_limit=10): материал НЕ округляется ОДНИМ ceil на весь runs —
    EVE физически заставляет разбить на несколько джобов, каждый round'ится (ceil) отдельно
    (ceil субаддитивен — раздельное округление даёт СТРОГО БОЛЬШЕ или столько же, никогда меньше).
    """
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',5.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,7);
        INSERT INTO sde_blueprints(blueprint_type_id,max_production_limit) VALUES (1000,10);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (34,10000002,5.0);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    # streams=1 (юзер не просил параллелизма) — но runs=20 > max_production_limit=10.
    n = core.sourcing.build_node_cost(conn, 2000, 20, 1, params, default_me=1)
    trit = next(l for l in n.lines if l.type_id == 34)
    # 2 джоба по 10: ceil(7*10*0.99)=70 ×2 = 140 (НЕ ceil(7*20*0.99)=139 — один ceil на весь runs).
    assert trit.quantity == 140
    assert n.streams == 2  # поднято до минимума джобов, планировщик создаст 2 отдельных джоба

    # Явный больший параллелизм не режется лимитом вниз — берётся максимум из двух источников.
    n3 = core.sourcing.build_node_cost(conn, 2000, 20, 3, params, default_me=1)
    assert n3.streams == 3

    # Лимит НЕ достигнут (runs <= max_production_limit) — streams не растёт.
    n_small = core.sourcing.build_node_cost(conn, 2000, 5, 1, params, default_me=1)
    assert n_small.streams == 1


def test_force_buy_extra_forces_component_purchase(conn):
    """Ручной override «строить→купить» по под-компоненту (force_buy_extra) делает его покупным."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',5.0),(3000,'Gadget',1.0),(34,'Trit',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1003,1,3000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,3000,5),(1003,1,34,7);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (34,10000002,5.0),(3000,10000009,500.0);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    auto = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=0)
    forced = core.sourcing.build_node_cost(conn, 2000, 1, 1, params, default_me=0, force_buy_extra=frozenset({3000}))
    g = lambda node: next(l for l in node.lines if l.type_id == 3000)
    assert g(auto).source == "build" and g(auto).child is not None     # авто: строить дешевле
    assert g(forced).source == "buy" and g(forced).child is None       # форс: купить, без под-дерева


def test_eiv_and_job_cost(conn):
    _seed(conn)
    mats = bom.materials(conn, 1000, 1)
    # EIV = 3*100*2 + 18*50*2 = 600 + 1800 = 2400
    assert cost.eiv(conn, mats, 2) == pytest.approx(2400.0)
    jc = cost.job_install_cost(2400.0, 0.05, 1.0, 0.0, 0.0)
    assert jc == pytest.approx(120.0)


def test_job_install_cost_tax_and_scc_apply_to_eiv_not_to_gross():
    """Сверено с реальным разбором клиента EVE (тултип Job Cost для Crystalline Carbonide):
    facility tax и SCC surcharge берутся от EIV НАПРЯМУЮ и СКЛАДЫВАЮТСЯ с (EIV × cost_index ×
    риг-бонус) — а не умножают итог как (1 + tax + scc). Реальные числа: EIV=61 298 963,
    cost_index=8%, tax=1%, scc=4% → Job Gross Cost 4 903 879 + Facility tax 612 990 + SCC
    2 451 959 = Total 7 968 828 (проверено напрямую в игре). Мультипликативная (неверная) формула
    EIV×cost_index×(1+tax+scc) дала бы ~5 149 113 — почти на 3 млн меньше факта."""
    eiv_value = 61_298_963.0
    jc = cost.job_install_cost(eiv_value, 0.08, 1.0, 0.01, 0.04)
    assert jc == pytest.approx(7_968_828, rel=1e-5)
    # Убедиться, что это НЕ мультипликативная формула.
    wrong = eiv_value * 0.08 * 1.0 * (1.0 + 0.01 + 0.04)
    assert jc != pytest.approx(wrong)


def test_landed_unit_cost_picks_min(conn):
    _seed(conn)
    params = core.build_params_from_config(CFG)
    # 34: только Jita 5.0 + vol 0.01*(1+2)=0.03 -> 5.03
    assert cost.landed_unit_cost(conn, 34, params) == pytest.approx(5.03)
    # 35: C-J6MT 18.0 + 0.01*2=0.02 -> 18.02  против Jita 19.0+0.03 -> 19.03; min 18.02
    assert cost.landed_unit_cost(conn, 35, params) == pytest.approx(18.02)


def test_hub_unit_prices(conn):
    _seed(conn)
    params = core.build_params_from_config(CFG)
    # 35: Jita 19.0 + доставка 0.01*1 = 19.01 ; C-J локально 18.0
    h = cost.hub_unit_prices(conn, 35, params)
    assert h["jita_to_cj"] == pytest.approx(19.01)
    assert h["cj_local"] == pytest.approx(18.0)
    # 34: только Jita → cj_local None
    h34 = cost.hub_unit_prices(conn, 34, params)
    assert h34["cj_local"] is None
    assert h34["jita_to_cj"] == pytest.approx(5.01)


def test_landed_unit_cost_switches_hub_on_insufficient_volume(conn):
    """Дешёвый хаб (C-J6MT), но объёма мало → закупаемся в Jita, где хватает."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id, name, volume) VALUES (50,'Scarce',0.01);
        -- C-J6MT дешевле (10) но всего 100 шт; Jita дороже (12) но 1 000 000 шт
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (50,10000009,10.0,100),
            (50,10000002,12.0,1000000);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    # Нужно 100 — на C-J6MT ровно хватает → берём дешёвый C-J (10 + 0.01*2 = 10.02)
    assert cost.landed_unit_cost(conn, 50, params, 100) == pytest.approx(10.02)
    # Нужно 500 — на C-J6MT не хватает → Jita (12 + 0.01*(1+2) = 12.03)
    choice = cost.choose_hub(conn, 50, params, 500)
    assert choice.hub == "jita"
    assert cost.landed_unit_cost(conn, 50, params, 500) == pytest.approx(12.03)
    # hub_unit_prices сообщает выбор и достаточность
    h = cost.hub_unit_prices(conn, 50, params, 500)
    assert h["chosen"] == "jita" and h["cj_enough"] is False and h["jita_enough"] is True


def test_landed_unit_cost_falls_back_to_cheapest_when_none_enough(conn):
    """Если объёма не хватает нигде — берём самый дешёвый и помечаем дефицит."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id, name, volume) VALUES (51,'Rare',0.01);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (51,10000009,10.0,5),
            (51,10000002,12.0,5);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    choice = cost.choose_hub(conn, 51, params, 1000)
    assert choice.hub == "cj" and choice.enough is False  # дешёвый, но дефицит
    assert cost.landed_unit_cost(conn, 51, params, 1000) == pytest.approx(10.02)


def test_unit_sell_value_basic(conn):
    _seed(conn)
    params = core.build_params_from_config(CFG)
    # 35 продаётся в C-J6MT по 18.0; вывоз GPLB-C→C-J6MT = объём(0.01) × 2.0 = 0.02; без налога/брокера
    assert cost.unit_sell_value(conn, 35, params, CJ_REGION, 0.0, 0.0) == pytest.approx(18.0 - 0.02)


def test_unit_sell_value_applies_broker_and_sales_tax(conn):
    _seed(conn)
    params = core.build_params_from_config(CFG)
    # 10% брокер + 5% налог с продажи -> 18.0*(1-0.10-0.05) - 0.02
    assert cost.unit_sell_value(conn, 35, params, CJ_REGION, 0.10, 0.05) == pytest.approx(18.0 * 0.85 - 0.02)


def test_unit_sell_value_none_without_price_in_sell_region(conn):
    """Продажа строго в месте сбыта — цена ТОЛЬКО в Jita (не в C-J6MT) не считается, без отката
    (та же гарантия, что и у ``profit.compute_profit`` — см. test_profit_no_jita_fallback)."""
    _seed(conn)
    params = core.build_params_from_config(CFG)
    # 34 продаётся только в Jita (10000002) — в C-J6MT цены нет вообще.
    assert cost.unit_sell_value(conn, 34, params, CJ_REGION, 0.0, 0.0) is None


def test_unit_sell_value_uses_jump_fuel_for_capital(conn):
    """Побочка/сырьё капитал-группы (в теории не встречается на практике, но формула общая с
    profit.compute_profit) — вывоз топливом за прыжок, не объём × ставка."""
    conn.executescript(
        """
        INSERT INTO sde_groups(group_id, category_id, name) VALUES (902, 6, 'Jump Freighter');
        INSERT INTO sde_types(type_id, name, group_id, category_id, volume)
            VALUES (28848,'Anshar',902,6,17550000.0);
        INSERT INTO market_snapshot(type_id, region_id, sell_min) VALUES (28848, 10000009, 6000000000.0);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    params.jump_fuel[("gplb_c", "c_j6mt")] = 10_000_000.0
    assert cost.unit_sell_value(conn, 28848, params, CJ_REGION, 0.0, 0.0) == pytest.approx(
        6_000_000_000.0 - 10_000_000.0
    )


def test_estimate_build_buys_materials(conn):
    _seed(conn)
    est = core.estimate_build(conn, CFG, 2000, runs=1, streams=1)
    n = est.node
    # материалы: 34 ×100 @5.03 = 503 ; 35 ×50 @18.02 = 901 ; итого 1404
    assert n.material_cost == pytest.approx(1404.0)
    # джоб: EIV(1 run)=3*100+18*50=1200 ; *0.05 = 60
    assert n.job_cost == pytest.approx(60.0)
    assert n.total_cost == pytest.approx(1464.0)
    # продажа в C-J6MT 5000 - вывоз vol5*2=10 -> 4990 ; прибыль 4990-1464=3526
    assert est.profit.sell_unit_price == pytest.approx(5000.0)
    assert est.profit.profit == pytest.approx(3526.0)


def test_aggregate_costs_sums_whole_tree():
    """Агрегат раскладывает себестоимость дерева на материалы / джобы / чертежи."""
    from forge.core.sourcing import MaterialLine, NodeResult, aggregate_costs
    # ребёнок производит 10 ед.: 50 материалы + 5 джоб + 3 чертёж = 58 (unit 5.8)
    child = NodeResult(
        35, "Comp", 1, 1035, 1, 1, 10, 50.0, 5.0, 58.0, 5.8,
        lines=[MaterialLine(34, "Trit", 10, "buy", 5.0, 50.0)], blueprint_cost=3.0,
    )
    # потребляем 3 из 10 произведённых → frac 0.3: материалы 15, джоб 1.5, чертёж 0.9 (subtotal 17.4)
    parent = NodeResult(
        2000, "W", 1, 1000, 1, 1, 1, 117.4, 20.0, 147.4, 147.4,
        lines=[
            MaterialLine(34, "Trit", 100, "buy", 5.0, 100.0),
            MaterialLine(35, "Comp", 3, "build", 5.8, 17.4, child=child),
        ],
        blueprint_cost=10.0,
    )
    materials, jobs, blueprints = aggregate_costs(parent)
    assert materials == pytest.approx(115.0)     # 100 куплено + 15 в суб-сборке
    assert jobs == pytest.approx(21.5)           # 20 + 1.5
    assert blueprints == pytest.approx(10.9)     # 10 + 0.9
    assert materials + jobs + blueprints == pytest.approx(parent.total_cost)


def test_profit_no_jita_fallback(conn):
    """Продажа строго в C-J6MT: нет цены в C-J → прибыль None (без отката на Jita)."""
    _seed(conn)
    conn.execute("DELETE FROM market_snapshot WHERE type_id = 2000 AND region_id = ?", (CJ_REGION,))
    conn.execute("INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (2000, ?, 5000.0)", (JITA_REGION,))
    conn.commit()
    est = core.estimate_build(conn, CFG, 2000, runs=1, streams=1)
    assert est.profit.profit is None and est.profit.sell_unit_price is None


def test_volume_uses_packaged_for_ships(conn):
    conn.executescript(
        """
        INSERT INTO sde_groups(group_id, category_id, name) VALUES (25, 6, 'Frigate');
        INSERT INTO sde_types(type_id, name, group_id, category_id, volume) VALUES
            (587,'Rifter',25,6,27289.0),     -- корабль: собранный объём, упакованный 2500
            (34,'Tritanium',18,4,0.01);      -- минерал: volume и есть упакованный
        """
    )
    conn.commit()
    assert prices.volume(conn, 587) == 2500.0   # по группе Frigate, не 27289
    assert prices.volume(conn, 34) == 0.01
    # капитал-класс тоже в маппинге: JF → packaged 1.3M, не assembled
    conn.executescript(
        """
        INSERT INTO sde_groups(group_id, category_id, name) VALUES (902, 6, 'Jump Freighter');
        INSERT INTO sde_types(type_id, name, group_id, category_id, volume)
            VALUES (28848,'Anshar',902,6,17550000.0);
        """
    )
    conn.commit()
    assert prices.volume(conn, 28848) == 1300000.0


def test_export_jump_fuel_for_capital(conn):
    """Вывоз самоходного капитала = топливо за прыжок (фикс), а не объём × ставка."""
    from forge.core import profit
    from forge.core.sourcing import NodeResult
    conn.executescript(
        """
        INSERT INTO sde_groups(group_id, category_id, name) VALUES (902, 6, 'Jump Freighter');
        INSERT INTO sde_types(type_id, name, group_id, category_id, volume)
            VALUES (28848,'Anshar',902,6,17550000.0);
        INSERT INTO market_snapshot(type_id, region_id, sell_min) VALUES (28848, 10000009, 6000000000.0);
        """
    )
    conn.commit()
    params = core.build_params_from_config(CFG)
    params.jump_fuel[("gplb_c", "c_j6mt")] = 10_000_000.0  # топливо за прыжок
    node = NodeResult(28848, "Anshar", 1, 28847, 1, 1, 1, 0.0, 0.0, 5_000_000_000.0, 5_000_000_000.0)
    pr = profit.compute_profit(conn, node, params, CJ_REGION, 0.0, 0.0)
    # вывоз = 10M (прыжок), а не 17.55M × 2 ISK/m³; revenue = цена − топливо
    assert pr.revenue == pytest.approx(6_000_000_000.0 - 10_000_000.0)


CFG_JUMP = config_mod.loads(
    """
db_path = "x.db"
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
from = "c_j6mt"
to = "gplb_c"
mode = "fixed_jump"
fixed_cost = 20000000.0
vessel_capacity_m3 = 1000.0
load_factor = 1.0
[[freight_routes]]
from = "gplb_c"
to = "c_j6mt"
mode = "fixed_jump"
fixed_cost = 20000000.0
vessel_capacity_m3 = 1000.0
load_factor = 1.0
"""
)


def test_jump_freight_cost_trips_scale_by_capacity():
    """Один прыжок в пределах вместимости; каждая ДОПОЛНИТЕЛЬНАЯ партия — ещё два прыжка
    (порожняком обратно + снова с грузом): N партий ⇒ (2N−1) прыжков × fixed_cost."""
    route = cost.JumpRoute(fixed_cost=20_000_000.0, vessel_capacity_m3=100.0, load_factor=1.0)
    assert cost.jump_freight_cost(route, 0.0) == 0.0
    assert cost.jump_freight_cost(route, 50.0) == pytest.approx(20_000_000.0)         # 1 партия
    assert cost.jump_freight_cost(route, 100.0) == pytest.approx(20_000_000.0)        # впритык — 1
    assert cost.jump_freight_cost(route, 100.01) == pytest.approx(20_000_000.0 * 3)   # 2 партии
    assert cost.jump_freight_cost(route, 250.0) == pytest.approx(20_000_000.0 * 5)    # 3 партии


def test_jump_freight_cost_load_factor_shrinks_effective_capacity():
    """load_factor < 1 уменьшает реальную вместимость за рейс — порог превышается раньше."""
    route = cost.JumpRoute(fixed_cost=20_000_000.0, vessel_capacity_m3=100.0, load_factor=0.5)
    assert cost.jump_freight_cost(route, 50.0) == pytest.approx(20_000_000.0)         # ровно 1 партия
    assert cost.jump_freight_cost(route, 50.01) == pytest.approx(20_000_000.0 * 3)    # уже 2 партии


def test_jump_freight_cost_zero_capacity_guard():
    """vessel_capacity_m3=0 (не настроено) не должно падать делением на 0 — минимум 1 рейс."""
    route = cost.JumpRoute(fixed_cost=20_000_000.0, vessel_capacity_m3=0.0, load_factor=1.0)
    assert cost.jump_freight_cost(route, 500.0) == pytest.approx(20_000_000.0)


def test_route_freight_cost_per_m3_stays_linear():
    """per_m3-маршрут — без рейсовой логики, просто объём × ставка (линейно)."""
    params = core.build_params_from_config(CFG)  # CFG: все маршруты per_m3
    assert cost.route_freight_cost(params, "c_j6mt", "gplb_c", 1000.0) == pytest.approx(2000.0)


def test_route_freight_cost_uses_jump_routes_when_fixed_jump():
    """fixed_jump-маршрут в конфиге → ступенчатая логика, а не линейная ставка."""
    params = core.build_params_from_config(CFG_JUMP)
    small = cost.route_freight_cost(params, "c_j6mt", "gplb_c", 1000.0)  # ровно вместимость
    assert small == pytest.approx(20_000_000.0)
    big = cost.route_freight_cost(params, "c_j6mt", "gplb_c", 1000.01)   # чуть больше
    assert big == pytest.approx(20_000_000.0 * 3)


def test_route_freight_cost_per_m3_applies_minimum_floor():
    """per_m3-маршрут с min_cost: пока объём×ставка меньше порога — платим порог целиком
    (минимум за доставку, даже мелкую); выше порога — обычная линейная ставка."""
    params = core.build_params_from_config(CFG)  # jita->c_j6mt: isk_per_m3=1.0
    params.min_cost[("jita", "c_j6mt")] = 5_000_000.0
    assert cost.route_freight_cost(params, "jita", "c_j6mt", 0.0) == 0.0
    small = cost.route_freight_cost(params, "jita", "c_j6mt", 100.0)  # 100 ISK << 5М
    assert small == pytest.approx(5_000_000.0)
    big = cost.route_freight_cost(params, "jita", "c_j6mt", 10_000_000.0)  # 10М ISK > 5М
    assert big == pytest.approx(10_000_000.0)


def test_batched_freight_params_applies_minimum_once_per_order(conn):
    """Минимум за доставку Jita→C-J6MT считается РАЗ на весь заказ, не на каждый материал.
    5 разных материалов по 2 м³ (10 м³ суммарно) при ставке 100 ISK/м³ = 1000 ISK << 5М
    минимума — минимум должен примениться ОДИН раз (5М), а не 5 раз (25М)."""
    from forge.core.sourcing import MaterialLine, NodeResult
    conn.executescript(
        "INSERT INTO sde_types(type_id,name,volume) VALUES " +
        ",".join(f"({800+i},'Mat{i}',1.0)" for i in range(5))
    )
    conn.commit()
    params0 = core.build_params_from_config(CFG)
    params0.min_cost[("jita", "c_j6mt")] = 5_000_000.0
    nodes = [
        NodeResult(800 + i, f"Mat{i}", 0, 0, 1, 1, 1, 0.0, 0.0, 0.0, 0.0,
                   lines=[MaterialLine(800 + i, f"Mat{i}", 2, "buy", 100.0, 200.0, buy_hub="jita")])
        for i in range(5)
    ]
    params1 = core.batched_freight_params(conn, params0, nodes)
    rate = params1.freight[("jita", "c_j6mt")]
    total_vol = 10.0  # 5 × 2
    assert rate == pytest.approx(5_000_000.0 / total_vol)   # минимум применён ОДИН раз
    assert rate * total_vol == pytest.approx(5_000_000.0)   # НЕ 25М (5 независимых минимумов)


def test_batched_freight_params_combines_multiple_materials_into_one_shipment(conn):
    """5 РАЗНЫХ материалов по 300 м³ каждый (в пределах вместимости 1000 м³ САМ ПО СЕБЕ) —
    если бы рейс считался на КАЖДЫЙ отдельно, это 5×20М=100М. Батч на СУММАРНЫЙ
    объём (1500 м³) — всего 2 рейса (×3=60М). Батчинг обязан давать второй вариант."""
    from forge.core.sourcing import MaterialLine, NodeResult
    conn.executescript(
        "INSERT INTO sde_types(type_id,name,volume) VALUES " +
        ",".join(f"({700+i},'Mat{i}',1.0)" for i in range(5))
    )
    conn.commit()
    params0 = core.build_params_from_config(CFG_JUMP)  # capacity=1000 m3, fixed_cost=20M
    nodes = [
        NodeResult(700 + i, f"Mat{i}", 0, 0, 1, 1, 1, 0.0, 0.0, 0.0, 0.0,
                   lines=[MaterialLine(700 + i, f"Mat{i}", 300, "buy", 100.0, 30_000.0, buy_hub="cj")])
        for i in range(5)
    ]
    params1 = core.batched_freight_params(conn, params0, nodes)
    rate = params1.freight[("c_j6mt", "gplb_c")]
    total_vol = 1500.0  # 5 × 300
    assert rate == pytest.approx(20_000_000.0 * 3 / total_vol)   # 2 рейса на суммарный объём
    assert rate * total_vol == pytest.approx(60_000_000.0)       # НЕ 100М (5 независимых рейсов)


def test_estimate_basket_applies_same_batched_rate_to_all_items(conn):
    """estimate_basket пересчитывает КАЖДЫЙ продукт корзины с ОДНОЙ И ТОЙ ЖЕ (батчевой)
    ставкой фрахта на суммарный объём всей корзины — не с независимой ставкой на каждый."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (2000,'WidgetA',1.0),(2001,'WidgetB',1.0),(34,'Tritanium',10.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1001,1,2001,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600),(1001,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,30),(1001,1,34,50);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000009,5.0,1000000);
        """
    )
    conn.commit()
    basket, batched_params = core.estimate_basket(conn, CFG_JUMP, [(2000, 1, 1), (2001, 1, 1)])
    # WidgetA: 30×10=300 м³; WidgetB: 50×10=500 м³; суммарно 800 м³ (< 1000) -> 1 рейс -> 20М/800.
    expected_rate = 20_000_000.0 / 800.0
    assert batched_params.freight[("c_j6mt", "gplb_c")] == pytest.approx(expected_rate)
    trit_a = next(l for l in basket[0].node.lines if l.type_id == 34)
    trit_b = next(l for l in basket[1].node.lines if l.type_id == 34)
    assert trit_a.unit_cost == pytest.approx(5.0 + 10.0 * expected_rate)
    assert trit_b.unit_cost == pytest.approx(5.0 + 10.0 * expected_rate)  # ТА ЖЕ ставка, не своя


def test_make_or_buy_prefers_cheaper_build(conn):
    _seed(conn)
    # Добавим субкомпонент 35000, который Widget потребляет вместо 35... проще: отдельный продукт.
    # Здесь проверим механизм на 35: дадим ему чертёж дешевле покупки.
    conn.executescript(
        """
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1035,1,35,100,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1035,1,34,1);
        """
    )
    conn.commit()
    # Построить 100 шт. 35 стоит ~ 1*100*5.03 материалов + копейки джоба = ~503 на 100 = ~5.03/шт,
    # что ДЕШЕВЛЕ покупки 18.02/шт -> source 'build'.
    est = core.estimate_build(conn, CFG, 2000, runs=1, streams=1)
    line35 = next(l for l in est.node.lines if l.type_id == 35)
    assert line35.source == "build"
    assert line35.unit_cost < 18.02
