"""Отчёт по корзине: остатки на GPLB-C, шейпинг закупок и инструкции по персонажам."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from forge import config as config_mod
from forge.core import cost
from forge.planner.instructions import GANTT_COLORS, build_character_instructions
from forge.planner.schedule import Schedule, Scheduled
from forge.web import report

# Локации/структуры с готовыми id (resolve_locations не нужен) + производственный чар.
CFG = config_mod.loads(
    """
db_path = "x.db"
manufacturing_character_ids = [7]

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
system_id = 30004000
region_id = 10000010

[structures]
gplb_engineering_complex_id = 60005000
"""
)

# Как CFG, но C-J6MT→GPLB-C — рейсовый (fixed_jump) маршрут, не per_m3.
CFG_JUMP = config_mod.loads(
    """
db_path = "x.db"
manufacturing_character_ids = [7]

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
system_id = 30004000
region_id = 10000010

[structures]
gplb_engineering_complex_id = 60005000

[[freight_routes]]
from = "c_j6mt"
to = "gplb_c"
mode = "fixed_jump"
fixed_cost = 1000000.0
vessel_capacity_m3 = 200.0
load_factor = 1.0
"""
)

# Как CFG, но Jita→C-J6MT — per_m3 с минимальной стоимостью доставки за заказ.
CFG_MIN = config_mod.loads(
    """
db_path = "x.db"
manufacturing_character_ids = [7]

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
system_id = 30004000
region_id = 10000010

[structures]
gplb_engineering_complex_id = 60005000

[[freight_routes]]
from = "jita"
to = "c_j6mt"
mode = "per_m3"
isk_per_m3 = 10000.0
min_cost = 5000000.0
"""
)


def _seed_build(conn):
    """Widget(2000) из своего BPO: материалы Tritanium(34, Jita) + Pyerite(35, C-J6MT), по 10 шт."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (2000,'Widget'),(34,'Tritanium'),(35,'Pyerite'),(1100,'Container');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,10),(1000,1,35,10);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,60005000,0,0,-1,-1,0);
        -- Trit дешевле/доступен только в Jita, Pyerite — только в C-J6MT.
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume)
            VALUES (34,10000002,5.0,1000000),(35,10000009,3.0,1000000);
        """
    )
    conn.commit()


def _seed_gplb_assets(conn):
    """Остатки на GPLB-C: Trit×4 прямо в структуре; Pyerite×3 во вложенном контейнере."""
    conn.executescript(
        """
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (900,7,34,60005000,4),
            (901,7,1100,60005000,1),
            (902,7,35,901,3);
        """
    )
    conn.commit()


def test_gplb_on_hand_resolves_nested_containers(conn):
    _seed_build(conn)
    _seed_gplb_assets(conn)
    on_hand = report.gplb_on_hand(conn, CFG)
    assert on_hand[34] == 4          # прямо в структуре
    assert on_hand[35] == 3          # внутри контейнера, лежащего в структуре


def test_gplb_on_hand_excludes_fitted_slot_items(conn):
    """Реальный случай: Multispectrum Shield Hardener II НЕ засчитывается «на складе GPLB-C»,
    если зафитован в слот на Viator, стоящем в ангаре — его там не найти, потому что
    физически он не лежит свободно, а прикручен к кораблю."""
    _seed_build(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (2281,'Multispectrum Shield Hardener II'),(12743,'Viator');
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (950,7,12743,60005000,1),
            (951,7,2281,950,1);
        INSERT INTO character_asset_flags(item_id,character_id,location_flag) VALUES
            (951,7,'MedSlot3');
        """
    )
    conn.commit()
    on_hand = report.gplb_on_hand(conn, CFG)
    assert on_hand.get(2281, 0) == 0


def test_gplb_on_hand_includes_cargo_hold_items(conn):
    """Тот же корабль на складе, но предмет в трюме (не в слоте фита) — реально доступный
    остаток, засчитывается как обычно."""
    _seed_build(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (2281,'Multispectrum Shield Hardener II'),(12743,'Viator');
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (950,7,12743,60005000,1),
            (951,7,2281,950,1);
        INSERT INTO character_asset_flags(item_id,character_id,location_flag) VALUES
            (951,7,'Cargo');
        """
    )
    conn.commit()
    on_hand = report.gplb_on_hand(conn, CFG)
    assert on_hand[2281] == 1


def test_freight_bucket_charges_full_trip_not_diluted_rate(conn):
    """Партия, что РЕАЛЬНО покупаем (после вычета склада), укладывается в 1 рейс —
    «Фрахт» должен показать ПОЛНУЮ стоимость этого рейса (fixed_cost), а не долю от ставки,
    посчитанной на объём ПОЛНОГО (до вычета склада) дерева. Если бы ставку (эффективную
    ISK/m³ для батч-расчёта себестоимости — стабильная база план/факт) переиспользовали для
    отчёта, чем больше на складе, тем НИЖЕ казался бы фрахт — хотя рейс всё равно один и тот
    же. Тут: полная потребность 1000 м³ (без склада — 5 рейсов), но 900 м³ уже на складе;
    реально покупаем только 100 м³ — это 1 рейс, должен стоить fixed_cost целиком."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',10.0),(34,'Tritanium',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,1000);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000009,5.0,1000000);
        -- склад GPLB-C: 900 из 1000 нужных Tritanium уже есть — реально купить остаётся 100.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,34,60005000,900);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG_JUMP, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    freight = next(b for b in p["cost_control"]["buckets"] if b["key"] == "freight")
    # 100 м³ (реальная покупка) < 200 м³ (вместимость) -> ровно 1 рейс -> fixed_cost целиком,
    # НЕ доля от разбавленной ставки (та дала бы 100 × 9000 = 900 000, меньше полного рейса).
    assert freight["plan"] == pytest.approx(1_000_000.0)


