"""web.stock_net.net_and_finalize (Фаза 2) — изолированно от report.py: реальный ``conn`` +
``core.sourcing.build_node_cost``/``consolidate_shared_components``, но собственные, простые
версии колбэков (``take_from_stock``/``classify_built``/``add_buy``/``note_no_price``) — прямые
ассерты на возвращённый ``current`` (не на полный ``build_report_payload``).

Топология воспроизводит реальный кейс 5× Ishtar (Vanadium Hafnite): Ship нужен
ComponentA и ComponentB, ОБА сами частично на складе (не только материал глубже), оба тянут
ОДИН общий SharedMat. Если каждая ветка независимо шринкует и округляет свой SharedMat, выходит
400 суммарно вместо 300 — см. докстринг ``forge/web/stock_net.py``.
"""

from __future__ import annotations

from forge import config as config_mod
from forge import core
from forge.core import sourcing
from forge.core.stock import gplb_on_hand
from forge.web import stock_net

CFG = config_mod.loads(
    """
db_path = "x.db"
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


def _seed(conn):
    """Ship(4000) x1 нужно 10 ComponentA(2500) + 10 ComponentB(2501). Склад: 4 ComponentA
    (остаток 6), 3 ComponentB (остаток 7) — партиальное покрытие САМИХ компонентов, не
    материала глубже. ComponentA тянет 35 SharedMat(3000)/прогон, ComponentB — 12/прогон.
    SharedMat(100/прогон) тянет 1 Tritanium(34)/прогон. У SharedMat своего остатка нет."""
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
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,4001,0,0,-1,-1,0),(2,7,4002,0,0,-1,-1,0),(3,7,4003,0,0,-1,-1,0),(4,7,4004,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34,10000002,5.0,1000000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,5.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30004000,1,0.0);
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (900,7,2500,60005000,4), (901,7,2501,60005000,3);
        """
    )
    conn.commit()


class _Callbacks:
    """Минимальные версии report.py's замыканий — только то, что нужно для ассертов."""

    def __init__(self, ledger):
        self.ledger = dict(ledger)
        self.built: dict[int, int] = {}  # product_type_id -> суммарный produced
        self.bought: dict[int, int] = {}  # type_id -> суммарное купленное количество
        self.no_price: list[int] = []
        self.take_calls: list[int] = []  # type_id каждого вызова take_from_stock (для ассертов)

    def take_from_stock(self, type_id, name, quantity, unit_cost):
        self.take_calls.append(type_id)
        have = self.ledger.get(type_id, 0)
        covered = min(have, quantity) if have > 0 else 0
        if covered > 0:
            self.ledger[type_id] = have - covered
        return quantity - covered

    def classify_built(self, node):
        self.built[node.product_type_id] = self.built.get(node.product_type_id, 0) + node.produced

    def add_buy(self, type_id, name, quantity, unit, buy_hub, shortage):
        self.bought[type_id] = self.bought.get(type_id, 0) + quantity

    def note_no_price(self, type_id, name, quantity):
        self.no_price.append(type_id)


def _build_pass1(conn, params, product_type_id=4000):
    """Проход 1: то, что report.py уже строит СЕГОДНЯ до вызова stock_net — полное (без учёта
    склада) дерево + core-level consolidate_shared_components, БЕЗ ИЗМЕНЕНИЙ."""
    ship = sourcing.build_node_cost(conn, product_type_id, 1, 1, params)
    sourcing.consolidate_shared_components(conn, [ship], params)
    return ship


