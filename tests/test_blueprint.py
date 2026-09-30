"""core.blueprint: владение (BPO/BPC), стоимость инвенты T2, ручной оверрайд, флаг missing."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core
from forge.core import blueprint as bp

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
region_id = 10000006
"""
)

MANUF_BP = 2186     # T2 BP продукта
PRODUCT = 2185      # T2 продукт
INV_BP = 2184       # T1 BP для инвенты


def _seed_invention(conn):
    conn.executescript(
        """
        -- датакоры с объёмом 0 → landed = цена (без фрахта)
        INSERT INTO sde_types(type_id,name,volume) VALUES (1001,'DC A',0.0),(1002,'DC B',0.0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (1001,10000009,100000.0,1000),(1002,10000009,50000.0,1000);
        -- инвента: T1 BP -> T2 BP, 10 ранов/копия, вероятность 0.3
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,8,2186,10,0.3);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,8,1001,2),(2184,8,1002,2);
        """
    )
    conn.commit()


def _params():
    return core.build_params_from_config(CFG)


def test_invention_per_run(conn):
    _seed_invention(conn)
    # attempt = 2*100000 + 2*50000 = 300000 ; /(0.3*10)=100000
    assert bp.invention_per_run(conn, MANUF_BP, _params()) == pytest.approx(100000.0)


def test_invention_none_without_path(conn):
    assert bp.invention_per_run(conn, 999999, _params()) is None


def test_blueprint_cost_invention_when_not_owned(conn):
    _seed_invention(conn)
    bc = bp.blueprint_cost(conn, PRODUCT, MANUF_BP, 1, 5, _params())
    assert bc.source == "invention"
    assert bc.per_run == pytest.approx(100000.0) and bc.total == pytest.approx(500000.0)


def test_blueprint_cost_owned_bpo_is_zero(conn):
    _seed_invention(conn)
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,2186,0,0,-1,-1,0)")
    conn.commit()
    bc = bp.blueprint_cost(conn, PRODUCT, MANUF_BP, 1, 5, _params())
    assert bc.source == "owned_bpo" and bc.total == 0.0


def test_blueprint_cost_owned_bpc_is_zero(conn):
    _seed_invention(conn)
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,2186,0,0,1,5,1)")
    conn.commit()
    bc = bp.blueprint_cost(conn, PRODUCT, MANUF_BP, 1, 5, _params())
    assert bc.source == "owned_bpc" and bc.total == 0.0


def test_blueprint_cost_owned_bpc_insufficient_runs(conn):
    """Своя копия есть, но ранов на ней МЕНЬШЕ, чем нужно этому узлу — флагаем недостачу
    (реальный кейс на живой стройке: Core Temperature Regulator — копия только на
    5 прогонов, а нужно 10). Стоимость всё равно 0 (копия — sunk cost), это только
    предупреждение, как и missing_reaction для реакций."""
    _seed_invention(conn)
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,2186,0,0,1,5,1)")
    conn.commit()
    bc = bp.blueprint_cost(conn, PRODUCT, MANUF_BP, 1, 10, _params())
    assert bc.source == "owned_bpc_insufficient" and bc.total == 0.0
    assert bc.bpc_runs_owned == 5 and bc.bpc_runs_needed == 10


