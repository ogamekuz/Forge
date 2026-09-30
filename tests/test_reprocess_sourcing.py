"""core/sourcing.py::build_node_cost — опция «переработка прекурсора дешевле прямой постройки»
(params.reprocessing_efficiency). Смоделировано по реальному найденному кейсу: Dysporite дешевле
получить, переработав Unrefined Dysporite (Cadmium+Mercury), чем строить напрямую из Dysprosium.
"""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core
from forge.core import sourcing
from forge.planner import schedule

CFG_OFF = config_mod.loads(
    """
db_path = "x.db"
[industry]
rig_material_mult = 1.0
rig_cost_mult = 1.0
facility_tax = 0.0
scc_surcharge = 0.0
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

CFG_ON = config_mod.loads(
    """
db_path = "x.db"
[industry]
rig_material_mult = 1.0
rig_cost_mult = 1.0
facility_tax = 0.0
scc_surcharge = 0.0
reprocessing_efficiency = 0.75
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
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (16668,'Dysporite',0.2),(29660,'Unrefined Dysporite',1.0),
            (16650,'Dysprosium',0.5),(16643,'Cadmium',0.5),(16646,'Mercury',0.5),
            (4247,'Helium Fuel Block',5.0);
        -- Dysporite Reaction Formula: дорогой Dysprosium -> 200 Dysporite/run.
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (46170,11,16668,200,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (46170,11,4247,5),(46170,11,16646,100),(46170,11,16650,100);
        -- Unrefined Dysporite Reaction Formula: дешёвый Cadmium -> 1 Unrefined Dysporite/run.
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (46200,11,29660,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (46200,11,4247,5),(46200,11,16643,100),(46200,11,16646,100);
        -- Переработка: 1x Unrefined Dysporite -> 73 Dysporite + 173 Mercury (100% эфф.).
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity) VALUES
            (29660,1,16668,73),(29660,1,16646,173);
        -- Цены: Dysprosium ДОРОГОЙ, Cadmium ДЕШЁВЫЙ (реальное соотношение). Mercury — ЕЩЁ и в
        -- C-J6MT (10000009): выручка с побочки продаётся ТОЛЬКО в месте сбыта (см.
        -- cost.unit_sell_value / profit.compute_profit — без отката на Jita), без цены там
        -- кредит за побочку всегда 0.
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES
            (4247,10000002,24000.0),(16646,10000002,3700.0),(16646,10000009,3700.0),
            (16650,10000002,60000.0),(16643,10000002,7000.0);
        """
    )
    conn.commit()


def _seed_two_tier(conn):
    """X — реакция, дешевле через переработку прекурсора P. У P СРЕДИ СВОИХ МАТЕРИАЛОВ — ещё
    один реакционный продукт Y, у которого САМОГО есть отдельный, ВЫГОДНЫЙ путь через
    переработку (дешёвый Q вместо дорогого Titanium). Смоделировано по реальному найденному
    случаю (живой профайлинг Nomad): «сложные» реакционные материалы (Carbon Polymers,
    Fullerides и т.п.) САМИ имеют Unrefined-путь — без ограничения на ОДИН уровень переработка
    рекурсивно пересчитывала бы переработку для НИХ ТОЖЕ на каждом вхождении в огромном дереве
    (тысячи материалов × собственная попытка переработки на каждый) — это зависание
    (0.03с без опции → 60+с с ней, не завершается даже с кэшами), поэтому переработка ограничена
    ОДНИМ уровнем (см. ``_REPROCESS_MARKER`` в sourcing.py)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (90001,'X',0.2),(90002,'P',1.0),(90003,'Y',0.2),(90004,'Q',1.0),
            (90005,'Titanium Expensive',0.5),(90006,'Cheap Mineral',0.5),
            (90007,'Filler',0.5),(4247,'Helium Fuel Block',5.0);
        -- X: обычный (дорогой) путь через Filler.
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (91000,11,90001,100,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (91000,11,90007,1000);
        -- P (прекурсор X при переработке): среди материалов — Y (реакционный продукт).
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (91001,11,90002,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (91001,11,4247,5),(91001,11,90003,50);
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity)
            VALUES (90002,1,90001,73);
        -- Y: обычный (ДОРОГОЙ) путь через Titanium Expensive.
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (91002,11,90003,10,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (91002,11,90005,100);
        -- Q: ДЕШЁВЫЙ прекурсор для Y через переработку (был бы выгоден, если оценивать Y отдельно).
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (91003,11,90004,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (91003,11,90006,100);
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity)
            VALUES (90004,1,90003,50);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES
            (4247,10000002,24000.0),(90005,10000002,100000.0),(90006,10000002,10.0),(90007,10000002,1000000.0);
        """
    )
    conn.commit()


def test_reprocessing_does_not_cascade_into_nested_reaction_tier_materials(conn):
    _seed_two_tier(conn)
    params = core.build_params_from_config(CFG_ON)

    # Контроль: Y САМА ПО СЕБЕ (построена напрямую, не как чужой материал) реально выгоднее
    # через переработку Q — иначе тест ничего не доказывает (нет реальной альтернативы, которую
    # можно было бы ошибочно не заметить).
    y_direct = sourcing.build_node_cost(conn, 90003, 10, 1, params, default_me=0)
    assert y_direct.blueprint_source == "reprocess"
    assert y_direct.reprocess_source_id == 90004

    # X — переработка через P (единственный проверяемый уровень).
    x_node = sourcing.build_node_cost(conn, 90001, 1, 1, params, default_me=0)
    assert x_node.blueprint_source == "reprocess"
    assert x_node.reprocess_source_id == 90002
    p_node = x_node.lines[0].child
    assert p_node.blueprint_type_id == 91001  # P реально построен по своему чертежу

    # Y — материал ВНУТРИ P (найден на глубине ВТОРОГО уровня переработки) — НЕ должен САМ
    # переключиться на переработку через Q, несмотря на то, что это было бы выгоднее (см.
    # y_direct выше): один уровень переработки на дерево — граница, подтверждённая
    # живым профайлингом Nomad.
    y_line = next(ln for ln in p_node.lines if ln.type_id == 90003)
    assert y_line.child is not None
    assert y_line.child.blueprint_source != "reprocess"
    assert y_line.child.blueprint_type_id == 91002  # обычный (дорогой) чертёж Y, не через Q


def _seed_shared_consumers(conn):
    """Два независимых «корабля», каждый из которых напрямую требует Dysporite — минимальный
    случай, где ``consolidate_shared_components`` сольёт узел Dysporite (переработка) из двух
    разных веток в один общий объект."""
    _seed(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (90101,'ShipA',100.0),(90102,'ShipB',100.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (91100,1,90101,1,1.0),(91101,1,90102,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (91100,1,16668,500),(91101,1,16668,700);
        """
    )
    conn.commit()


def test_reprocessing_byproduct_credit_not_counted_after_shared_component_consolidation(conn):
    """Побочка переработки — справочный бонус, НЕ часть себестоимости (юзер продавать её не
    планирует). ``reprocess_byproduct_credit`` на узле остаётся ненулевым (для UI), но не
    должен как-либо влиять на ``material_cost`` — ни сразу после сборки, ни после слияния
    общих компонентов (``consolidate_shared_components``/``_recompute_aggregates``). Здесь —
    минимальный кейс: два независимых «корабля» напрямую требуют Dysporite (сольётся в один
    общий узел переработки)."""
    _seed_shared_consumers(conn)
    params = core.build_params_from_config(CFG_ON)

    ship_a = sourcing.build_node_cost(conn, 90101, 1, 1, params, default_me=0)
    ship_b = sourcing.build_node_cost(conn, 90102, 1, 1, params, default_me=0)
    dysporite_a = ship_a.lines[0].child
    dysporite_b = ship_b.lines[0].child
    assert dysporite_a.blueprint_source == "reprocess"
    assert dysporite_b.blueprint_source == "reprocess"
    assert dysporite_a is not dysporite_b  # ещё не объединены

    sourcing.consolidate_shared_components(conn, [ship_a, ship_b], params, default_me=0)

    merged = ship_a.lines[0].child
    assert merged is ship_b.lines[0].child  # теперь общий объект
    assert merged.blueprint_source == "reprocess"
    assert merged.reprocess_byproduct_credit > 0  # справочное поле считается и после слияния

    # material_cost = precursor.subtotal, БЕЗ вычета кредита — ни до, ни после пересчёта
    # агрегатов при слиянии.
    assert merged.material_cost == pytest.approx(merged.lines[0].subtotal)
    assert merged.total_cost == pytest.approx(merged.material_cost + merged.job_cost + merged.blueprint_cost)
    assert merged.unit_cost == pytest.approx(merged.total_cost / merged.produced)


def test_aggregate_costs_treats_reprocess_node_as_atomic_material(conn):
    """totals в отчёте/Калькуляторе («Материалы (всего)»/«Джобы (всего)») считаются через
    ``sourcing.aggregate_costs``, НЕ через node.material_cost напрямую — узел-обёртка
    переработки обязан остаться атомарным материальным расходом (не расщепляться на джобы/
    чертежи прекурсора), и сумма (materials+jobs+blueprints) обязана биться с node.total_cost,
    как гарантирует докстринг aggregate_costs."""
    _seed(conn)
    params = core.build_params_from_config(CFG_ON)
    node = sourcing.build_node_cost(conn, 16668, 1, 1, params, default_me=0)
    assert node.blueprint_source == "reprocess"
    assert node.reprocess_byproduct_credit > 0
    assert node.job_cost == 0.0  # переработка сама не джоб — весь расход уже в material_cost

    m, j, b = sourcing.aggregate_costs(node)
    assert m == pytest.approx(node.material_cost)
    assert j == pytest.approx(node.job_cost)
    assert b == pytest.approx(node.blueprint_cost)
    assert m + j + b == pytest.approx(node.total_cost)  # гарантия из докстринга aggregate_costs


def _seed_reverse_reprocess_ingredient(conn):
    """X (90301) — реакционный продукт. Y (90302) — обычный (MANUFACTURING) предмет, который
    САМ требует X как ингредиент своего чертежа — И ОДНОВРЕМЕННО легитимно перерабатывается
    ОБРАТНО в X (реальный найденный случай: «Fulleroferrocene Power Conduits» — устаревший
    T3-сабсистем-компонент, требует Methanofullerene в материалах И перерабатывается в него же,
    Nomad, эфф. переработки 55%). Экономически бессмысленно (строить Y, чтобы переработать
    обратно в X, которого для постройки Y и так нужно больше), но легитимно в SDE."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (90301,'X',0.2),(90302,'Y',1.0),(90303,'Filler',0.5),(90304,'Other Mat',0.5);
        -- X: обычный (единственный) путь — через Filler.
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (91300,11,90301,200,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (91300,11,90303,1000);
        -- Y: обычная постройка, ТРЕБУЕТ X как ингредиент.
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (91301,1,90302,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (91301,1,90301,50),(91301,1,90304,10);
        -- Переработка: Y -> X (легитимно в SDE, экономически абсурдно).
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity)
            VALUES (90302,1,90301,73);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES
            (90303,10000002,50000.0),(90304,10000002,10.0);
        """
    )
    conn.commit()


def test_source_needs_target_detects_direct_ingredient_reference(conn):
    """Прямая проверка чистой функции: Y требует X в своих материалах → True."""
    _seed_reverse_reprocess_ingredient(conn)
    assert sourcing._source_needs_target(conn, 90302, 90301) is True
    assert sourcing._source_needs_target(conn, 90303, 90301) is False  # Filler не требует X


def test_reprocessing_skips_precursor_that_requires_target_as_ingredient(conn):
    """Реальный случай (живой Nomad): переработка
    НИКОГДА не должна предлагать прекурсор, который сам требует целевой материал как
    ингредиент — независимые LLC-пересборки (web/stock_net.py) через общий
    params.reprocess_cache способны замкнуть такую пару в реальный цикл ОБЪЕКТОВ (не просто
    типов), и обход финального дерева упал бы с RecursionError.
    Здесь: единственный «источник переработки» для X (Y) отсекается заранее — X обязан
    остаться на обычном (единственном оставшемся) пути через Filler, а не зациклиться/упасть."""
    _seed_reverse_reprocess_ingredient(conn)
    params = core.build_params_from_config(CFG_ON)
    node = sourcing.build_node_cost(conn, 90301, 1, 1, params, default_me=0)
    assert node.blueprint_source != "reprocess"
    assert node.blueprint_type_id == 91300  # обычный чертёж X через Filler, не переработка через Y


def test_collect_reprocess_byproducts_single_node(conn):
    """Побочка одного узла переработки (Dysporite -> Mercury) — физическое количество, не ISK."""
    _seed(conn)
    params = core.build_params_from_config(CFG_ON)
    node = sourcing.build_node_cost(conn, 16668, 1, 1, params, default_me=0)
    assert node.blueprint_source == "reprocess"

    totals = sourcing.collect_reprocess_byproducts([node])
    assert totals == {16646: ("Mercury", node.reprocess_byproducts[0][2])}
    assert totals[16646][1] > 0


def test_collect_reprocess_byproducts_counts_shared_node_once(conn):
    """Тот же риск задвоения, что и на Crystallite Alloy (см. докстринг ``_walk`` внутри
    ``consolidate_shared_components``): общий узел переработки, достижимый из ДВУХ родителей
    ПОСЛЕ слияния, не должен задвоить свою побочку при обходе ``collect_reprocess_byproducts`` —
    каждый УНИКАЛЬНЫЙ объект побочки считается ровно один раз, не по числу ссылающихся строк."""
    _seed_shared_consumers(conn)
    params = core.build_params_from_config(CFG_ON)
    ship_a = sourcing.build_node_cost(conn, 90101, 1, 1, params, default_me=0)
    ship_b = sourcing.build_node_cost(conn, 90102, 1, 1, params, default_me=0)
    sourcing.consolidate_shared_components(conn, [ship_a, ship_b], params, default_me=0)
    merged = ship_a.lines[0].child
    assert merged is ship_b.lines[0].child

    totals = sourcing.collect_reprocess_byproducts([ship_a, ship_b])
    assert totals[16646][1] == merged.reprocess_byproducts[0][2]  # РОВНО одна порция, не две


def test_reprocessing_off_by_default_uses_normal_blueprint(conn):
    _seed(conn)
    params = core.build_params_from_config(CFG_OFF)
    assert params.reprocessing_efficiency == 0.0
    node = sourcing.build_node_cost(conn, 16668, 1, 1, params, default_me=0)
    assert node.blueprint_source != "reprocess"
    assert node.blueprint_type_id == 46170  # обычный Dysporite Reaction Formula


def test_reprocessing_on_picks_cheaper_precursor_route(conn):
    _seed(conn)
    params = core.build_params_from_config(CFG_ON)
    assert params.reprocessing_efficiency == 0.75

    normal_params = core.build_params_from_config(CFG_OFF)
    normal_node = sourcing.build_node_cost(conn, 16668, 1, 1, normal_params, default_me=0)

    node = sourcing.build_node_cost(conn, 16668, 1, 1, params, default_me=0)
    assert node.blueprint_source == "reprocess"
    assert node.reprocess_source_id == 29660
    assert node.reprocess_source_name == "Unrefined Dysporite"
    assert node.produced >= 200  # runs=1 нормального рецепта даёт 200 Dysporite — планка та же
    assert node.unit_cost < normal_node.unit_cost  # реально дешевле, не просто иной путь
    assert node.reprocess_byproduct_credit > 0  # кредит за побочный Mercury учтён

    # Единственная строка — прекурсор, реально сорсенный (не абстрактная цифра).
    assert len(node.lines) == 1
    precursor = node.lines[0]
    assert precursor.type_id == 29660
    assert precursor.child is not None  # прекурсор сам построен через свой чертёж
    assert precursor.child.blueprint_type_id == 46200


def test_reprocessing_byproduct_credit_not_subtracted_from_cost(conn):
    """Юзер продавать побочку не планирует — кредит остаётся справочным полем
    (``reprocess_byproduct_credit`` > 0, для UI), но НЕ уменьшает material_cost/unit_cost:
    ``material_cost == precursor.subtotal`` РОВНО, без вычета кредита."""
    _seed(conn)
    params = core.build_params_from_config(CFG_ON)
    node = sourcing.build_node_cost(conn, 16668, 1, 1, params, default_me=0)
    assert node.blueprint_source == "reprocess"
    assert node.reprocess_byproduct_credit > 0
    assert node.material_cost == pytest.approx(node.lines[0].subtotal)
    assert node.unit_cost == pytest.approx(node.material_cost / node.produced)


def test_reprocessing_node_schedules_precursor_job_not_a_fake_reprocess_job(conn):
    """Обёртка переработки сама не джоб (activity_id=0/blueprint_type_id=0) — но её
    единственная строка ссылается на РЕАЛЬНУЮ постройку прекурсора, и та обязана попасть в
    расписание (иначе руки юзера не поймут, что физически нужно ЗАПУСТИТЬ реакцию Unrefined
    Dysporite Reaction Formula, чтобы получить материал)."""
    _seed(conn)
    params = core.build_params_from_config(CFG_ON)
    node = sourcing.build_node_cost(conn, 16668, 1, 1, params, default_me=0)
    assert node.activity_id == 0 and node.blueprint_type_id == 0

    jobs = schedule.extract_jobs(node)
    assert len(jobs) == 1  # ТОЛЬКО джоб прекурсора — не два (без фиктивного "джоба переработки")
    assert jobs[0].blueprint_type_id == 46200
    assert jobs[0].product_type_id == 29660


def test_reprocessing_falls_back_to_normal_when_precursor_is_unsourceable(conn):
    """Прекурсор без чертежа И без рыночной цены — совсем не оценить (``_source_material``
    вернёт ``subtotal=None``). Остаёмся на обычном пути постройки, без крашей."""
    _seed(conn)
    conn.executescript(
        """
        DELETE FROM sde_reprocessing_materials WHERE type_id = 29660;
        INSERT INTO sde_types(type_id,name,volume) VALUES (77777,'Ghost Ore',1.0);
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity)
            VALUES (77777,1,16668,1);
        """
    )  # единственный оставшийся «путь переработки» — через 77777, у которого нет ни чертежа, ни цены.
    conn.commit()
    params = core.build_params_from_config(CFG_ON)
    node = sourcing.build_node_cost(conn, 16668, 1, 1, params, default_me=0)
    assert node.blueprint_source != "reprocess"  # выбрали единственный оцениваемый путь — обычный
    assert node.blueprint_type_id == 46170