def test_freight_bucket_applies_jita_minimum_to_actual_purchase_volume(conn):
    """То же, что и выше, но для минимума за доставку Jita→C-J6MT (per_m3 + min_cost),
    а не для рейсового плеча. Полная потребность 1000 м³ (линейно 1000×10000=10М — минимум
    5М тут ни при чём, батч-ставка = обычная 10000 ISK/м³). Но 900 м³ уже на складе — реально
    покупаем только 100 м³. Если бы «Фрахт» переиспользовал разбавленную ставку (10000 ISK/м³,
    та же, что на полном дереве) на этот меньший объём — вышло бы 100×10000=1М, что НИЖЕ
    минимума за заказ. Правильно — 100 м³ всё равно должны стоить минимум 5М целиком."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',10.0),(34,'Tritanium',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,1000);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        -- склад GPLB-C: 900 из 1000 нужных Tritanium уже есть — реально купить остаётся 100.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,34,60005000,900);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG_MIN, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    freight = next(b for b in p["cost_control"]["buckets"] if b["key"] == "freight")
    assert freight["plan"] == pytest.approx(5_000_000.0)


def test_top_level_product_stock_is_deducted(conn):
    """Остаток склада ПО САМОМУ верхнему запрошенному продукту (не только по его
    материалам) должен уменьшать объём постройки: строим 10 шт., 5 уже готовых лежат на
    GPLB-C — верхний узел строится не в полном объёме (10), склад проверяется и по самому
    продукту (так же, как по материалам внутри дерева)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (31718,'Medium EM Shield Reinforcer I'),(34,'Tritanium');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (31719,1,31718,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (31719,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (31719,1,34,10);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,31719,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        -- 5 готовых Medium EM Shield Reinforcer I уже на складе GPLB-C, просим построить 10.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,31718,60005000,5);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(31718, 10, 1)], now=datetime(2026, 6, 16, 12, 0))
    on_hand = _line_for(p["shopping"]["on_hand"], 31718)
    assert on_hand is not None and on_hand["covered"] == 5 and on_hand["required"] == 10
    own = _line_for(p["shopping"]["own_build"], 31718)
    assert own is not None and own["produced"] == 5   # 10 − 5, а НЕ полные 10


def test_me_override_survives_partial_stock_shrink_of_top_product(conn):
    """«Точный ME/TE» верхнего товара НЕ должен теряться, когда часть запрошенного количества
    уже на складе GPLB-C и верхний узел пересобирается заново на остаток
    (``report.py::_shrink_node``, а не обычная рекурсия ``build_node_cost``) — тот самый путь,
    из-за которого механизм оверрайда сделан по ``product_type_id``, а не по глубине рекурсии
    (см. sourcing.build_node_cost). Тот же сетап, что у test_top_level_product_stock_is_deducted
    (5 из 10 уже на складе → строим 5), но сравниваем себестоимость с/без me_overrides."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (31718,'Medium EM Shield Reinforcer I'),(34,'Tritanium');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (31719,1,31718,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (31719,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (31719,1,34,10);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,31719,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        -- 5 готовых Medium EM Shield Reinforcer I уже на складе GPLB-C, просим построить 10.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,31718,60005000,5);
        """
    )
    conn.commit()
    without = report.build_report_payload(conn, CFG, [(31718, 10, 1)], now=datetime(2026, 6, 16, 12, 0))
    with_override = report.build_report_payload(
        conn, CFG, [(31718, 10, 1)], now=datetime(2026, 6, 16, 12, 0),
        me_overrides={31718: 10},
    )
    own = _line_for(with_override["shopping"]["own_build"], 31718)
    assert own is not None and own["produced"] == 5   # склад вычтен и с оверрайдом (5 из 10)
    # ME10 после пересборки на остаток (5 прогонов) требует МЕНЬШЕ Tritanium, чем ME0 —
    # если бы _shrink_node «терял» оверрайд, total_cost совпали бы.
    assert with_override["totals"]["total_cost"] < without["totals"]["total_cost"]


def test_top_level_product_fully_on_hand_skips_build_entirely(conn):
    """Если запрошенное количество ЦЕЛИКОМ лежит на складе GPLB-C — верхний продукт вообще
    не должен строиться (ни в «Своя постройка», ни материалы/джобы на него не тратятся)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (31718,'Medium EM Shield Reinforcer I'),(34,'Tritanium');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (31719,1,31718,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (31719,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (31719,1,34,10);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,31719,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,31718,60005000,10);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(31718, 10, 1)], now=datetime(2026, 6, 16, 12, 0))
    assert _line_for(p["shopping"]["own_build"], 31718) is None
    on_hand = _line_for(p["shopping"]["on_hand"], 31718)
    assert on_hand is not None and on_hand["covered"] == 10 and on_hand["required"] == 10


def test_jobs_bucket_shrinks_with_stock_covered_subcomponent(conn):
    """«Взносы за джобы» считаются НЕ от ПОЛНОГО (до вычета склада) дерева — джоб на
    под-сборку, целиком или частично покрытую складом, не оплачивается так, будто
    реально запускается на исходный объём (в реальном заказе — 159М по полному дереву против ~115М факт).
    Component нужен 100 шт., но 30 уже на складе — джоб должен считаться на 70, а не на 100."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (4000,'Ship'),(2500,'Component'),(34,'Tritanium');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (2501,1,2500,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (4001,1,600),(2501,1,60);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,100), (2501,1,34,2);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,60005000,0,0,-1,-1,0),
            (2,7,2501,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,5.0), (2500, 1000.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30004000,1,0.05);
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,2500,60005000,30);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(4000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    jobs = next(b for b in p["cost_control"]["buckets"] if b["key"] == "jobs")
    # Ship: eiv=1000*100*1=100000, job=100000*0.05=5000. Component (70 шт.): eiv=5*2*70=700,
    # job=700*0.05=35. Итого 5035, а НЕ 5050 (что было бы для полных 100 шт. Component).
    assert jobs["plan"] == pytest.approx(5035.0)


def test_partial_stock_shrinks_buildable_subcomponent(conn):
    """Частичное покрытие складом БУИДИРУЕМОГО под-компонента должно уменьшать объём его
    постройки, а не только позицию в закупках (реальный кейс: Ishtar/Ion Thruster —
    на складе 61, нужно 350 — план не должен строить все 350): под-дерево компонента
    пересчитывается на remaining, а не на исходное required qty."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (4000,'Ship'),(2500,'Component'),(34,'Tritanium');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (2501,1,2500,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (4001,1,600),(2501,1,60);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,100), (2501,1,34,2);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,60005000,0,0,-1,-1,0),
            (2,7,2501,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        -- склад GPLB-C: 30 из 100 нужных Component уже есть — покрытие ЧАСТИЧНОЕ.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,2500,60005000,30);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(4000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    on_hand = _line_for(p["shopping"]["on_hand"], 2500)
    assert on_hand is not None and on_hand["covered"] == 30 and on_hand["required"] == 100
    own = _line_for(p["shopping"]["own_build"], 2500)
    assert own is not None and own["produced"] == 70   # 100 − 30, а НЕ полные 100


def test_stock_exhaustion_across_sibling_branches_tracks_full_required(conn):
    """Реальный кейс юзера (5× Ishtar, отчёт): общий материал (Thulium Hafnite,
    через Photonic Metamaterials) нужен ДВУМ независимым ветвям дерева (Photon Microprocessor
    и Oscillator Capacitor Unit). Когда склад обнуляется на ПЕРВОЙ ветке,
    ``take_from_stock`` для ВТОРОЙ ветки (``covered == 0``, т.к. взять уже нечего) всё равно
    пишет её спрос в ``on_hand_used`` — иначе «нужно» в таблице «Уже на складе» показало бы ТОЛЬКО
    спрос первой ветки (100), тихо теряя спрос второй (50): в реальном кейсе «221/3 408» на
    складе при 4 400 шт. в «Строить самому» — необъяснимая нестыковка."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES
            (4000,'Ship'),(2500,'ComponentA'),(2501,'ComponentB'),(3000,'SharedMat');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (4002,1,2500,1,1.0), (4003,1,2501,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (4001,1,600),(4002,1,60),(4003,1,60);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,1),(4001,1,2501,1),(4002,1,3000,80),(4003,1,3000,50);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,60005000,0,0,-1,-1,0),
            (2,7,4002,60005000,0,0,-1,-1,0),
            (3,7,4003,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (3000,10000002,5.0,1000000);
        -- склад GPLB-C: 60 из 130 (80+50) нужных SharedMat — хватает только на первую ветку.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,3000,60005000,60);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(4000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    on_hand = _line_for(p["shopping"]["on_hand"], 3000)
    assert on_hand is not None
    assert on_hand["covered"] == 60
    assert on_hand["required"] == 130  # 80 + 50 — ОБЕ ветки, не только первая
    bought = (_line_for(p["shopping"]["jita"], 3000) or _line_for(p["shopping"]["cj"], 3000))["quantity"]
    assert bought == 70  # 130 − 60 покрытых складом (30 + 40 от каждой ветки по отдельности)


def test_consolidate_does_not_double_process_shared_component_in_report(conn):
    """Реальный кейс юзера (5× Ishtar, consolidate=True): после
    sourcing.consolidate_shared_components строка ОДНОГО из родителей (ComponentB) указывает
    на ОБЩИЙ (уже пересобранный) объект, id которого НЕ совпадает с тем, что видел структурный
    обход ДО консолидации (объект пересобран заново). По id(ln.child) «общий ли компонент» не
    определить — после переприсвоения на пересобранный узел этот id не совпадает со
    справочником, и общий под-компонент обработался бы ВТОРОЙ раз (списание склада, джоб,
    стоимость — всё задвоилось бы). Поэтому — по id(ln) самой строки (не меняется при
    переприсвоении child), и реально ОДНА постройка на весь спрос."""
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
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30004000,1,0.0);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(4000, 1, 1)], now=datetime(2026, 6, 16, 12, 0), consolidate=True)
    own = _line_for(p["shopping"]["own_build"], 3000)
    assert own is not None
    assert own["produced"] == 500  # ceil(470/100) — ОДНА общая постройка, не 400+200=600
    # Ровно ОДИН джоб на SharedMat (не два — общий компонент не задваивается).
    shared_jobs = [j for j in p["jobs"] if j["name"] == "SharedMat"]
    assert len(shared_jobs) == 1