def test_owned_bpc_insufficient_computes_shortfall_invention_attempts(conn):
    """Недостача ранов попадает в список инвенты: раз чертёж
    РЕАЛЬНО инвентится (T1-источник есть в SDE), узел обязан посчитать, сколько попыток инвенты
    нужно ЗАПУСТИТЬ, чтобы закрыть именно НЕДОСТАЧУ (5 прогонов = 10 нужно − 5 на копии), а не
    всю постройку заново — то же число, что дало бы bcs=ceil(5/10)=1, ceil(1/0.3)=4 попытки."""
    from forge.core import sourcing
    _seed_invention(conn)  # T1-источник (2184) инвентит T2 BP (2186), 10 ранов/копия, prob 0.3
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        INSERT INTO characters(character_id,name) VALUES (7,'I');
        -- копия только на 5 прогонов, а нужно 10.
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,2186,0,0,1,5,1);
        """
    )
    conn.commit()
    n = sourcing.build_node_cost(conn, 2185, 10, 1, _params(), allow_build=False)
    assert n.blueprint_source == "owned_bpc_insufficient"
    assert n.bpc_runs_owned == 5 and n.bpc_runs_needed == 10
    assert n.bpc_shortfall_invention_attempts == 4
    # T1-источник не во владении → та же ownership-проверка, что и у обычной инвенты.
    assert n.invention_source_id == 2184 and n.invention_source_owned is False


def test_owned_bpc_insufficient_folds_shortfall_invention_cost_into_total(conn):
    """Недостачу ранов НЕ считаем «бесплатной» (sunk cost) — раз можем посчитать
    реальную стоимость закрытия недостачи инвентой (T1-копия + джоб-взнос + датакоры/декриптор,
    та же механика, что у обычной инвенты), она обязана попасть в total_cost/blueprint_cost,
    а не оставлять их искусственно заниженными."""
    from forge.core import sourcing
    _seed_invention(conn)  # датакоры: 2×DC A(100000) + 2×DC B(50000) = 300000/попытку
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        INSERT INTO characters(character_id,name) VALUES (7,'I');
        -- копия только на 5 прогонов, а нужно 10.
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,2186,0,0,1,5,1);
        """
    )
    conn.commit()
    params = _params()
    params.blueprint_overrides[2184] = 500000.0  # ручная цена T1-копии (напр. купленная на рынке)
    n = sourcing.build_node_cost(conn, 2185, 10, 1, params, allow_build=False)
    assert n.bpc_shortfall_invention_attempts == 4
    assert n.bpc_shortfall_invention_fee_per_attempt == pytest.approx(500000.0)  # T1-копия + 0 job_fee (нет EIV-данных)
    # blueprint_cost = ТОЛЬКО «право» на чертёж (T1-копия × попытки), без датакоров/декриптора.
    assert n.blueprint_cost == pytest.approx(4 * 500000.0)
    # датакоры на 4 попытки — НАСТОЯЩИЕ покупные материалы, уже в material_cost (не в blueprint_cost).
    dc_lines = [l for l in n.lines if l.type_id in (1001, 1002)]
    assert len(dc_lines) == 2
    assert sum(l.quantity for l in dc_lines if l.type_id == 1001) == 8   # 2/попытку × 4 попытки
    assert sum(l.quantity for l in dc_lines if l.type_id == 1002) == 8
    assert n.total_cost == pytest.approx(n.material_cost + n.job_cost + n.blueprint_cost)
    assert n.total_cost > n.material_cost + n.job_cost  # недостача не "бесплатна"


