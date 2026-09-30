"""core.sourcing.consolidate_shared_components: объединение общего под-компонента,
встречающегося в РАЗНЫХ ветках ОДНОГО дерева, в одну общую постройку.
"""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core
from forge.core import sourcing

CFG = config_mod.loads(
    """
db_path = "x.db"
[industry]
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
"""
)


def _seed_shared_component_scenario(conn):
    """Ship(4000) <- ComponentA(2500)x1 + ComponentB(2501)x1, ОБА независимо тянут
    SharedMat(3000) (100 шт./прогон): A нужно 350, B нужно 120. Раздельное округление:
    ceil(350/100)=4 (400) + ceil(120/100)=2 (200) = 600. Общее: ceil(470/100)=5 (500) —
    экономия 100 шт. (меньше округления впустую)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (4000,'Ship',1.0),(2500,'ComponentA',1.0),(2501,'ComponentB',1.0),
            (3000,'SharedMat',1.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (4002,1,2500,1,1.0), (4003,1,2501,1,1.0), (4004,1,3000,100,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,1),(4001,1,2501,1),
                   (4002,1,3000,350),(4003,1,3000,120),
                   (4004,1,34,1);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,0,0,-1,-1,0),(2,7,4002,0,0,-1,-1,0),(3,7,4003,0,0,-1,-1,0),(4,7,4004,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,5.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,1,0.0);
        """
    )
    conn.commit()


def _params(conn=None):
    return core.build_params_from_config(CFG, conn)


def test_consolidate_merges_two_branches_into_one_shared_build(conn):
    _seed_shared_component_scenario(conn)
    p = _params(conn)
    root = sourcing.build_node_cost(conn, 4000, 1, 1, p, allow_build=True)

    # ДО консолидации: две РАЗНЫЕ ветки, каждая со своим округлением (400 + 200 = 600 шт.).
    line_a = next(ln for ln in root.lines if ln.type_id == 2500)
    line_b = next(ln for ln in root.lines if ln.type_id == 2501)
    shared_a = next(ln for ln in line_a.child.lines if ln.type_id == 3000)
    shared_b = next(ln for ln in line_b.child.lines if ln.type_id == 3000)
    assert shared_a.child.produced == 400
    assert shared_b.child.produced == 200
    assert id(shared_a.child) != id(shared_b.child)  # разные объекты — не объединены

    sourcing.consolidate_shared_components(conn, [root], p, allow_build=True)

    line_a2 = next(ln for ln in root.lines if ln.type_id == 2500)
    line_b2 = next(ln for ln in root.lines if ln.type_id == 2501)
    shared_a2 = next(ln for ln in line_a2.child.lines if ln.type_id == 3000)
    shared_b2 = next(ln for ln in line_b2.child.lines if ln.type_id == 3000)
    # ОБЩИЙ объект теперь у обеих строк — суммарно 470 -> ceil до 500 (не 600).
    assert id(shared_a2.child) == id(shared_b2.child)
    assert shared_a2.child.produced == 500
    # Каждая строка расходует ИЗ общей постройки СВОЁ количество (350 и 120), не всю партию.
    assert shared_a2.quantity == 350 and shared_b2.quantity == 120
    assert shared_a2.unit_cost == pytest.approx(shared_b2.unit_cost)  # одна и та же цена/шт.


def test_consolidate_auto_streams_restores_parallelism_without_losing_savings(conn):
    """Объединение материалов само по себе убирает параллелизм: без него ComponentA и
    ComponentB строят СВОЙ SharedMat на РАЗНЫХ слотах ОДНОВРЕМЕННО, после объединения — это
    ОДИН узел, и без ``auto_streams`` он идёт ОДНИМ последовательным джобом (реальный кейс
    юзера: Crystalline Carbonide у Ishtar ×5 — без объединения 7 веток идут параллельно по чарам,
    с объединением без auto_streams — одним джобом на одном слоте, дольше). При
    ``auto_streams=True`` общая постройка получает число потоков = число слитых веток (тут
    2 — ComponentA и ComponentB), НЕ теряя экономию материала (500, не 600)."""
    _seed_shared_component_scenario(conn)
    p = _params(conn)
    root = sourcing.build_node_cost(conn, 4000, 1, 1, p, allow_build=True)

    sourcing.consolidate_shared_components(conn, [root], p, allow_build=True, auto_streams=True)

    line_a = next(ln for ln in root.lines if ln.type_id == 2500)
    line_b = next(ln for ln in root.lines if ln.type_id == 2501)
    shared = next(ln for ln in line_a.child.lines if ln.type_id == 3000).child
    assert id(shared) == id(next(ln for ln in line_b.child.lines if ln.type_id == 3000).child)
    assert shared.produced == 500        # экономия материала сохранена (не 600)
    assert shared.streams == 2           # 2 слитые ветки => 2 параллельных потока, не 1