def test_consolidate_survives_top_level_stock_shrink_across_basket_items(conn):
    """Реальный кейс юзера (Sylramic Fibers ×50 + Ferrogel ×50 в одной корзине,
    оба тянут общий Hexite): ДВА РАЗНЫХ товара корзины (не ветки одного дерева) делят один
    строящийся под-компонент, И у ОДНОГО из этих товаров есть частичный остаток на складе
    GPLB-C по самому себе (не по материалам) — это заставляет отчёт пересобрать его верхний
    узел заново (``_shrink_node``) свежим, ещё не объединённым поддеревом. Сделай он это ПОСЛЕ
    структурного обхода/консолидации, общий компонент считался бы
    ДВАЖДЫ: один раз как «старая» (уже объединённая) ветка, второй — как «новая» (после
    пересборки, снова не объединённая с соседним товаром). Пересборка склада по
    верхним продуктам идёт ДО консолидации/обхода, поэтому итог — ровно ОДНА общая постройка
    на суммарный (уже уменьшённый складом) спрос."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES
            (4000,'ShipA'),(4010,'ShipB'),(2500,'Component'),(34,'Tritanium');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (4011,1,4010,1,1.0), (2501,1,2500,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (4001,1,600),(4011,1,600),(2501,1,60);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,100), (4011,1,2500,50), (2501,1,34,2);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,60005000,0,0,-1,-1,0),
            (2,7,4011,60005000,0,0,-1,-1,0),
            (3,7,2501,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        -- 1 из 4 запрошенных ShipA уже на складе GPLB-C — частичное покрытие ПО СЕБЕ САМОМУ.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES (900,7,4000,60005000,1);
        """
    )
    conn.commit()
    p = report.build_report_payload(
        conn, CFG, [(4000, 4, 1), (4010, 2, 1)], now=datetime(2026, 6, 16, 12, 0), consolidate=True
    )
    # ShipA: 4 нужно − 1 на складе = 3 строим ⇒ Component 3*100=300. ShipB: 2*50=100. Итого 400,
    # а НЕ 500 (задвоенный старый общий спрос) и НЕ 700 (старая ветка + новая несведённая).
    own = _line_for(p["shopping"]["own_build"], 2500)
    assert own is not None and own["produced"] == 400
    comp_jobs = [j for j in p["jobs"] if j["name"] == "Component"]
    assert len(comp_jobs) == 1
    assert comp_jobs[0]["runs"] == 400


def _seed_vanadium_hafnite_style_bug(conn):
    """Минимальный повтор реального кейса (5× Ishtar, Vanadium Hafnite) без датасета Ishtar:
    Ship(4000) нужно 10 ComponentA(2500) + 10 ComponentB(2501), ОБА сами частично на складе
    (4 и 3 соответственно — не только материал глубже, а сами компоненты). ComponentA тянет
    35 SharedMat(3000)/прогон, ComponentB — 12/прогон; у SharedMat своего остатка нет. SharedMat
    (100/прогон) тянет 1 Tritanium(34)/прогон.

    Ветки шринкуются (ComponentA → 6 прогонов, ComponentB → 7); если бы каждая НЕЗАВИСИМО
    пересобирала/округляла СВОЙ SharedMat: ceil(6×35/100)=3 (300) + ceil(7×12/100)=1 (100) =
    400 суммарно, Tritanium 3+1=4. stock_net.net_and_finalize даёт ОДИН комбинированный спрос
    6×35+7×12=294 → ceil(294/100)=3 прогона (300), Tritanium 3."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (4000,'Ship',1.0),(2500,'ComponentA',1.0),(2501,'ComponentB',1.0),
            (3000,'SharedMat',1.0),(34,'Tritanium',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (4002,1,2500,1,1.0), (4003,1,2501,1,1.0), (4004,1,3000,100,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (4001,1,600),(4002,1,60),(4003,1,60),(4004,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,10),(4001,1,2501,10),
                   (4002,1,3000,35),(4003,1,3000,12),
                   (4004,1,34,1);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,60005000,0,0,-1,-1,0),(2,7,4002,60005000,0,0,-1,-1,0),
            (3,7,4003,60005000,0,0,-1,-1,0),(4,7,4004,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,5.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30004000,1,0.0);
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (900,7,2500,60005000,4), (901,7,2501,60005000,3);
        """
    )
    conn.commit()