def test_owned_bpc_insufficient_leaves_shortfall_none_when_not_inventable(conn):
    """Чертёж НЕ инвентится вообще (нет T1-источника в SDE) — не выдумываем число попыток,
    остаётся None (фронтенд в этом случае показывает общую фразу «докупи копию на рынке»)."""
    from forge.core import sourcing
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        INSERT INTO characters(character_id,name) VALUES (7,'I');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) VALUES
            (1,7,2186,0,0,1,5,1);
        """
    )
    conn.commit()
    n = sourcing.build_node_cost(conn, 2185, 10, 1, _params(), allow_build=False)
    assert n.blueprint_source == "owned_bpc_insufficient"
    assert n.bpc_shortfall_invention_attempts is None


def test_owned_bpc_total_runs_sums_stack(conn):
    """Стек одинаковых копий, слитых ESI в одну позицию (quantity>0, у каждой один и тот же
    ``runs``) — суммарные раны = quantity × runs, не просто ``runs`` одной копии."""
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,2186,0,0,3,4,1)")
    conn.commit()
    assert bp.owned_bpc_total_runs(conn, 2186) == 12  # 3 копии × 4 рана


def test_owned_bpc_total_runs_ignores_bpo(conn):
    """BPO (is_copy=0) не считается этой функцией — у оригинала раны не ограничены вообще,
    вызывающий код обязан сначала проверить owned_kind()=='bpo' и не звать её для BPO."""
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,2186,0,0,-1,-1,0)")
    conn.commit()
    assert bp.owned_bpc_total_runs(conn, 2186) == 0


def test_blueprint_cost_manual_override_wins(conn):
    _seed_invention(conn)
    params = _params()
    params.blueprint_overrides[PRODUCT] = 777.0
    bc = bp.blueprint_cost(conn, PRODUCT, MANUF_BP, 1, 3, params)
    assert bc.source == "manual" and bc.per_run == 777.0 and bc.total == pytest.approx(2331.0)


def test_blueprint_cost_missing_when_no_path(conn):
    # нет инвенты, не владеем, нет оверрайда → missing, 0
    bc = bp.blueprint_cost(conn, 5000, 5001, 1, 4, _params())
    assert bc.source == "missing" and bc.total == 0.0


def test_blueprint_location_filter(conn):
    """Фильтр по location_id: свой BPO учитывается только если лежит в выбранной локации."""
    _seed_invention(conn)
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,2186,5000,0,0,-1,-1,0)")
    conn.commit()
    p = _params()
    # без фильтра — BPO найден (0)
    assert bp.blueprint_cost(conn, PRODUCT, MANUF_BP, 1, 1, p).source == "owned_bpo"
    # фильтр на нужную локацию — BPO найден
    p.blueprint_location_ids = frozenset({5000})
    assert bp.blueprint_cost(conn, PRODUCT, MANUF_BP, 1, 1, p).source == "owned_bpo"
    # фильтр на другую локацию — BPO «не виден» → путь инвенты
    p.blueprint_location_ids = frozenset({9999})
    assert bp.blueprint_cost(conn, PRODUCT, MANUF_BP, 1, 1, p).source == "invention"


def test_reaction_has_no_blueprint_cost(conn):
    from forge.core.bom import REACTION
    bc = bp.blueprint_cost(conn, 100, 101, REACTION, 9, _params())
    assert bc.total == 0.0


def test_invention_charges_whole_bpc_and_whole_attempts(conn):
    """Инвента оплачивается ЦЕЛЫМИ BPC (ceil(runs/прогонов на копию)) И целым числом попыток
    (ceil(bpcs/вероятность)) — нельзя провести дробную попытку, T1-копия/декриптор/датакоры
    расходуются на КАЖДУЮ попытку заново. Игрок реально готовит и запускает целое число
    попыток, поэтому округление ВСЕГДА вверх, даже 3.1 → 4; "амортизированного" режима
    (дробное число попыток по статистическому среднему) для инвенты нет —
    whole_blueprint НЕ влияет на инвенту вообще, оба режима дают одинаковый результат."""
    from forge.core import sourcing
    _seed_invention(conn)  # 2184 -8-> 2186, 10 прогонов/копия, prob 0.3, датакоры=300000
    # Производственная сторона T2-продукта 2185 из чертежа 2186 (декрипторы без цен → не в опциях).
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        """
    )
    conn.commit()
    p = _params()
    # цена 1 попытки = 300000 (датакоры); ceil(1/0.3)=4 попытки на 1 BPC -> 4×300000=1,200,000.
    # Датакоры — настоящие MaterialLine (список закупок + проверка склада) — считаются в
    # material_cost, НЕ в blueprint_cost (тот — только T1-копия+джоб-взнос инвенты, здесь оба 0:
    # нет своего T1 BPO, нет ставки индекса под инвенту в этой фикстуре). Полная стоимость
    # попытки (для информационной разбивки в UI) — в invention_breakdown["total"].
    n1 = sourcing.build_node_cost(conn, 2185, 1, 1, p, allow_build=False, whole_blueprint=True)
    assert n1.blueprint_source == "invention" and n1.decryptor is None
    assert n1.invention_breakdown["bpcs"] == 1
    assert n1.invention_breakdown["attempts"] == 4          # ceil(1/0.3), НЕ 3.33
    assert n1.invention_breakdown["total"] == pytest.approx(1_200_000.0)
    assert n1.blueprint_cost == pytest.approx(0.0)
    # сырьё: base ME инвенты (INVENT_BASE_ME=2%) даже без декриптора — ceil(100×0.98)=98×10=980.
    assert n1.material_cost == pytest.approx(980.0 + 1_200_000.0)  # сырьё + 4×300000 датакоры
    dc_lines = [ln for ln in n1.lines if ln.type_id in (1001, 1002)]
    assert {ln.type_id: ln.quantity for ln in dc_lines} == {1001: 8, 1002: 8}  # 4 попытки × 2 шт.
    assert all(ln.source == "buy" for ln in dc_lines)
    n10 = sourcing.build_node_cost(conn, 2185, 10, 1, p, allow_build=False, whole_blueprint=True)
    assert n10.invention_breakdown["attempts"] == 4  # runs=10 -> всё та же 1 BPC, 4 попытки
    n11 = sourcing.build_node_cost(conn, 2185, 11, 1, p, allow_build=False, whole_blueprint=True)
    assert n11.invention_breakdown["bpcs"] == 2
    assert n11.invention_breakdown["attempts"] == 7          # ceil(2/0.3)=ceil(6.667)=7
    assert n11.invention_breakdown["total"] == pytest.approx(2_100_000.0)  # 7×300000
    # whole_blueprint=False (по умолчанию) даёт ТОТ ЖЕ результат — амортизации для инвенты
    # нет вообще, флаг ничего не меняет для этой активности.
    namo = sourcing.build_node_cost(conn, 2185, 1, 1, p, allow_build=False)
    assert namo.invention_breakdown["attempts"] == 4
    assert namo.material_cost == pytest.approx(980.0 + 1_200_000.0)