def test_consolidate_auto_streams_ignored_without_it(conn):
    """Без ``auto_streams`` (дефолт) объединённая постройка идёт ОДНИМ потоком —
    параллелизм по слитым веткам включается только явно."""
    _seed_shared_component_scenario(conn)
    p = _params(conn)
    root = sourcing.build_node_cost(conn, 4000, 1, 1, p, allow_build=True)

    sourcing.consolidate_shared_components(conn, [root], p, allow_build=True)

    shared = next(ln for ln in root.lines if ln.type_id == 2500).child.lines[0].child
    assert shared.streams == 1


def test_consolidate_updates_ancestor_totals(conn):
    """После объединения material_cost/total_cost родителей должны пересчитаться под НОВЫЙ
    (меньший из-за меньшего округления) unit_cost общего компонента, а не остаться от
    старой (раздельной, более дорогой) постройки."""
    _seed_shared_component_scenario(conn)
    p = _params(conn)
    root = sourcing.build_node_cost(conn, 4000, 1, 1, p, allow_build=True)
    before_total = root.total_cost

    sourcing.consolidate_shared_components(conn, [root], p, allow_build=True)

    # Меньше округления => меньше (или равна) суммарная стоимость материалов корабля.
    assert root.total_cost <= before_total
    # material_cost корня — сумма subtotal его строк (ComponentA/ComponentB), пересчитана.
    assert root.material_cost == pytest.approx(sum(ln.subtotal for ln in root.lines if ln.subtotal is not None))


def test_consolidate_merges_across_different_basket_items(conn):
    """Общий под-компонент, встречающийся в РАЗНЫХ товарах корзины (не только внутри одного
    дерева), тоже объединяется — соответствует замыслу чекбокса «Объединять
    общие компоненты» (он мёржит между товарами корзины и ДЖОБЫ, и
    материалы/стоимость)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (5000,'Alpha',1.0),(5001,'Beta',1.0),(3000,'SharedMat',1.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (5100,1,5000,1,1.0), (5101,1,5001,1,1.0), (4004,1,3000,100,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (5100,1,3000,350),(5101,1,3000,120),(4004,1,34,1);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,5100,0,0,-1,-1,0),(2,7,5101,0,0,-1,-1,0),(3,7,4004,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        """
    )
    conn.commit()
    p = _params(conn)
    alpha = sourcing.build_node_cost(conn, 5000, 1, 1, p, allow_build=True)
    beta = sourcing.build_node_cost(conn, 5001, 1, 1, p, allow_build=True)
    shared_alpha = next(ln for ln in alpha.lines if ln.type_id == 3000)
    shared_beta = next(ln for ln in beta.lines if ln.type_id == 3000)
    assert shared_alpha.child.produced == 400  # ceil(350/100)
    assert shared_beta.child.produced == 200   # ceil(120/100)

    sourcing.consolidate_shared_components(conn, [alpha, beta], p, allow_build=True)

    shared_alpha2 = next(ln for ln in alpha.lines if ln.type_id == 3000)
    shared_beta2 = next(ln for ln in beta.lines if ln.type_id == 3000)
    assert id(shared_alpha2.child) == id(shared_beta2.child)
    assert shared_alpha2.child.produced == 500  # ceil(470/100), не 600


def test_consolidate_does_not_inflate_demand_through_revisited_shared_node(conn):
    """Реальный кейс юзера (5× Ishtar, отчёт по Crystallite Alloy): НЕСКОЛЬКО
    уровней вложенной общности одновременно. ComponentA и ComponentB делят MidPart (сливается
    в раунде 1); сам MidPart в своём чертеже требует SharedMat — который НАПРЯМУЮ нужен ещё и
    ComponentC (отдельная, третья ветка). Внутренний ``_walk()`` в
    ``consolidate_shared_components`` помнит посещённые узлы: после слияния MidPart
    достижим из ДВУХ строк (ComponentA и ComponentB), но на раунде 2 обходится ОДИН раз —
    иначе его строка SharedMat попала бы в ``occurrences`` дважды (дубль одного
    и того же объекта MaterialLine), суммарный спрос на SharedMat завысился бы (50+50+150=250
    вместо верных 50+150=200), и итоговая постройка вышла бы НА ЦЕЛЫЙ ЛИШНИЙ ПРОГОН больше
    нужного (в реальном кейсе — Crystallite Alloy на 480 прогонов вместо верных ~86)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (4000,'Ship',1.0),(2500,'ComponentA',1.0),(2501,'ComponentB',1.0),
            (2502,'ComponentC',1.0),(2600,'MidPart',1.0),(3000,'SharedMat',1.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (4002,1,2500,1,1.0), (4003,1,2501,1,1.0),
                   (4005,1,2502,1,1.0), (2601,1,2600,1,1.0), (4004,1,3000,100,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,1),(4001,1,2501,1),(4001,1,2502,1),
                   (4002,1,2600,3),(4003,1,2600,2),
                   (4005,1,3000,150),
                   (2601,1,3000,10),
                   (4004,1,34,1);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,0,0,-1,-1,0),(2,7,4002,0,0,-1,-1,0),(3,7,4003,0,0,-1,-1,0),
            (4,7,4005,0,0,-1,-1,0),(5,7,2601,0,0,-1,-1,0),(6,7,4004,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        """
    )
    conn.commit()
    p = _params(conn)
    root = sourcing.build_node_cost(conn, 4000, 1, 1, p, allow_build=True)

    sourcing.consolidate_shared_components(conn, [root], p, allow_build=True)

    line_c = next(ln for ln in root.lines if ln.type_id == 2502)
    shared_mat_direct = next(ln for ln in line_c.child.lines if ln.type_id == 3000)
    # MidPart слит (3+2=5 прогонов) => его SharedMat = 5*10=50; ComponentC напрямую = 150.
    # Верно: ceil((50+150)/100)=2 прогона => produced=200 — НЕ 300 (задвоенные 50 от MidPart).
    assert shared_mat_direct.child.produced == 200
    assert shared_mat_direct.child.runs == 2