def test_shared_component_below_two_independently_shrunk_branches_nets_once(conn):
    """Основной сценарий (не задвоение — НЕДОСчёт): общий SharedMat нужен
    двум веткам, каждая из которых сама частично на складе — итог должен быть ОДНОЙ общей
    постройкой на комбинированный (уже уменьшенный обеими ветками) спрос, не суммой двух
    независимо округлённых построек."""
    _seed_vanadium_hafnite_style_bug(conn)
    p = report.build_report_payload(
        conn, CFG, [(4000, 1, 1)], now=datetime(2026, 6, 16, 12, 0), consolidate=True
    )
    own = _line_for(p["shopping"]["own_build"], 3000)
    assert own is not None
    assert own["produced"] == 300  # НЕ 400 (300+100 — сумма двух независимых округлений)
    shared_jobs = [j for j in p["jobs"] if j["name"] == "SharedMat"]
    assert len(shared_jobs) == 1
    assert shared_jobs[0]["runs"] == 3
    bought = _line_for(p["shopping"]["jita"], 34) or _line_for(p["shopping"]["cj"], 34)
    assert bought is not None
    assert bought["quantity"] == 3  # НЕ 4 (3+1 от независимых построек)


def test_shared_component_below_two_shrunk_branches_consolidate_false_stays_independent(conn):
    """При consolidate=False (юзер явно выключил объединение) — ветки остаются независимыми,
    и это НЕ «неправильное» в этом режиме поведение: 400 суммарно, два джоба."""
    _seed_vanadium_hafnite_style_bug(conn)
    p = report.build_report_payload(
        conn, CFG, [(4000, 1, 1)], now=datetime(2026, 6, 16, 12, 0), consolidate=False
    )
    own = _line_for(p["shopping"]["own_build"], 3000)
    assert own is not None
    assert own["produced"] == 400  # 300 (A) + 100 (B) — намеренно не объединено
    shared_jobs = [j for j in p["jobs"] if j["name"] == "SharedMat"]
    assert len(shared_jobs) == 2
    bought = _line_for(p["shopping"]["jita"], 34) or _line_for(p["shopping"]["cj"], 34)
    assert bought is not None
    assert bought["quantity"] == 4  # 3 (A) + 1 (B)


def test_shared_component_fully_covered_after_both_branches_shrink_is_not_built(conn):
    """Собственный остаток общего SharedMat ПОЛНОСТЬЮ покрывает комбинированный (уже
    уменьшенный обеими ветками) спрос 294 — узел не строится вообще, обе ссылки обнуляются."""
    _seed_vanadium_hafnite_style_bug(conn)
    conn.execute(
        "INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) "
        "VALUES (902,7,3000,60005000,294)"
    )
    conn.commit()
    p = report.build_report_payload(
        conn, CFG, [(4000, 1, 1)], now=datetime(2026, 6, 16, 12, 0), consolidate=True
    )
    assert _line_for(p["shopping"]["own_build"], 3000) is None
    assert [j for j in p["jobs"] if j["name"] == "SharedMat"] == []
    assert _line_for(p["shopping"]["jita"], 34) is None
    assert _line_for(p["shopping"]["cj"], 34) is None
    on_hand = _line_for(p["shopping"]["on_hand"], 3000)
    assert on_hand is not None and on_hand["covered"] == 294 and on_hand["required"] == 294


def test_two_independent_shared_component_groups_both_net_correctly(conn):
    """Синтетический стресс-тест по мотивам реального 5× Ishtar (там тангл из 4 общих
    компонентов сразу — здесь два, ДЛЯ РАЗНЫХ пар веток, отчасти пересекающихся по родителю
    ComponentB): Ship нужно 10×(ComponentA, ComponentB, ComponentC), каждый сам частично на
    складе. ComponentA и ComponentB ОБА тянут SharedMat1; ComponentB И ComponentC ОБА тянут
    SharedMat2 — т.е. ComponentB участвует в ОБОИХ группах разом. Проверяем, что сведение одной
    группы не портит другую (retry-подход, где починка одного дубликата в раунде
    может тихо терять спрос другого, уже верно объединённого, здесь бы не прошёл)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (4000,'Ship',1.0),(2500,'ComponentA',1.0),(2501,'ComponentB',1.0),(2502,'ComponentC',1.0),
            (3000,'SharedMat1',1.0),(3001,'SharedMat2',1.0),(34,'Tritanium',0.01),(35,'Pyerite',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (4001,1,4000,1,1.0), (4002,1,2500,1,1.0), (4003,1,2501,1,1.0), (4006,1,2502,1,1.0),
                   (4004,1,3000,50,1.0), (4005,1,3001,40,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (4001,1,600),(4002,1,60),(4003,1,60),(4006,1,60),(4004,1,600),(4005,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (4001,1,2500,10),(4001,1,2501,10),(4001,1,2502,10),
                   (4002,1,3000,22),
                   (4003,1,3000,13),(4003,1,3001,9),
                   (4006,1,3001,27),
                   (4004,1,34,1), (4005,1,35,1);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,60005000,0,0,-1,-1,0),(2,7,4002,60005000,0,0,-1,-1,0),
            (3,7,4003,60005000,0,0,-1,-1,0),(4,7,4006,60005000,0,0,-1,-1,0),
            (5,7,4004,60005000,0,0,-1,-1,0),(6,7,4005,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume)
            VALUES (34,10000002,5.0,1000000),(35,10000002,3.0,1000000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,5.0),(35,3.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30004000,1,0.0);
        -- ComponentA: 3 из 10 на складе (остаток 7). ComponentB: 2 из 10 (остаток 8).
        -- ComponentC: 4 из 10 (остаток 6). У самих SharedMat1/SharedMat2 остатка нет.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (900,7,2500,60005000,3), (901,7,2501,60005000,2), (902,7,2502,60005000,4);
        """
    )
    conn.commit()
    p = report.build_report_payload(
        conn, CFG, [(4000, 1, 1)], now=datetime(2026, 6, 16, 12, 0), consolidate=True
    )
    # SharedMat1: комбинированный спрос 7×22 (A) + 8×13 (B) = 154+104=258 → ceil(258/50)=6 (300).
    # Независимо было бы ceil(154/50)=4(200) + ceil(104/50)=3(150) = 350.
    sm1 = _line_for(p["shopping"]["own_build"], 3000)
    assert sm1 is not None and sm1["produced"] == 300
    sm1_jobs = [j for j in p["jobs"] if j["name"] == "SharedMat1"]
    assert len(sm1_jobs) == 1 and sm1_jobs[0]["runs"] == 6
    trit = _line_for(p["shopping"]["jita"], 34) or _line_for(p["shopping"]["cj"], 34)
    assert trit is not None and trit["quantity"] == 6

    # SharedMat2: комбинированный спрос 8×9 (B) + 6×27 (C) = 72+162=234 → ceil(234/40)=6 (240).
    # Независимо было бы ceil(72/40)=2(80) + ceil(162/40)=5(200) = 280.
    sm2 = _line_for(p["shopping"]["own_build"], 3001)
    assert sm2 is not None and sm2["produced"] == 240
    sm2_jobs = [j for j in p["jobs"] if j["name"] == "SharedMat2"]
    assert len(sm2_jobs) == 1 and sm2_jobs[0]["runs"] == 6
    pyerite = _line_for(p["shopping"]["jita"], 35) or _line_for(p["shopping"]["cj"], 35)
    assert pyerite is not None and pyerite["quantity"] == 6


