"""Пер-станционные параметры: конвертация %→множитель, маршрутизация, применение к стоимости."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core
from forge.core import cost
from forge.core.bom import MANUFACTURING, REACTION

INVENTION = 8

CFG = config_mod.loads(
    """
db_path = "x.db"
[industry]
rig_material_mult = 1.0
rig_cost_mult = 1.0
time_mult = 1.0
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006

[[facilities]]
name = "React"
role = "reaction"
material_bonus_pct = 4.0
time_bonus_pct = 20.0

[[facilities]]
name = "Invent"
role = "invention"

[[facilities]]
name = "Adv Comp"
role = "component"
material_bonus_pct = 5.0
cost_bonus_pct = 90.0
tax_pct = 1.0
group_ids = [334]

[[facilities]]
name = "Default"
role = "manufacturing"
material_bonus_pct = 1.0
time_bonus_pct = 30.0
"""
)


def test_pct_to_mult_conversion():
    p = core.build_params_from_config(CFG)
    react = next(f for f in p.facilities if f.role == "reaction")
    assert react.material_mult == pytest.approx(0.96)   # 1 - 4%
    assert react.time_mult == pytest.approx(0.80)       # 1 - 20%
    comp = next(f for f in p.facilities if f.role == "component")
    assert comp.cost_mult == pytest.approx(0.10) and comp.facility_tax == pytest.approx(0.01)
    assert comp.group_ids == frozenset({334})


def test_routing_by_activity_and_group():
    p = core.build_params_from_config(CFG)
    assert cost.facility_mults(p, REACTION, None).role == "reaction"
    assert cost.facility_mults(p, INVENTION, None).role == "invention"
    assert cost.facility_mults(p, MANUFACTURING, 334).role == "component"   # группа-компонент
    assert cost.facility_mults(p, MANUFACTURING, 999).role == "manufacturing"  # дефолт


def test_fallback_to_industry_when_role_missing():
    cfg = config_mod.loads(
        'db_path="x"\n[industry]\nrig_material_mult=0.9\nrig_cost_mult=0.8\ntime_mult=0.7\nfacility_tax=0.02'
    )
    p = core.build_params_from_config(cfg)
    fm = cost.facility_mults(p, MANUFACTURING, 123)
    assert fm.material_mult == 0.9 and fm.cost_mult == 0.8 and fm.time_mult == 0.7 and fm.facility_tax == 0.02


def test_reaction_group_scoping_fixes_original_bug():
    """Риг реакторной эффективности реально скопирован под ОДНУ категорию реакций
    (напр. Composite ≠ Hybrid), а не «всё что реакция»: facility role=reaction с group_ids
    применяется только к своим группам (риг под Composite не даёт скидку на Hybrid).
    group_ids работает для ЛЮБОЙ роли, не только component."""
    cfg = config_mod.loads(
        """
db_path = "x"
[industry]
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006

[[facilities]]
name = "Composite Reactor"
role = "reaction"
material_bonus_pct = 2.0
group_ids = [429]

[[facilities]]
name = "Hybrid Reactor"
role = "reaction"
material_bonus_pct = 5.0
group_ids = [428]
"""
    )
    p = core.build_params_from_config(cfg)
    composite = cost.facility_mults(p, REACTION, 429)
    hybrid = cost.facility_mults(p, REACTION, 428)
    assert composite.material_mult == pytest.approx(0.98)   # 1 - 2%, СВОЙ риг
    assert hybrid.material_mult == pytest.approx(0.95)       # 1 - 5%, ДРУГОЙ риг — не спутались
    assert composite.material_mult != hybrid.material_mult


def test_group_scoped_catchall_requires_empty_group_ids():
    """Facility с непустым group_ids, не совпавшим с запрошенной группой, НЕ должна становиться
    catch-all — иначе Hybrid-риг мог бы случайно поглотить Composite-реакцию, просто оказавшись
    в списке первым. Без совпадения по группе и без catch-all (пустой group_ids) — фолбэк на
    industry-дефолты BuildParams."""
    cfg = config_mod.loads(
        """
db_path = "x"
[industry]
rig_material_mult = 0.77
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006