def test_recompute_aggregates_propagates_child_savings_through_grandparent():
    """Реальный кейс юзера (5× Ishtar, себестоимость): консолидация ДЕЙСТВИТЕЛЬНО
    делает общий компонент дешевле (пересобран с меньшим округлением), и экономия доходит
    не только до НЕПОСРЕДСТВЕННОГО родителя, но и выше, даже если сам этот
    родитель не был напрямую объединён (только его РЕБЁНОК). ``_recompute_aggregates``
    пересчитывает material_cost узла, освежая ``ln.subtotal`` под НОВЫЙ (уже пересчитанный
    рекурсией) ``ln.child.unit_cost`` — иначе экономия застряла бы на первом уровне вверх от
    объединённого узла, и себестоимость ВСЕГО Ishtar была бы БИТ-В-БИТ идентична при
    ``consolidate=True`` и без консолидации вообще (Crystallite Alloy — на 3 уровня ниже
    Ishtar), хотя материала реально уходит меньше.

    Тест — прямая проверка контракта ``_recompute_aggregates`` без реального BOM: Ship ->
    ComponentA -> SharedNode (2 уровня). ``SharedNode`` уже «удешевлён» (как будто консолидация
    его только что пересобрала); строка ComponentA -> SharedNode уже освежена под новую цену
    (как делает сам ``consolidate_shared_components`` для строк НАПРЯМУЮ объединяемых). Но
    material_cost/unit_cost САМОГО ComponentA и строка Ship -> ComponentA — ЕЩЁ старые
    (протухшие), как до пересчёта. После ``_recompute_aggregates(ship)`` обе величины должны
    дойти до КОРНЯ, а не застрять на ComponentA."""
    shared_buy_line = sourcing.MaterialLine(34, "Tritanium", 64, "buy", 5.0, 320.0)  # 320 = material_cost ниже
    shared = sourcing.NodeResult(3000, "SharedNode", 1, 4004, 4, 1, 400, 320.0, 0.0, 320.0, 0.8, lines=[shared_buy_line])
    line_to_shared = sourcing.MaterialLine(3000, "SharedNode", 50, "build", 0.8, 40.0, child=shared)
    component_a = sourcing.NodeResult(
        2500, "ComponentA", 1, 4002, 1, 1, 1, 500.0, 0.0, 500.0, 500.0, lines=[line_to_shared],
    )  # material_cost/unit_cost=500 — ПРОТУХШИЕ (реальная цена строки уже 40, а не 500)
    line_to_a = sourcing.MaterialLine(2500, "ComponentA", 1, "build", 500.0, 500.0, child=component_a)
    ship = sourcing.NodeResult(4000, "Ship", 1, 4001, 1, 1, 1, 500.0, 0.0, 500.0, 500.0, lines=[line_to_a])

    sourcing._recompute_aggregates(ship)

    assert component_a.material_cost == pytest.approx(40.0)   # 50 * 0.8, не протухшие 500
    assert component_a.unit_cost == pytest.approx(40.0)
    assert line_to_a.subtotal == pytest.approx(40.0)           # освежена под новый unit_cost ComponentA
    assert ship.material_cost == pytest.approx(40.0)           # дошло до корня, а НЕ застряло на 500
    assert ship.total_cost == pytest.approx(40.0)


def test_consolidate_leaves_single_occurrence_untouched(conn):
    """Компонент, встречающийся только В ОДНОЙ ветке, не трогаем — нечего объединять."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (4000,'Ship',1.0),(2500,'ComponentA',1.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (4002,1,2500,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,1),(4002,1,34,10);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,0,0,-1,-1,0),(2,7,4002,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        """
    )
    conn.commit()
    p = _params(conn)
    root = sourcing.build_node_cost(conn, 4000, 1, 1, p, allow_build=True)
    before = root.lines[0].child
    sourcing.consolidate_shared_components(conn, [root], p, allow_build=True)
    after = root.lines[0].child
    assert id(before) == id(after)  # объект не подменён