def test_overrides_ignored_for_invention(conn):
    """У инвентируемых чертежей ME/TE задаёт выбранный декриптор (автооптимум по стоимости,
    см. build_node_cost) — me_overrides/te_overrides там НЕ должны ничего менять (defense in
    depth; /api/compare-me и так не вызывает оверрайд для blueprint_source == "invention")."""
    from forge.core import sourcing
    _seed_invention(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        """
    )
    conn.commit()
    p = _params()
    without = sourcing.build_node_cost(conn, 2185, 1, 1, p, allow_build=False)
    with_override = sourcing.build_node_cost(
        conn, 2185, 1, 1, p, allow_build=False, me_overrides={2185: 10}, te_overrides={2185: 20}
    )
    assert without.blueprint_source == "invention"
    assert without.total_cost == with_override.total_cost
    assert without.material_cost == with_override.material_cost
    assert without.lines[0].quantity == with_override.lines[0].quantity
    assert without.resulting_te == with_override.resulting_te


def test_build_node_cost_resulting_te_matches_invention_decryptor(conn):
    """``resulting_te`` — TE итоговой BPC от инвенты — обязан отражать РЕАЛЬНО выбранный
    (оптимальный) декриптор, а не базовое значение чертежа. Без декриптора (ни у одного нет
    цены) — базовый TE инвенты (``INVENT_BASE_TE``); с приоритетным (Process, te_mod=+6) —
    база+модификатор. Материал взят большим количеством (100 000/прогон), чтобы экономия
    материала от ME+3 (Process) однозначно перевешивала стоимость самого декриптора (иначе
    декриптор — при небольшом объёме материала — может проиграть чисто по деньгам, несмотря
    на лучшие ME/TE, см. реальный расчёт в разборе: 1000 ISK/попытку легко перекрывает
    30 ISK экономии материала на маленьком объёме)."""
    from forge.core import sourcing
    _seed_invention(conn)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100000);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        """
    )
    conn.commit()
    n_no_decryptor = sourcing.build_node_cost(conn, 2185, 1, 1, _params(), allow_build=False)
    assert n_no_decryptor.blueprint_source == "invention" and n_no_decryptor.decryptor is None
    assert n_no_decryptor.resulting_te == bp.INVENT_BASE_TE

    conn.execute("INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (34205,10000009,1000.0)")
    conn.commit()
    n_process = sourcing.build_node_cost(conn, 2185, 1, 1, _params(), allow_build=False)
    assert n_process.blueprint_source == "invention" and n_process.decryptor == "Process"
    assert n_process.resulting_te == bp.INVENT_BASE_TE + 6  # Process: te_mod=+6