def test_net_and_finalize_combines_demand_before_rounding_instead_of_per_branch(conn):
    _seed(conn)
    params = core.build_params_from_config(CFG, conn)
    ship = _build_pass1(conn, params)
    ledger = gplb_on_hand(conn, CFG)
    cb = _Callbacks(ledger)

    current = stock_net.net_and_finalize(
        conn, [ship], params, consolidate=True,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={},
    )

    shared_key = (3000, 4004, 1)
    assert shared_key in current
    shared_final = current[shared_key]
    assert shared_final is not None
    # Комбинированный спрос: 6×35 (ComponentA после шринка) + 7×12 (ComponentB) = 294,
    # ceil(294/100) = 3 прогона → 300 (НЕ 400 — сумма двух НЕЗАВИСИМЫХ округлений 300+100).
    assert shared_final.produced == 300
    assert cb.built[3000] == 300
    # Tritanium: 3 (по одному общему прогону), а не 4 (3 от независимого A + 1 от независимого B).
    assert cb.bought[34] == 3


def test_net_and_finalize_exactly_one_current_entry_per_shared_key(conn):
    """current — ровно одна запись на общий ключ (не по одной на родителя)."""
    _seed(conn)
    params = core.build_params_from_config(CFG, conn)
    ship = _build_pass1(conn, params)
    ledger = gplb_on_hand(conn, CFG)
    cb = _Callbacks(ledger)

    current = stock_net.net_and_finalize(
        conn, [ship], params, consolidate=True,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={},
    )
    shared_keys = [k for k in current if k[0] == 3000]
    assert len(shared_keys) == 1


def test_net_and_finalize_splices_both_parent_lines_to_the_same_final_node(conn):
    """После сплайса ОБА родителя (ComponentA/ComponentB) ссылаются на ОДИН и тот же (по
    identity) финальный SharedMat-узел — не на два разных."""
    _seed(conn)
    params = core.build_params_from_config(CFG, conn)
    ship = _build_pass1(conn, params)
    ledger = gplb_on_hand(conn, CFG)
    cb = _Callbacks(ledger)

    stock_net.net_and_finalize(
        conn, [ship], params, consolidate=True,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={},
    )

    comp_a = next(ln.child for ln in ship.lines if ln.type_id == 2500)
    comp_b = next(ln.child for ln in ship.lines if ln.type_id == 2501)
    shared_from_a = next(ln.child for ln in comp_a.lines if ln.type_id == 3000)
    shared_from_b = next(ln.child for ln in comp_b.lines if ln.type_id == 3000)
    assert shared_from_a is shared_from_b


def test_net_and_finalize_inherits_pass1_streams_when_no_deadline_override(conn):
    """Без явного «Макс. дней/поток» (``substream_fn`` не задан) пересборка обязана
    унаследовать streams, который Проход 1 уже дал общей постройке через ``auto_streams``
    (столько потоков, сколько веток слито) — а не молча схлопнуть его к 1: та же природа
    несоответствия, что и с buy/build (см. ``build_report_payload``/``pass1_market_only``) —
    «ВСЕГДА пересобираем заново» без переноса решения Прохода 1 тихо стирает то, что тот
    явно посчитал. Здесь: ComponentA/ComponentB сливаются в общий SharedMat — auto_streams
    даёт ему 2 потока (по числу веток); после шринка (склад ComponentA/ComponentB) новый спрос
    даёт ceil(294/100)=3 прогона — streams обязаны остаться 2 (min(2, 3)), не схлопнуться в 1."""
    _seed(conn)
    params = core.build_params_from_config(CFG, conn)
    ship = sourcing.build_node_cost(conn, 4000, 1, 1, params)
    sourcing.consolidate_shared_components(conn, [ship], params, auto_streams=True)
    assert ship.lines[0].child.lines[0].child.streams == 2  # Проход 1: auto_streams дал 2 ветки → 2 потока

    ledger = gplb_on_hand(conn, CFG)
    cb = _Callbacks(ledger)
    current = stock_net.net_and_finalize(
        conn, [ship], params, consolidate=True,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={},  # нет substream_fn — «Макс. дней/поток» не задан
    )

    shared_final = current[(3000, 4004, 1)]
    assert shared_final.runs == 3     # ceil(294/100) — объединённый спрос
    assert shared_final.streams == 2  # унаследовано от Прохода 1, НЕ схлопнуто в 1