def test_invention_materials_appear_in_shopping_list_and_stock_check(conn):
    """Датакоры инвенты — настоящие материалы: попадают в список закупок (Jita/C-J6MT) И
    проверяются против остатков на складе GPLB-C, как и любой обычный материал постройки —
    не только абстрактная ISK-сумма внутри blueprint_cost."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (2185,'T2 Item',1.0),(40,'Mineral',0.01),(1001,'DC A',0.0),(1002,'DC B',0.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,8,2186,10,0.3), (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,8,1001,2),(2184,8,1002,2), (2186,1,40,100);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (2186,1,600);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (40,10000009,10.0,1000000),(1001,10000009,100000.0,1000),(1002,10000009,50000.0,1000);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        -- 3 из 8 нужных DC A уже на складе GPLB-C; DC B своего остатка нет.
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (900,7,1001,60005000,3);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(2185, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    # 1 прогон продукта → bpcs=1, ceil(1/0.3)=4 попытки инвенты → нужно 4×2=8 DC A, 4×2=8 DC B.
    on_hand = _line_for(p["shopping"]["on_hand"], 1001)
    assert on_hand is not None and on_hand["covered"] == 3 and on_hand["required"] == 8
    bought_a = _line_for(p["shopping"]["jita"], 1001) or _line_for(p["shopping"]["cj"], 1001)
    assert bought_a is not None and bought_a["quantity"] == 5   # 8 нужно − 3 со склада
    bought_b = _line_for(p["shopping"]["jita"], 1002) or _line_for(p["shopping"]["cj"], 1002)
    assert bought_b is not None and bought_b["quantity"] == 8   # своего остатка нет — покупаем всё


def test_invention_prep_shows_attempts_and_missing_t1_source_issue(conn):
    """Отчёт (не только Калькулятор) обязан показать (1) плановое число попыток инвенты
    (Т1-копий, округлено вверх) и (2) явный «Нет чертежа»-стиль issue, если T1-чертёж-источник
    инвенты не во владении — маленький inline-бейдж легко не заметить, нужно такое же
    предупреждение «купить чертёж», как обычное «Нет чертежа: Raven»."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (2185,'T2 Item',1.0),(40,'Mineral',0.01),(1001,'DC A',0.0),(1002,'DC B',0.0),(2184,'T1 BP',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,8,2186,10,0.3), (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,8,1001,2),(2184,8,1002,2), (2186,1,40,100);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (2186,1,600);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (40,10000009,10.0,1000000),(1001,10000009,100000.0,1000),(1002,10000009,50000.0,1000);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(2185, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    inv = p["shopping"]["invention"][0]
    assert inv["attempts"] == 4  # ceil(bpcs=1 / probability=0.3)
    issue = next((i for i in p["shopping"]["issues"] if i["kind"] == "bp_missing_invention_source"), None)
    assert issue is not None
    assert issue["name"] == "T1 BP" and issue["for_product"] == "T2 Item"
    html = report.render_report_html(p)
    assert "Нет T1-чертежа для инвенты: T1 BP" in html
    assert "4 попыт" in html


def test_owned_bpc_insufficient_runs_flagged_as_issue(conn):
    """Своя копия чертежа есть, но ранов на ней меньше, чем нужно этому джобу — репорт обязан
    предупредить (реальный кейс: Core Temperature Regulator, копия на 5 прогонов, а надо 10)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        -- копия только на 5 прогонов
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,2186,0,0,1,5,1);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(2185, 10, 1)], now=datetime(2026, 6, 16, 12, 0))
    issue = next((i for i in p["shopping"]["issues"] if i["kind"] == "bpc_runs_insufficient"), None)
    assert issue is not None
    assert issue["name"] == "T2 Item" and issue["owned"] == 5 and issue["needed"] == 10
    own = _line_for(p["shopping"]["own_build"], 2185)
    assert own is not None and own["source"] == "owned_bpc_insufficient"
    html = report.render_report_html(p)
    assert "Не хватает ранов у своей копии чертежа: T2 Item" in html


def test_owned_bpc_insufficient_runs_adds_shortfall_to_invention_prep(conn):
    """Недостача ранов попадает в список инвенты (напр.
    'Заинвентить: X → 5 шт. (N попыт. Т1-копий...)') — раз чертёж РЕАЛЬНО инвентится (T1-
    источник есть), отчёт обязан показать конкретное число попыток на недостачу (5 = 10−5), не
    только общую фразу «докупи копию/инвентни ещё»."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (2185,'T2 Item',1.0),(40,'Mineral',0.01),(1001,'DC A',0.0),(1002,'DC B',0.0),(2184,'T1 BP',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,8,2186,10,0.3), (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,8,1001,2),(2184,8,1002,2), (2186,1,40,100);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (2186,1,600);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES
            (40,10000009,10.0),(1001,10000009,100000.0),(1002,10000009,50000.0);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        -- копия только на 5 прогонов, а нужно 10.
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,2186,0,0,1,5,1);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(2185, 10, 1)], now=datetime(2026, 6, 16, 12, 0))
    issue = next((i for i in p["shopping"]["issues"] if i["kind"] == "bpc_runs_insufficient"), None)
    assert issue is not None and issue["owned"] == 5 and issue["needed"] == 10

    inv = next((r for r in p["shopping"]["invention"] if r["type_id"] == 2185), None)
    assert inv is not None
    assert inv["produced"] == 5      # недостача = 10 − 5, не вся постройка
    assert inv["attempts"] == 4      # bpcs=ceil(5/10)=1, ceil(1/0.3)=4

    src_issue = next((i for i in p["shopping"]["issues"] if i["kind"] == "bp_missing_invention_source"), None)
    assert src_issue is not None and src_issue["for_product"] == "T2 Item"

    html = report.render_report_html(p)
    assert "Заинвентить: <b>T2 Item</b> → 5 шт." in html
    assert "4 попыт" in html


CFG_T1_OVERRIDE = config_mod.loads(
    """
db_path = "x.db"
manufacturing_character_ids = [7]

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
system_id = 30004000
region_id = 10000010

[structures]
gplb_engineering_complex_id = 60005000

[[blueprint_overrides]]
type_id = 2184
per_run = 500000.0
"""
)