def test_build_node_cost_resulting_te_default_when_blueprint_missing(conn):
    """Без владения и без пути инвенты (``blueprint_source == "missing"``) планировщик обязан
    получить ``default_te`` (аналог ``default_me`` для материалов) — не 0 жёстко."""
    from forge.core import sourcing
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'Widget',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        """
    )
    conn.commit()
    n = sourcing.build_node_cost(conn, 2185, 1, 1, _params(), allow_build=False, default_te=37)
    assert n.blueprint_source == "missing"
    assert n.resulting_te == 37


def test_build_node_cost_resulting_te_none_when_owned(conn):
    """Чертёж во владении — TE решает планировщик по конкретной физической копии/чару
    (``resulting_te`` не фиксируется здесь, остаётся ``None``)."""
    from forge.core import sourcing
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'Widget',1.0),(40,'Mineral',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,2186,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        """
    )
    conn.commit()
    n = sourcing.build_node_cost(conn, 2185, 1, 1, _params(), allow_build=False, default_te=99)
    assert n.blueprint_source == "owned_bpo"
    assert n.resulting_te is None


def test_invention_source_ownership_flag(conn):
    """Узел инвенты помечает T1-чертёж и владение им (для предупреждения о нехватке)."""
    from forge.core import sourcing
    _seed_invention(conn)  # T1-источник = 2184 (инвентит 2186)
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01),(2184,'T1 BP',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        """
    )
    conn.commit()
    assert bp.invention_source(conn, 2186) == 2184
    # T1-чертёж не во владении → owned=False
    n = sourcing.build_node_cost(conn, 2185, 1, 1, _params(), allow_build=False)
    assert n.blueprint_source == "invention"
    assert n.invention_source_id == 2184 and n.invention_source_owned is False
    # появился свой T1 BPO → owned=True
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (10,7,2184,0,0,-1,-1,0)")
    conn.commit()
    n2 = sourcing.build_node_cost(conn, 2185, 1, 1, _params(), allow_build=False)
    assert n2.invention_source_owned is True


def test_t1_copy_cost_added_when_bpo_owned(conn):
    """Со своим T1 BPO стоимость инвенты включает копи-джоб T1-копии (0.02 × EIV × индекс)."""
    from forge.core import sourcing
    _seed_invention(conn)  # T1-источник 2184, инвентит 2186 -> продукт 2185
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2185,'T2 Item',1.0),(40,'Mineral',0.01),
            (41,'T1 Mineral',0.01),(2184,'T1 BP',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2186,1,40,100),(2184,1,41,1000);   -- 2184 (T1 BP) производит из 41
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (40,10000009,10.0);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (41,500.0);  -- EIV T1
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (0,5,0.05);
        -- свой T1 BPO
        INSERT INTO characters(character_id,name) VALUES (7,'I');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (10,7,2184,0,0,-1,-1,0);
        """
    )
    conn.commit()
    p = _params()
    # EIV T1 (1 ран) = 1000 × 500 = 500000; копи-база = 0.02 × 500000 = 10000; × индекс 0.05 = 500.
    assert sourcing._t1_copy_cost(conn, 2184, p) == pytest.approx(500.0)
    # инвента-узел: T1 во владении → копия добавлена в стоимость инвенты (>0 на прогон).
    n = sourcing.build_node_cost(conn, 2185, 1, 1, p, allow_build=False)
    assert n.invention_source_owned is True
    # blueprint_cost — ТОЛЬКО T1-копия + джоб-взнос инвенты (датакоры уже в material_cost
    # как настоящие MaterialLine, см. test_invention_charges_whole_bpc_and_whole_attempts):
    # 500 (T1-копия) × 4 попытки = 2000, джоб-взнос здесь 0 (нет adjusted_price под материал 40).
    assert n.invention_breakdown["attempts"] == 4
    assert n.invention_breakdown["t1_copy"] == pytest.approx(500.0)
    assert n.blueprint_cost == pytest.approx(500.0 * 4)
    # без своего T1 BPO — t1_copy_cost=0 → blueprint_cost падает до 0 (датакоры остаются
    # материалом, но уже без добавленной стоимости T1-копии).
    conn.execute("DELETE FROM character_blueprints WHERE type_id = 2184")
    conn.commit()
    n_unowned = sourcing.build_node_cost(conn, 2185, 1, 1, p, allow_build=False)
    assert n_unowned.invention_source_owned is False
    assert n_unowned.blueprint_cost == pytest.approx(0.0)