def test_net_and_finalize_recomputes_streams_for_rebuilt_node_not_stale_value(conn):
    """Реальный случай (переработка Nomad, «Макс. дней/поток» ≤3 — без пересчёта выходило 447
    дней): пересборка общего компонента ПОСЛЕ LLC-нэттинга не передаёт в build_node_cost
    СТАРЫЙ node.streams (посчитан Проходом 1 под МЕНЬШИЙ, ещё не объединённый спрос ветки),
    а пересчитывает потоки под НОВЫЙ (объединённый, обычно бОльший) new_runs через
    substream_fn — иначе «Макс. дней/поток» тихо перестаёт соблюдаться именно там, где
    объединение НАРАЩИВАЕТ спрос сильнее всего. Здесь: substream_fn — «ловушка», отдающая
    заведомо ДРУГОЕ число потоков (7) ИМЕННО для итогового new_runs=3 (готовый SharedMat после
    объединения — см. соседний тест), и 1 для любого другого runs (в т.ч. то, что видел Проход 1
    по отдельной ветке) — итоговый узел обязан получить 7, а не унаследованное 1."""
    _seed(conn)
    params = core.build_params_from_config(CFG, conn)
    ship = _build_pass1(conn, params)
    ledger = gplb_on_hand(conn, CFG)
    cb = _Callbacks(ledger)

    def trap_substream_fn(type_id, runs):
        return 7 if runs == 3 else 1

    current = stock_net.net_and_finalize(
        conn, [ship], params, consolidate=True,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={"substream_fn": trap_substream_fn},
    )

    shared_final = current[(3000, 4004, 1)]
    assert shared_final.runs == 3       # ceil(294/100) — объединённый спрос, не 1 и не 2 по ветке
    assert shared_final.streams == 7    # пересчитано под НОВЫЙ runs, не унаследовано от Прохода 1


def _seed_reprocess(conn):
    """Ship(5000) нужно ComponentA(2600)×1 (тянет SharedX×35) + ComponentB(2601)×1 (тянет
    SharedX×12). SharedX(3100) — реакционный продукт: обычный чертёж (200 ед./прогон, дорогое
    сырьё) ИЛИ переработка дешёвого CheapPrecursor(3102) (73 ед./порция при 100% эфф.). Своего
    остатка нет ни у чего — net_and_finalize обязан пересобрать SharedX (не корень)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (5000,'Ship',1.0),(2600,'ComponentA',1.0),(2601,'ComponentB',1.0),
            (3100,'SharedX',0.2),(3101,'ExpensiveRaw',0.5),(3102,'CheapPrecursor',1.0),
            (3103,'CheapRaw',0.5),(4247,'Helium Fuel Block',5.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (5001,1,5000,1,1.0), (5002,1,2600,1,1.0), (5003,1,2601,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (5001,1,600),(5002,1,60),(5003,1,60);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (5001,1,2600,1),(5001,1,2601,1),
                   (5002,1,3100,35),(5003,1,3100,12);
        -- SharedX: обычный (дорогой) путь через ExpensiveRaw, 200 ед./прогон.
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (5004,11,3100,200,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (5004,11,3101,1000);
        -- CheapPrecursor: дешёвый путь для переработки SharedX.
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (5005,11,3102,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (5005,11,4247,5),(5005,11,3103,100);
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity)
            VALUES (3102,1,3100,73);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,5001,0,0,-1,-1,0),(2,7,5002,0,0,-1,-1,0),(3,7,5003,0,0,-1,-1,0),
            (4,7,5004,0,0,-1,-1,0),(5,7,5005,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (3101,10000002,50000.0,1000000),(4247,10000002,24000.0,1000000),(3103,10000002,10.0,1000000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (3101,50000.0),(4247,24000.0),(3103,10.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30004000,1,0.0),(30004000,11,0.0);
        """
    )
    conn.commit()