def test_owned_bpc_insufficient_shortfall_invention_appears_in_schedule_and_cost_control(conn):
    """Джоб инвенты на недостачу
    ранов обязан появиться в «Сроки по джобам» с реальной план-стоимостью попытки, И эта
    стоимость обязана попасть в бакет «Чертежи/инвента» контроля стоимости (не оставаться
    невидимой суммой 0)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (2185,'T2 Item',1.0),(40,'Mineral',0.01),(1001,'DC A',0.0),(1002,'DC B',0.0),(2184,'T1 BP',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,8,2186,10,0.3), (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,8,1001,2),(2184,8,1002,2), (2186,1,40,100);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES
            (2186,1,600), (2184,8,6000);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES
            (40,10000009,10.0),(1001,10000009,100000.0),(1002,10000009,50000.0);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        -- копия только на 5 прогонов, а нужно 10.
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,2186,0,0,1,5,1);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG_T1_OVERRIDE, [(2185, 10, 1)], now=datetime(2026, 6, 16, 12, 0))

    inv_job = next((j for j in p["jobs"] if j["activity_id"] == cost.INVENTION), None)
    assert inv_job is not None
    assert inv_job["product_type_id"] == 2185
    # план-стоимость попытки = ручная цена T1-копии (500000, job_fee здесь 0 — нет EIV-данных).
    assert inv_job["plan_cost"] == pytest.approx(500000.0 * inv_job["runs"])

    # ИТОГО «Чертежи/инвента» в контроле стоимости — не 0, отражает реальную докупочную инвенту.
    bucket = next(b for b in p["cost_control"]["buckets"] if b["key"] == "blueprints")
    assert bucket["plan"] == pytest.approx(4 * 500000.0)


def test_cost_control_blueprints_bucket_auto_sums_from_invention_jobs(conn):
    """«Чертежи/инвента» в контроле стоимости — авто-сумма из джобов инвенты в таблице «Сроки
    по джобам» (по аналогии с «Взносы за джобы»), не отдельное непонятное число."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (2185,'T2 Item',1.0),(40,'Mineral',0.01),(1001,'DC A',0.0),(1002,'DC B',0.0),
            (41,'T1 Mineral',0.01),(2184,'T1 BP',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,8,2186,10,0.3), (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,8,1001,2),(2184,8,1002,2), (2186,1,40,100), (2184,1,41,1000);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (2186,1,600),(2184,8,600);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (40,10000009,10.0,1000000),(1001,10000009,100000.0,1000),(1002,10000009,50000.0,1000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (41,500.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES
            (30004000,5,0.05),(30004000,8,0.05);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        -- свой T1 BPO → t1_copy_cost и invention_job_fee оба ненулевые.
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (10,7,2184,60005000,0,0,-1,-1,0);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(2185, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    assert any(j["activity_id"] == 8 for j in p["jobs"])  # джобы инвенты реально создались
    bucket = next(b for b in p["cost_control"]["buckets"] if b["key"] == "blueprints")
    assert bucket["plan"] > 0
    inv_jobs_total = sum(j["plan_cost"] for j in p["jobs"] if j["activity_id"] == 8)
    assert inv_jobs_total == pytest.approx(bucket["plan"])
    html = report.render_report_html(p)
    assert 'data-autocost="blueprints"' in html


def test_report_jobs_include_planned_cost(conn):
    """Каждый джоб в «Сроки по джобам» несёт плановую стоимость установки (взнос), линейно
    пропорциональную числу прогонов ЭТОГО конкретного джоба (EIV не округляется на джоб,
    в отличие от материалов — поэтому per-run стоимость постоянна для продукта)."""
    _seed_build(conn)
    conn.executescript(
        """
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,5.0),(35,3.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30004000,1,0.05);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(2000, 10, 2)], now=datetime(2026, 6, 16, 12, 0))
    assert p["jobs"]
    per_run = [j["plan_cost"] / j["runs"] for j in p["jobs"] if j["runs"]]
    assert per_run and all(x > 0 for x in per_run)
    assert all(abs(x - per_run[0]) < 1e-6 for x in per_run)  # константа на run для продукта


def test_report_jobs_include_resource_name_for_copy_button(conn):
    """Каждый джоб несёт ``resource_name`` — чистое название строящегося ресурса (без префикса
    "Инвента: ", который несёт отображаемое ``name``) — для кнопки копирования в интерфейсе,
    чтобы искать чертёж в EVE по имени продукта, а не по надписи джоба целиком."""
    _seed_build(conn)
    conn.executescript(
        """
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,5.0),(35,3.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30004000,1,0.05);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(2000, 10, 2)], now=datetime(2026, 6, 16, 12, 0))
    assert p["jobs"]
    for j in p["jobs"]:
        assert "Инвента: " not in j["resource_name"]
        assert j["resource_name"] == j["name"].removeprefix("Инвента: ")


def test_gplb_on_hand_empty_without_structures(conn):
    _seed_build(conn)
    _seed_gplb_assets(conn)
    cfg2 = config_mod.loads('db_path="x"')  # нет id структур → нечего считать
    assert report.gplb_on_hand(conn, cfg2) == {}


def _line_for(rows, type_id):
    return next((r for r in rows if r["type_id"] == type_id), None)


CFG_REPROCESS = config_mod.loads(
    """
db_path = "x.db"
manufacturing_character_ids = [7]
reaction_character_ids = [7]

[industry]
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
system_id = 30004000
region_id = 10000010

[structures]
gplb_engineering_complex_id = 60005000
"""
)


def _seed_reprocess_dysporite(conn):
    """Как в реальном найденном кейсе: Dysporite(16668) дешевле через переработку Unrefined
    Dysporite(29660, Cadmium-путь), чем напрямую через дорогой Dysprosium."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (16668,'Dysporite',0.2),(29660,'Unrefined Dysporite',1.0),
            (16650,'Dysprosium',0.5),(16643,'Cadmium',0.5),(16646,'Mercury',0.5),
            (4247,'Helium Fuel Block',5.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (46170,11,16668,200,1.0), (46200,11,29660,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (46170,11,4247,5),(46170,11,16646,100),(46170,11,16650,100),
                   (46200,11,4247,5),(46200,11,16643,100),(46200,11,16646,100);
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity) VALUES
            (29660,1,16668,73),(29660,1,16646,173);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (4247,10000002,24000.0,100000),(16646,10000002,3700.0,100000),
            (16650,10000002,60000.0,100000),(16643,10000002,7000.0,100000);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        """
    )
    conn.commit()