def test_invention_job_fee(conn):
    """Взнос за джоб инвенты = 0.02 × EIV(T1) × индекс инвенты × множители станции."""
    from forge.core import cost as ccost
    from forge.core import sourcing
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2184,'T1 BP',1.0),(41,'Mat',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,1,500,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,1,41,1000);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (41,500.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (0,8,0.05);
        """
    )
    conn.commit()
    # EIV(T1,1 ран)=1000×500=500000; база=0.02×500000=10000; ×индекс 0.05=500 (станций нет→mult 1).
    assert sourcing._bp_job_fee(conn, 2184, _params(), ccost.INVENTION) == pytest.approx(500.0)


def test_invention_options_with_decryptor(conn):
    """invention_options: вариант без декриптора (ME2) + с декриптором (сдвиг ME/прогонов/цены)."""
    _seed_invention(conn)
    # цена Optimized Attainment Decryptor (34207): prob×1.9, ME+1, прогоны+2
    conn.execute("INSERT INTO sde_types(type_id,name,volume) VALUES (34207,'Optimized Attainment Decryptor',0.1)")
    conn.execute("INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (34207,10000009,100000.0,100)")
    conn.commit()
    opts = bp.invention_options(conn, MANUF_BP, _params())
    base = next(o for o in opts if o.decryptor is None)
    assert base.me == 2 and base.runs_per_copy == 10
    assert base.per_run == pytest.approx(100000.0)  # 300000/(0.3*10)
    oa = next(o for o in opts if o.decryptor == "Optimized Attainment")
    assert oa.me == 3 and oa.runs_per_copy == 12               # 2+1, 10+2
    assert oa.per_run == pytest.approx(400000.0 / (0.3 * 1.9 * 12))  # (датакоры+декр)/(prob×mult×runs)


# --- реакции: владение формулой + число копий под параллельные потоки -------------------

def test_reaction_formula_copies_bpo(conn):
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,900,0,0,-1,-1,0)")
    conn.commit()
    assert bp.reaction_formula_copies(conn, 900) == 1


def test_reaction_formula_copies_bpc_stack_and_exhausted(conn):
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    # одиночная BPC с прогонами
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,900,0,0,-2,50,1)")
    # стек из 3 одинаковых BPC
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (2,7,900,0,0,3,50,1)")
    # истощённая одиночная BPC (0 прогонов) — не считается
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (3,7,900,0,0,-2,0,1)")
    conn.commit()
    assert bp.reaction_formula_copies(conn, 900) == 4  # 1 (одиночная) + 3 (стек)


def test_reaction_formula_copies_bpo_stack(conn):
    """Живые данные ESI: стек BPO-подобных формул реакции — quantity>0 вместе с runs=-1
    (в отличие от обычных BPC-стеков, где runs — реальный положительный остаток)."""
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,900,0,0,6,-1,0)")
    conn.commit()
    assert bp.reaction_formula_copies(conn, 900) == 6


def test_reaction_formula_copies_location_filter(conn):
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,900,5000,0,0,-1,-1,0)")
    conn.commit()
    assert bp.reaction_formula_copies(conn, 900, location_ids=[5000]) == 1
    assert bp.reaction_formula_copies(conn, 900, location_ids=[9999]) == 0


def test_blueprint_cost_reaction_missing_when_not_owned(conn):
    from forge.core.bom import REACTION
    bc = bp.blueprint_cost(conn, 900, 901, REACTION, 9, _params(), streams_needed=3)
    assert bc.source == "missing_reaction" and bc.total == 0.0
    assert bc.reaction_owned == 0 and bc.reaction_needed == 3


def test_blueprint_cost_reaction_owned_enough(conn):
    from forge.core.bom import REACTION
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,901,0,0,3,50,1)")  # стек из 3 BPC
    conn.commit()
    bc = bp.blueprint_cost(conn, 900, 901, REACTION, 9, _params(), streams_needed=3)
    assert bc.source == "owned_bpo" and bc.total == 0.0
    assert bc.reaction_owned == 3 and bc.reaction_needed == 3


def test_blueprint_cost_reaction_owned_insufficient(conn):
    from forge.core.bom import REACTION
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,901,0,0,2,50,1)")  # только 2 копии
    conn.commit()
    bc = bp.blueprint_cost(conn, 900, 901, REACTION, 9, _params(), streams_needed=5)
    assert bc.source == "missing_reaction"
    assert bc.reaction_owned == 2 and bc.reaction_needed == 5


def test_blueprint_cost_reaction_manual_override_wins(conn):
    from forge.core.bom import REACTION
    params = _params()
    params.blueprint_overrides[900] = 123.0
    bc = bp.blueprint_cost(conn, 900, 901, REACTION, 2, params, streams_needed=5)
    assert bc.source == "manual" and bc.per_run == 123.0 and bc.total == pytest.approx(246.0)


def _seed_reaction(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (900,'Reaction Product',1.0),(950,'Input',0.01);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity)
            VALUES (901,11,900,100);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (901,11,950,10);
        INSERT INTO market_snapshot(type_id,region_id,sell_min) VALUES (950,10000009,5.0);
        """
    )
    conn.commit()


def test_build_node_cost_reaction_streams_needed(conn):
    """Число нужных копий формулы = число ФАКТИЧЕСКИ запускаемых параллельных джобов."""
    from forge.core import sourcing
    _seed_reaction(conn)
    p = _params()
    # runs=1000, streams=5 -> 5 параллельных джобов -> нужно 5 копий формулы; нет ни одной.
    node = sourcing.build_node_cost(conn, 900, 1000, 5, p)
    assert node.blueprint_source == "missing_reaction"
    assert node.reaction_bp_needed == 5 and node.reaction_bp_owned == 0

    # владеем 5 копиями -> удовлетворено.
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,901,0,0,5,999,1)")
    conn.commit()
    node2 = sourcing.build_node_cost(conn, 900, 1000, 5, p)
    assert node2.blueprint_source == "owned_bpo"
    assert node2.reaction_bp_owned == 5 and node2.reaction_bp_needed == 5


def test_build_node_cost_reaction_streams_capped_by_runs(conn):
    """streams больше runs -> split_runs схлопывает пустые джобы -> нужно меньше копий."""
    from forge.core import sourcing
    _seed_reaction(conn)
    node = sourcing.build_node_cost(conn, 900, 3, 5, _params())
    assert node.reaction_bp_needed == 3