[[facilities]]
name = "Hybrid Reactor"
role = "reaction"
material_bonus_pct = 5.0
group_ids = [428]
"""
    )
    p = core.build_params_from_config(cfg)
    other = cost.facility_mults(p, REACTION, 999)  # чужая группа, не 428
    assert other.material_mult == pytest.approx(0.77)  # фолбэк на industry, а НЕ Hybrid-риг


def test_category_scoping_fixes_xl_rig_cross_contamination():
    """Реальный кейс юзера (Typhoon, facility «GEZ T1 Ship Comp Equip»): риги
    Engineering Complex XL-тира даются ПО КАТЕГОРИИ продукта, не по группе — Standup XL-Set
    Ship Manufacturing Efficiency действует ТОЛЬКО на Ships (category_id=6), а не на
    Module/Charge/Structure. Если все 3 XL-рига (Ship/Equipment&Consumable/Structure&
    Component) фитовать на ОДНУ facility без разбора, они сложатся стэкинг-пенальти
    ВМЕСТЕ на любой продукт — а тултип клиента: Installed Rig -4.2% материала (это РОВНО
    ОДИН риг: -2.0% × nullSecModifier 2.1), не ~9.9% (все три рига сразу)."""
    cfg = config_mod.loads(
        """
db_path = "x"
[industry]
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006

[[facilities]]
name = "Ship rig"
role = "manufacturing"
category_ids = [6]
material_bonus_pct = 2.0

[[facilities]]
name = "Equipment rig"
role = "manufacturing"
category_ids = [7, 8]
material_bonus_pct = 9.9
"""
    )
    p = core.build_params_from_config(cfg)
    ship = cost.facility_mults(p, MANUFACTURING, None, 6)     # category Ship
    module = cost.facility_mults(p, MANUFACTURING, None, 7)   # category Module
    assert ship.material_mult == pytest.approx(0.98)    # 1-2%, СВОЙ риг (Ship)
    assert module.material_mult == pytest.approx(0.901)  # 1-9.9%, ДРУГОЙ риг (Equipment) — не спутались
    assert ship.material_mult != module.material_mult


def test_category_scoped_catchall_requires_empty_category_ids():
    """Facility с непустым category_ids, не совпавшим с запрошенной категорией, НЕ должна
    становиться catch-all — иначе риг под Ships мог бы случайно поглотить Module, просто
    оказавшись в списке первым."""
    cfg = config_mod.loads(
        """
db_path = "x"
[industry]
rig_material_mult = 0.77
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006

[[facilities]]
name = "Ship rig"
role = "manufacturing"
category_ids = [6]
material_bonus_pct = 2.0
"""
    )
    p = core.build_params_from_config(cfg)
    other = cost.facility_mults(p, MANUFACTURING, None, 65)  # чужая категория (Structure), не 6
    assert other.material_mult == pytest.approx(0.77)  # фолбэк на industry, а НЕ Ship-риг


def test_reaction_facility_fit_applies_in_build_node_cost(conn):
    """Интеграционный тест (по образцу test_component_station_applies_in_build_node_cost):
    реакция группы 429 (Composite) берёт СВОЮ facility, реакция группы 428 (Hybrid) — другую,
    без перекрёстного влияния."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,group_id,volume) VALUES
            (34,'Tritanium',18,0.01),(2429,'CompositeProduct',429,1.0),(2428,'HybridProduct',428,1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,11,2429,1,1.0), (1001,11,2428,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,11,34,100), (1001,11,34,100);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,11,0.05);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000006,5.0,1000000);
        """
    )
    conn.commit()
    from forge.core import sourcing
    cfg = config_mod.loads(
        """
db_path = "x"
[industry]
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006

[[facilities]]
name = "Composite Reactor"
role = "reaction"
material_bonus_pct = 50.0
group_ids = [429]
"""
    )
    params = core.build_params_from_config(cfg)
    node_composite = sourcing.build_node_cost(conn, 2429, runs=1, streams=1, params=params)
    node_hybrid = sourcing.build_node_cost(conn, 2428, runs=1, streams=1, params=params)
    assert node_composite.lines[0].quantity == 50   # 100 × (1 − 0.5) — своя facility
    assert node_hybrid.lines[0].quantity == 100      # чужая группа — риг НЕ применился


def test_category_scoped_facility_fit_applies_in_build_node_cost(conn):
    """Интеграционный тест на реальный кейс юзера (Typhoon): продукт категории Ship (6) берёт
    facility со своим category_ids, продукт категории Module (7) — НЕ берёт (чужая категория),
    даже если оба продукта строятся на одной и той же станции по роли manufacturing."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,group_id,category_id,volume) VALUES
            (34,'Tritanium',18,4,0.01),(2600,'ShipProduct',27,6,1.0),(2601,'ModuleProduct',7,7,1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2600,1,1.0), (1001,1,2601,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,100), (1001,1,34,100);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,1,0.05);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000006,5.0,1000000);
        """
    )
    conn.commit()
    from forge.core import sourcing
    cfg = config_mod.loads(
        """
db_path = "x"
[industry]
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006