def test_reprocess_route_not_collapsed_into_buy_only_for_top_level_item(conn):
    """У reprocess-узла ``blueprint_type_id==0`` (нет обычного industryActivity-
    чертежа) — но он НЕ должен ловиться той же проверкой, что и «непостроиваемое мета/дроп»
    (``blueprint_type_id==0``): иначе ВЕСЬ reprocess-путь схлопнется в тупую покупку по рыночной
    цене напрямую (в /api/cost — produced=1 вместо ожидаемых 200+,
    вся экономия переработки теряется). Dysporite ЗАПРОШЕН НАПРЯМУЮ как товар корзины — именно
    этот путь (не под-материал глубже) идёт через эту проверку в report.py."""
    _seed_reprocess_dysporite(conn)
    p = report.build_report_payload(conn, CFG_REPROCESS, [(16668, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    item = p["items"][0]
    assert item.get("buy_only") is not True  # НЕ схлопнулось в «просто купи по рынку»
    assert item["produced"] >= 200  # реальный выход переработки, не max(1,runs)=1

    # Прекурсор (Unrefined Dysporite) — реальный материал в списке закупок (сборка идёт через
    # его постройку, а не через прямую покупку Dysporite по рынку).
    sh = p["shopping"]
    precursor = _line_for(sh["jita"], 29660) or _line_for(sh["cj"], 29660)
    dysprosium = _line_for(sh["jita"], 16650) or _line_for(sh["cj"], 16650)
    cadmium = _line_for(sh["jita"], 16643) or _line_for(sh["cj"], 16643)
    assert precursor is None  # Unrefined Dysporite сам строится (не покупается) — не лист
    assert cadmium is not None  # сырьё дешёвого пути реально в закупке
    assert dysprosium is None  # а дорогой прямой путь вообще не использовался

    # И джоб на постройку прекурсора реально есть в расписании (не потерян).
    job_names = [j["name"] for j in p["jobs"]]
    assert any("Unrefined Dysporite" in n for n in job_names)


def test_shopping_split_by_hub_without_stock(conn):
    _seed_build(conn)  # без ассетов
    p = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    sh = p["shopping"]
    assert _line_for(sh["jita"], 34)["quantity"] == 10   # Trit → Jita
    assert _line_for(sh["cj"], 35)["quantity"] == 10      # Pyerite → C-J6MT
    assert sh["on_hand"] == []                            # остатков нет
    # своё производство = продукт со своим BPO
    assert any(o["type_id"] == 2000 and o["source"] == "owned_bpo" for o in sh["own_build"])
    assert p["totals"]["total_cost"] == 80.0             # 10×5 + 10×3


def test_stock_nets_purchase_but_not_cost(conn):
    _seed_build(conn)
    base = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    _seed_gplb_assets(conn)
    netted = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))

    sh = netted["shopping"]
    assert _line_for(sh["jita"], 34)["quantity"] == 6    # 10 нужно − 4 в наличии
    assert _line_for(sh["cj"], 35)["quantity"] == 7      # 10 − 3
    on = {r["type_id"]: r["covered"] for r in sh["on_hand"]}
    assert on == {34: 4, 35: 3}
    # Себестоимость НЕ изменилась — остатки вычитаются только из закупки.
    assert netted["totals"]["total_cost"] == base["totals"]["total_cost"] == 80.0


def test_full_coverage_removes_line(conn):
    _seed_build(conn)
    conn.executescript(
        "INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity)"
        " VALUES (910,7,34,60005000,50);"  # Trit полностью покрыт
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    assert _line_for(p["shopping"]["jita"], 34) is None   # покупать не нужно
    assert _line_for(p["shopping"]["on_hand"], 34)["covered"] == 10


def _seed_nested(conn):
    """Widget(2000) ← строить компонент Gadget(3000)×5; Gadget ← покупать Trit(34)×10/прогон.
    Свои BPO на оба. Цена есть только у Trit (в Jita) → Gadget строится, а не покупается."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (2000,'Widget'),(3000,'Gadget'),(34,'Tritanium');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1003,1,3000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,600),(1003,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,3000,5),(1003,1,34,10);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,60005000,0,0,-1,-1,0),(2,7,1003,60005000,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        """
    )
    conn.commit()


def test_built_intermediate_in_stock_is_not_rebuilt(conn):
    """Главный кейс Ishtar: компонент, который мы УМЕЕМ строить, но он уже лежит на складе,
    берём со склада — не строим и не закупаем его сырьё."""
    _seed_nested(conn)
    without = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    # без остатков: Gadget строится (в «своё» и в расписании), его сырьё Trit — в закупке Jita
    assert {o["type_id"] for o in without["shopping"]["own_build"]} == {2000, 3000}
    assert _line_for(without["shopping"]["jita"], 34) is not None
    assert "Gadget" in {j["name"] for j in without["jobs"]}      # в «Контроле сроков» есть джоб Gadget

    conn.executescript(
        "INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity)"
        " VALUES (920,7,3000,60005000,5);"  # 5 готовых Gadget уже на складе GPLB-C
    )
    conn.commit()
    netted = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    sh = netted["shopping"]
    assert _line_for(sh["on_hand"], 3000)["covered"] == 5      # берём со склада
    assert {o["type_id"] for o in sh["own_build"]} == {2000}   # Gadget НЕ строим
    assert _line_for(sh["jita"], 34) is None                   # его сырьё не закупаем
    names = {j["name"] for j in netted["jobs"]}
    assert "Gadget" not in names and "Widget" in names          # в расписании Gadget нет, Widget есть
    # Себестоимость не изменилась — нетим только закупку.
    assert netted["totals"]["total_cost"] == without["totals"]["total_cost"]


def test_report_has_character_instructions(conn):
    _seed_build(conn)
    p = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    assert p["summary"]["jobs"] == 1
    igor = next(c for c in p["characters"] if c["name"] == "Igor")
    assert len(igor["steps"]) == 1
    assert igor["steps"][0]["text"].startswith("Запусти производство")
    # Контроль стоимости — по статьям затрат (не по предметам).
    cc = p["cost_control"]
    assert {b["key"] for b in cc["buckets"]} == {"mat_jita", "mat_cj", "freight", "jobs", "blueprints"}
    assert cc["plan_total"] == sum(b["plan"] for b in cc["buckets"])
    # HTML рендерится и самодостаточен (встроенный payload + S-кривая).
    html = report.render_report_html(p)
    assert "window.__FORGE_REPORT__" in html and "scurve" in html and "data-cost" in html
    # «Взносы за джобы» — авто-сумма из колонки факт-стоимости в таблице сроков.
    assert 'data-autocost="jobs"' in html and 'data-f="cost"' in html


def test_report_title_slug_and_meta_index(conn, tmp_path):
    _seed_build(conn)
    p = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    # читаемый заголовок + уникальное имя файла с составом корзины
    assert p["title"] == "1× Widget"
    assert p["report_id"] == "build-20260616-120000-Widget-x1"
    html = report.render_report_html(p)
    assert "Widget" in html and "Все стройки" in html  # заголовок + ссылка на список

    res = report.write_report(tmp_path, p, html)
    assert (tmp_path / res["filename"]).exists()
    assert (tmp_path / "index.html").exists()                 # страница-список
    meta = json.loads((tmp_path / (p["report_id"] + ".meta.json")).read_text(encoding="utf-8"))
    assert meta["title"] == "1× Widget"
    assert meta["jobs"] == p["summary"]["jobs"]
    assert meta["url"] == res["url"]


def test_unbuildable_item_treated_as_purchase(conn):
    """Предмет без чертежа (мета/дроп) в корзине считается как покупаемый материал."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (40,'Compact MetaMod');
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (40,10000002,1000.0,1000000);
        """
    )
    conn.commit()
    p = report.build_report_payload(conn, CFG, [(40, 3, 1)], now=datetime(2026, 6, 16, 12, 0))
    line = _line_for(p["shopping"]["jita"], 40)
    assert line is not None and line["quantity"] == 3          # покупаем 3 шт в Jita
    assert p["totals"]["total_cost"] == 3000.0                 # 3 × 1000 (фрахт в CFG не задан)
    assert any(it["type_id"] == 40 and it.get("buy_only") for it in p["items"])