CFG_REPRO = config_mod.loads(
    """
db_path = "x.db"
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


def test_net_and_finalize_rebuild_uses_real_blueprint_qty_per_run_not_reprocess_runs_field(conn):
    """Реальный случай (Nomad, эфф. переработки 55% — Unrefined Prometium: адекватные ~240
    прогонов, а не 46 397 в отчёте): узел-обёртка переработки хранит в поле
    ``runs`` НЕ «прогонов чертежа», а ``target_quantity`` (запрошенное количество) — формула
    ``qty_per_run = node.produced / node.runs`` для такого узла дала бы ≈1 (produced
    почти равен target), и REMAINING (в единицах ПРОДУКТА) ушёл бы в build_node_cost как
    «прогонов ЧЕРТЕЖА» — тот умножил бы его на СВОЙ РЕАЛЬНЫЙ qty_per_run (200) ЕЩЁ РАЗ, раздувая
    итог в сотни раз. Здесь: SharedX нужен ДВУМ компонентам (35+12=47) — после пересборки
    net_and_finalize обязан дать РАЗУМНОЕ produced (сравнимое с реальным спросом ~47, с округлением
    до целого прогона чертежа/порции переработки — НЕ на порядки больше)."""
    _seed_reprocess(conn)
    params = core.build_params_from_config(CFG_REPRO, conn)
    ship = _build_pass1(conn, params, product_type_id=5000)
    ledger = gplb_on_hand(conn, CFG_REPRO)
    cb = _Callbacks(ledger)

    current = stock_net.net_and_finalize(
        conn, [ship], params, consolidate=True,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={"default_me": 0, "default_te": 0, "allow_build": True},
    )

    shared_keys = [k for k in current if k[0] == 3100]
    assert len(shared_keys) == 1
    shared_final = current[shared_keys[0]]
    assert shared_final is not None
    # Ошибка в qty_per_run раздула бы produced в 40-200 раз (напр. Unrefined Prometium: 240 -> 46 397,
    # ~193x) — верный итог остаётся В ПРЕДЕЛАХ округления до целого прогона чертежа/порции переработки
    # (SharedX 200 ед./прогон, спрос 47 -> максимум 1 прогон "нормального" масштаба = 200-300 ед.).
    assert shared_final.produced < 500
    assert shared_final.produced >= 47


def test_net_and_finalize_independent_branches_rebuild_uses_real_blueprint_qty_per_run(conn):
    """Та же ловушка (см. предыдущий тест), но в СЕСТРИНСКОМ пути ``_net_independent_branches``
    (``consolidate=False``) — переработка триггерится независимо от флага объединения, так что
    узел-обёртка с тем же несоответствием семантики ``runs`` встречается и здесь. У фикстуры
    вообще нет остатков ни на чём — ``remaining`` всегда равен ``gross_demand`` > 0, так что
    ``net_one`` безусловно пересобирает SharedX."""
    _seed_reprocess(conn)
    params = core.build_params_from_config(CFG_REPRO, conn)
    ship = sourcing.build_node_cost(conn, 5000, 1, 1, params)  # БЕЗ consolidate_shared_components
    ledger = gplb_on_hand(conn, CFG_REPRO)
    cb = _Callbacks(ledger)

    stock_net.net_and_finalize(
        conn, [ship], params, consolidate=False,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={"default_me": 0, "default_te": 0, "allow_build": True},
    )

    comp_a = next(ln.child for ln in ship.lines if ln.type_id == 2600)
    shared_from_a = next(ln.child for ln in comp_a.lines if ln.type_id == 3100)
    assert shared_from_a.produced < 500
    assert shared_from_a.produced >= 35


def test_net_and_finalize_consolidate_false_keeps_branches_independent(conn):
    """При consolidate=False — БЕЗ объединения: два отдельных SharedMat, суммарно 400
    (300 от A + 100 от B, каждая ветка округляется сама по себе) — обычный режим без объединения."""
    _seed(conn)
    params = core.build_params_from_config(CFG, conn)
    ship = sourcing.build_node_cost(conn, 4000, 1, 1, params)  # БЕЗ consolidate_shared_components
    ledger = gplb_on_hand(conn, CFG)
    cb = _Callbacks(ledger)

    stock_net.net_and_finalize(
        conn, [ship], params, consolidate=False,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={},
    )
    assert cb.built[3000] == 400  # 300 (A) + 100 (B) — НЕ объединено, как и просил юзер
    assert cb.bought[34] == 4     # 3 (A) + 1 (B)


def test_net_and_finalize_fully_covered_shared_node_drops_both_references(conn):
    """Собственный остаток общего узла ПОЛНОСТЬЮ покрывает суммарный (уже уменьшенный обоими
    родителями) спрос — обе ссылающиеся строки должны стать child=None, узел не строится."""
    _seed(conn)
    conn.execute(
        "INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) "
        "VALUES (902,7,3000,60005000,294)"  # ровно комбинированный спрос
    )
    conn.commit()
    params = core.build_params_from_config(CFG, conn)
    ship = _build_pass1(conn, params)
    ledger = gplb_on_hand(conn, CFG)
    cb = _Callbacks(ledger)

    stock_net.net_and_finalize(
        conn, [ship], params, consolidate=True,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={},
    )
    comp_a = next(ln.child for ln in ship.lines if ln.type_id == 2500)
    comp_b = next(ln.child for ln in ship.lines if ln.type_id == 2501)
    shared_from_a = next(ln for ln in comp_a.lines if ln.type_id == 3000)
    shared_from_b = next(ln for ln in comp_b.lines if ln.type_id == 3000)
    assert shared_from_a.child is None
    assert shared_from_b.child is None
    assert 3000 not in cb.built
    assert cb.bought.get(34, 0) == 0


def test_net_and_finalize_skips_stock_lookup_when_all_parents_fully_covered(conn):
    """И ComponentA, И ComponentB САМИ целиком на складе (не только материал глубже) — значит
    ни одна ветка вообще не строится, и общий SharedMat реально не нужен ни в каком количестве.
    Иначе отчёт всё равно показал бы SharedMat в «уже на складе» строкой 0/0 (нашёл бы
    несвязанные остатки SharedMat в ledger от других причин) — шум без экономии. Правильное
    поведение: take_from_stock для SharedMat вообще не вызывается, когда gross_demand == 0."""
    _seed(conn)
    conn.execute(
        "INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) "
        "VALUES (903,7,2500,60005000,6), (904,7,2501,60005000,7), "
        "(905,7,3000,60005000,50)"  # SharedMat само на складе по несвязанной причине
    )
    conn.commit()
    params = core.build_params_from_config(CFG, conn)
    ship = _build_pass1(conn, params)
    ledger = gplb_on_hand(conn, CFG)
    cb = _Callbacks(ledger)

    stock_net.net_and_finalize(
        conn, [ship], params, consolidate=True,
        take_from_stock=cb.take_from_stock, classify_built=cb.classify_built,
        add_buy=cb.add_buy, note_no_price=cb.note_no_price,
        build_kwargs={},
    )
    comp_a = next(ln.child for ln in ship.lines if ln.type_id == 2500)
    comp_b = next(ln.child for ln in ship.lines if ln.type_id == 2501)
    assert comp_a is None and comp_b is None  # оба компонента целиком покрыты складом
    assert 3000 not in cb.take_calls  # SharedMat вообще не запрашивался у склада
    assert 3000 not in cb.built
    assert cb.bought.get(34, 0) == 0
    assert cb.ledger[3000] == 50  # несвязанный остаток SharedMat не тронут