[[facilities]]
name = "Ship rig"
role = "manufacturing"
material_bonus_pct = 50.0
category_ids = [6]
"""
    )
    params = core.build_params_from_config(cfg, conn)
    node_ship = sourcing.build_node_cost(conn, 2600, runs=1, streams=1, params=params)
    node_module = sourcing.build_node_cost(conn, 2601, runs=1, streams=1, params=params)
    assert node_ship.lines[0].quantity == 50    # 100 × (1 − 0.5) — своя facility (category Ship)
    assert node_module.lines[0].quantity == 100  # чужая категория — риг НЕ применился


def test_always_buy_forces_buy_over_build(conn):
    """«Всегда покупать» (по type_id и по группе) пересиливает make-or-buy, даже если строить дешевле."""
    from forge.core import sourcing
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,group_id,volume) VALUES (34,'Trit',18,0.0),(2000,'Widget',600,0.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity) VALUES (1000,1,34,1);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,1.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,1,0.0);
        -- 34 дёшево (постройка дешевле), 2000 дорого купить
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,1.0,1000000),(2000,10000002,1000.0,1000000);
        """
    )
    conn.commit()
    cfg = config_mod.loads(
        'db_path="x"\n[industry]\n[locations.jita]\nname="Jita"\nregion_id=10000002\n'
        '[locations.gplb_c]\nname="GPLB-C"\nsystem_id=30000552\nregion_id=10000006'
    )
    kw = dict(default_me=0, max_depth=8, allow_build=True, depth=0, chain=frozenset())

    params = core.build_params_from_config(cfg)
    assert sourcing._source_material(conn, 2000, 1, params, **kw).source == "build"  # дешевле строить

    params.always_buy_types = frozenset({2000})
    assert sourcing._source_material(conn, 2000, 1, params, **kw).source == "buy"

    params.always_buy_types = frozenset()
    params.always_buy_groups = frozenset({600})
    assert sourcing._source_material(conn, 2000, 1, params, **kw).source == "buy"


def test_component_station_applies_in_build_node_cost(conn):
    """Продукт группы-компонента считается материалами/стоимостью своей станции."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,group_id,volume) VALUES (34,'Tritanium',18,0.01),(2000,'Comp',334,1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,100);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,1,0.05);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000006,5.0,1000000);
        """
    )
    conn.commit()
    from forge.core import sourcing
    # станция компонентов даёт −50% материалов; по умолчанию (без станции) — 100 ед.
    cfg_comp = config_mod.loads(
        'db_path="x"\n[industry]\n[locations.gplb_c]\nname="GPLB-C"\nsystem_id=30000552\nregion_id=10000006\n'
        '[[facilities]]\nname="C"\nrole="component"\nmaterial_bonus_pct=50.0\ngroup_ids=[334]'
    )
    params = core.build_params_from_config(cfg_comp)
    node = sourcing.build_node_cost(conn, 2000, runs=1, streams=1, params=params)
    # 100 × (1 − 0.5) = ceil(50) = 50 ед. трита; материалы по landed-цене трита
    line = node.lines[0]
    assert line.quantity == 50


def test_build_params_from_config_computes_rig_and_structure_cost_mult(conn):
    """Реальный кейс юзера (инвента Nergal): build_params_from_config(cfg, conn)
    должен считать cost_mult facility из ФИТОВАННЫХ ригов + встроенного бонуса структуры, не
    только material/time (facility_bonus_pct поддерживает kind='cost', а _cost_mult в
    build_params_from_config смотрит и на риги, не ТОЛЬКО на структуру — так риг «Standup
    M-Set Invention Cost Optimization II» с attributeEngRigCostBonus=-12.0 влияет на
    итоговую стоимость джоба инвенты)."""
    conn.executescript(
        """
        INSERT INTO sde_systems(system_id,name,region_id,security) VALUES (30000552,'GPLB-C',10000006,-0.25);
        INSERT INTO sde_dogma_attribute_types(attribute_id,name,display_name,stackable,high_is_good) VALUES
            (2595,'attributeEngRigCostBonus',NULL,1,0),
            (2357,'nullSecModifier','Nullsec and Wormhole Bonus Multiplier',0,1),
            (2601,'strEngCostBonus',NULL,0,1);
        INSERT INTO sde_dogma_type_attributes(type_id,attribute_id,value_float) VALUES
            (43878,2595,-12.0),
            (43878,2357,2.1),
            (35825,2601,0.97);
        """
    )
    conn.commit()
    cfg = config_mod.loads(
        """
db_path = "x"
[industry]
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006

[[facilities]]
name = "Invention"
role = "invention"
fitted_type_ids = [43878]
structure_type_id = 35825
"""
    )
    params = core.build_params_from_config(cfg, conn)
    fm = next(f for f in params.facilities if f.role == "invention")
    # риг: -12.0 × 2.1 = -25.2% (mult 0.748); структура Raitaru: 0.97; итог 0.72556
    assert fm.cost_mult == pytest.approx(0.72556)