def test_force_buy_routes_item_to_shopping_not_build(conn):
    """По решению игрока (force_buy_ids) строящийся предмет уходит в закупку, без джобов/own_build."""
    _seed_build(conn)
    conn.execute("INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (2000,10000009,90.0,1000)")
    conn.commit()
    built = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    assert any(o["type_id"] == 2000 for o in built["shopping"]["own_build"]) and built["summary"]["jobs"] >= 1

    bought = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0),
                                         force_buy_ids={2000})
    assert bought["summary"]["jobs"] == 0                                  # не строим — нет джобов
    assert not any(o["type_id"] == 2000 for o in bought["shopping"]["own_build"])
    assert _line_for(bought["shopping"]["cj"], 2000)["quantity"] == 1        # ушёл в закупку C-J6MT
    assert any(it["type_id"] == 2000 and it.get("buy_only") for it in bought["items"])


def test_external_reservations_held_until_fully_done(tmp_path):
    """Резерв другого отчёта держится ЦЕЛИКОМ, пока не готовы ВСЕ его джобы; удалённый — ноль.

    Резерв НЕ «тает» линейно по общей доле готовых джобов (done/jobs) — это занижало бы
    резерв материала, который потребляют лишь НЕСКОЛЬКО джобов из отчёта, если пользователь
    сперва отмечает готовыми ДРУГИЕ джобы (реальный кейс: недостача топливных блоков при
    нескольких активных отчётах). Резерв бинарный: снимается только на 100% готовности.
    """
    (tmp_path / "build-A.meta.json").write_text(
        json.dumps({"report_id": "build-A", "jobs": 4, "reserved": {"34": 100}}), encoding="utf-8")
    (tmp_path / "build-A.tracking.json").write_text(
        json.dumps({"steps": {"0": {"done": True}, "1": {"done": False},
                              "2": {"done": False}, "3": {"done": False}}}), encoding="utf-8")
    assert report.external_reservations(tmp_path) == {34: 100}         # 1/4 готово — держит ВЕСЬ резерв
    (tmp_path / "build-A.tracking.json").write_text(
        json.dumps({"steps": {"0": {"done": True}, "1": {"done": True},
                              "2": {"done": True}, "3": {"done": True}}}), encoding="utf-8")
    assert report.external_reservations(tmp_path) == {}                # все джобы готовы — резерв снят
    (tmp_path / "build-A.tracking.json").unlink()
    assert report.external_reservations(tmp_path) == {34: 100}         # нет трекинга → 0% → весь резерв
    assert report.external_reservations(tmp_path, exclude_id="build-A") == {}


def test_report_nets_external_reservations(conn):
    """Новый отчёт вычитает со склада то, что зарезервировано другими отчётами."""
    _seed_build(conn)
    conn.execute("INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity)"
                 " VALUES (930,7,34,60005000,15)")
    conn.commit()
    base = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    assert _line_for(base["shopping"]["on_hand"], 34)["covered"] == 10   # склад 15 покрывает нужные 10
    assert base["shopping"]["reserved_external"] is False

    netted = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0),
                                         external_reserved={34: 8})
    assert _line_for(netted["shopping"]["on_hand"], 34)["covered"] == 7  # 15 − 8 (резерв) = 7
    assert _line_for(netted["shopping"]["jita"], 34)["quantity"] == 3    # докупаем 10 − 7
    assert netted["shopping"]["reserved_external"] is True


def test_delete_report_files(conn, tmp_path):
    import pytest
    _seed_build(conn)
    p = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    report.write_report(tmp_path, p, report.render_report_html(p))
    (tmp_path / (p["report_id"] + ".tracking.json")).write_text("{}", encoding="utf-8")

    deleted = report.delete_report_files(tmp_path, p["report_id"])
    assert set(deleted) == {p["report_id"] + s for s in (".html", ".meta.json", ".tracking.json")}
    assert not (tmp_path / (p["report_id"] + ".html")).exists()
    assert report.delete_report_files(tmp_path, p["report_id"]) == []   # повторно — нечего удалять
    with pytest.raises(ValueError):
        report.delete_report_files(tmp_path, "../forge")                # защита от выхода из каталога


def test_report_id_unique_per_basket(conn):
    _seed_build(conn)
    a = report.build_report_payload(conn, CFG, [(2000, 1, 1)], now=datetime(2026, 6, 16, 12, 0))
    b = report.build_report_payload(conn, CFG, [(2000, 5, 1)], now=datetime(2026, 6, 16, 13, 30))
    assert a["report_id"] != b["report_id"]   # разные стройки → разные файлы, не затирают


def test_build_character_instructions_groups_and_orders():
    sched = Schedule(
        items=[
            Scheduled(0, 35, "Pyerite", 11, 2, 7, "Igor", "reaction", 0, 0.0, 3600.0, 0),
            Scheduled(1, 2000, "Widget", 1, 1, 7, "Igor", "manufacturing", 0, 3600.0, 7200.0, 10),
            Scheduled(2, 2001, "Gadget", 1, 1, 8, "Bob", "manufacturing", 0, 0.0, 1200.0, 0),
        ],
        makespan=7200.0,
    )
    plans = build_character_instructions(sched, datetime(2026, 6, 16, 12, 0))
    assert [p.name for p in plans] == ["Igor", "Bob"]        # порядок первого появления
    assert plans[0].color == GANTT_COLORS[0]
    assert plans[1].color == GANTT_COLORS[1]
    igor = plans[0]
    assert [s.order for s in igor.steps] == [1, 2]            # по времени старта
    assert igor.steps[0].text.startswith("Запусти реакцию")  # реакция раньше (start=0)
    assert igor.steps[1].text.startswith("Запусти производство")
    assert igor.steps[0].step_id == 0                         # = job_id
