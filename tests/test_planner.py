"""planner: слоты из скиллов, длительность джоба, извлечение джобов и расписание."""

from __future__ import annotations

import itertools

import pytest

from forge import config as config_mod
from forge.core.sourcing import MaterialLine, NodeResult
from forge.planner import plan_basket, schedule, slots, streams_for_deadline, timing

CFG = config_mod.loads(
    """
db_path = "x.db"
[industry]
time_mult = 1.0
"""
)


def _seed_skills_names(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES
            (3387,'Mass Production'),(24625,'Advanced Mass Production'),
            (45748,'Mass Reactions'),(45749,'Advanced Mass Reactions'),
            (3406,'Laboratory Operation'),(24624,'Advanced Laboratory Operation'),
            (3380,'Industry'),(3388,'Advanced Industry');
        """
    )
    conn.commit()


def test_slots_from_skills(conn):
    _seed_skills_names(conn)
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_skills(character_id,skill_type_id,active_level) VALUES
            (7,3387,5),(7,24625,3),(7,45748,4);
        """
    )
    conn.commit()
    sp = slots.slots_for(conn, 7, "Igor")
    assert sp.manufacturing == 1 + 5 + 3   # base + Mass Production + Advanced
    assert sp.reaction == 1 + 4 + 0
    assert sp.science == 1
    assert sp.slots_for_activity(11) == 5   # reaction pool


def test_job_seconds_formula():
    # производство: 6000 c/run × 2 × (1-0.1 TE) × (1-0.04*5 Ind) × (1-0.03*5 AdvInd)
    s = timing.job_seconds(6000, 2, 1, te=10, industry_level=5, adv_industry_level=5, time_mult=1.0)
    assert round(s) == round(6000 * 2 * 0.9 * 0.8 * 0.85)
    # реакция: без TE и без Industry/Advanced Industry (описание скилла в SDE: "all
    # manufacturing and research times" — реакции не входят); свой скилл Reactions (4%/ур.).
    r = timing.job_seconds(3600, 1, 11, te=20, industry_level=5, adv_industry_level=5, reactions_level=5)
    assert round(r) == round(3600 * 1 * 0.8)  # только Reactions: 1 - 0.04*5 = 0.8
    # без Reactions (level=0) — Advanced Industry всё равно не должен просачиваться в реакции.
    r0 = timing.job_seconds(3600, 1, 11, te=20, industry_level=5, adv_industry_level=5)
    assert r0 == 3600.0
    # инвента (8): без TE (нет своей копии) и без Industry (описание скилла — "reduction in
    # manufacturing time", инвента не входит), но Advanced Industry ПРИМЕНЯЕТСЯ ("all
    # manufacturing and research times" — инвента это research). Industry к инвенте НЕ
    # применяется (важно, раз инвента планируется отдельным джобом).
    i = timing.job_seconds(63900, 3, 8, te=20, industry_level=5, adv_industry_level=5)
    assert round(i) == round(63900 * 3 * 0.85)  # только AdvInd: 1 - 0.03*5 = 0.85


def test_specialization_mult_only_for_required_skills_and_manufacturing(conn):
    """Реальный кейс юзера: Capital Gravimetric Sensor Cluster требует Electronic
    Engineering (manufactureTimePerLevel=-1%/ур.) — у персонажа уровень 5 → -5%. Реакции такой
    механики не имеют вообще (только производство)."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (11453,'Electronic Engineering');
        INSERT INTO sde_dogma_attribute_types(attribute_id,name,display_name,stackable,high_is_good)
            VALUES (1982,'manufactureTimePerLevel',NULL,0,1);
        INSERT INTO sde_dogma_type_attributes(type_id,attribute_id,value_float) VALUES (11453,1982,-1.0);
        INSERT INTO sde_blueprint_skills(blueprint_type_id,activity_id,skill_type_id,level)
            VALUES (29056,1,11453,4);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_skills(character_id,skill_type_id,active_level) VALUES (7,11453,5);
        """
    )
    conn.commit()
    MANUFACTURING = 1
    mult = timing.specialization_mult(conn, 7, 29056, MANUFACTURING)
    assert mult == pytest.approx(0.95)  # 1 - 0.01*5
    # Реакции — специализированные скиллы производства не применяются вообще (другая активность).
    assert timing.specialization_mult(conn, 7, 29056, 11) == 1.0
    # Персонаж без скилла (уровень 0) — нет эффекта, но не падает.
    assert timing.specialization_mult(conn, 999, 29056, MANUFACTURING) == 1.0
    # Чертёж без специализированных требований — нет эффекта.
    assert timing.specialization_mult(conn, 7, 99999, MANUFACTURING) == 1.0


def _tree() -> NodeResult:
    """Widget(2000) <- build Pyerite(35); Pyerite <- buy Trit(34)."""
    child = NodeResult(35, "Pyerite", 1, 1035, 1, 1, 100, 0.0, 0.0, 0.0, 0.0,
                       lines=[MaterialLine(34, "Tritanium", 1, "buy", 5.0, 5.0)])
    parent = NodeResult(2000, "Widget", 1, 1000, 1, 1, 1, 0.0, 0.0, 0.0, 0.0,
                        lines=[MaterialLine(34, "Tritanium", 100, "buy", 5.0, 500.0),
                               MaterialLine(35, "Pyerite", 50, "build", 1.0, 50.0, child=child)])
    return parent


def test_extract_jobs_with_deps():
    jobs = schedule.extract_jobs(_tree())
    assert len(jobs) == 2
    child = next(j for j in jobs if j.product_type_id == 35)
    parent = next(j for j in jobs if j.product_type_id == 2000)
    assert child.deps == frozenset()
    assert parent.deps == frozenset({child.id})  # родитель ждёт компонент


def test_extract_jobs_shares_job_for_consolidated_dag_node():
    """После sourcing.consolidate_shared_components дерево — DAG: ДВЕ разные родительские
    строки (Alpha и Beta) могут указывать на ОДИН и тот же общий NodeResult (общий
    под-компонент). extract_jobs должен создать джоб общего компонента РОВНО ОДИН раз — оба
    родителя зависят от ТОГО ЖЕ id, а не плодят дублирующие джобы на одну физическую постройку."""
    shared = NodeResult(35, "Pyerite", 1, 1035, 5, 1, 500, 0.0, 0.0, 0.0, 0.0, lines=[])
    alpha = NodeResult(2000, "Alpha", 1, 1000, 1, 1, 1, 0.0, 0.0, 0.0, 0.0,
                       lines=[MaterialLine(35, "Pyerite", 350, "build", 1.0, 350.0, child=shared)])
    beta = NodeResult(2001, "Beta", 1, 1001, 1, 1, 1, 0.0, 0.0, 0.0, 0.0,
                      lines=[MaterialLine(35, "Pyerite", 150, "build", 1.0, 150.0, child=shared)])
    ship = NodeResult(3000, "Ship", 1, 1002, 1, 1, 1, 0.0, 0.0, 0.0, 0.0,
                      lines=[MaterialLine(2000, "Alpha", 1, "build", 0.0, 0.0, child=alpha),
                             MaterialLine(2001, "Beta", 1, "build", 0.0, 0.0, child=beta)])
    jobs = schedule.extract_jobs(ship)
    shared_jobs = [j for j in jobs if j.product_type_id == 35]
    assert len(shared_jobs) == 1  # НЕ два — общий компонент строится один раз
    assert len(jobs) == 4  # Ship + Alpha + Beta + один общий Pyerite (не 5)
    alpha_job = next(j for j in jobs if j.product_type_id == 2000)
    beta_job = next(j for j in jobs if j.product_type_id == 2001)
    assert alpha_job.deps == beta_job.deps == frozenset({shared_jobs[0].id})


def test_extract_jobs_many_shares_job_across_different_basket_roots():
    """Реальный кейс юзера (Sylramic Fibers ×50 + Ferrogel ×50 в одной корзине,
    оба тянут общий Hexite, объединённый sourcing.consolidate_shared_components): если звать
    extract_jobs ОТДЕЛЬНО на КАЖДЫЙ корень корзины (своя мемоизация у каждого вызова), общий
    узел получит ПОЛНЫЙ (не долевой) джоб от КАЖДОГО корня (49+49), и consolidate_jobs
    сложит эти дубли (98 вместо верных 49). extract_jobs_many
    обходит ВСЕ корни ОДНОЙ мемоизацией — общий узел получает джоб РОВНО один раз."""
    shared = NodeResult(35, "Hexite", 11, 1035, 49, 1, 9800, 0.0, 0.0, 0.0, 0.0, lines=[])
    sylramic = NodeResult(2000, "Sylramic Fibers", 11, 1000, 50, 1, 300000, 0.0, 0.0, 0.0, 0.0,
                          lines=[MaterialLine(35, "Hexite", 4868, "build", 1.0, 4868.0, child=shared)])
    ferrogel = NodeResult(2001, "Ferrogel", 11, 1001, 50, 1, 20000, 0.0, 0.0, 0.0, 0.0,
                          lines=[MaterialLine(35, "Hexite", 4868, "build", 1.0, 4868.0, child=shared)])
    jobs = schedule.extract_jobs_many([sylramic, ferrogel])
    hexite_jobs = [j for j in jobs if j.product_type_id == 35]
    assert len(hexite_jobs) == 1               # НЕ два — общий компонент строится один раз
    assert hexite_jobs[0].runs == 49            # уже объединённый спрос, не 49+49=98
    assert len(jobs) == 3                       # Sylramic + Ferrogel + один общий Hexite


def test_consolidate_jobs_is_no_op_on_already_merged_shared_component():
    """Кейс web/stock_net.py (Vanadium Hafnite, 5× Ishtar):
    на дереве, где общий под-компонент УЖЕ представлен ОДНИМ объектом (как гарантирует
    stock_net.net_and_finalize — см. его докстринг), schedule.consolidate_jobs ничего не должен
    менять — extract_jobs_many сам по себе уже даёт правильный (не задвоенный, не переразбитый
    на фиктивные потоки) список джобов. Останься узел НЕ слитым, у
    consolidate_jobs'а был бы побочный эффект: он свёл бы задвоение по КЛЮЧУ, но заодно
    подменил бы число потоков на len(объектов) — искусственный параллелизм без всякого основания
    в auto_streams/max_stream_days. Здесь подтверждаем: на правильно слитом дереве
    consolidate_jobs — доказуемый no-op."""
    shared = NodeResult(3000, "SharedMat", 1, 4004, 3, 1, 300, 0.0, 0.0, 0.0, 0.0, lines=[])
    comp_a = NodeResult(2500, "ComponentA", 1, 4002, 6, 1, 6, 0.0, 0.0, 0.0, 0.0,
                         lines=[MaterialLine(3000, "SharedMat", 210, "build", 1.0, 210.0, child=shared)])
    comp_b = NodeResult(2501, "ComponentB", 1, 4003, 7, 1, 7, 0.0, 0.0, 0.0, 0.0,
                         lines=[MaterialLine(3000, "SharedMat", 84, "build", 1.0, 84.0, child=shared)])
    ship = NodeResult(4000, "Ship", 1, 4001, 1, 1, 1, 0.0, 0.0, 0.0, 0.0,
                       lines=[MaterialLine(2500, "ComponentA", 6, "build", 0.0, 0.0, child=comp_a),
                              MaterialLine(2501, "ComponentB", 7, "build", 0.0, 0.0, child=comp_b)])

    direct = schedule.extract_jobs_many([ship])
    via_consolidate = schedule.consolidate_jobs([schedule.extract_jobs_many([ship])])

    def _fingerprint(jobs):
        return sorted((j.product_type_id, j.runs, j.blueprint_type_id, j.activity_id) for j in jobs)

    assert _fingerprint(direct) == _fingerprint(via_consolidate)
    shared_jobs = [j for j in direct if j.product_type_id == 3000]
    assert len(shared_jobs) == 1 and shared_jobs[0].runs == 3  # не переразбито на потоки


def _invention_node(attempts: float = 2.5) -> NodeResult:
    """T2 Item(2185) из инвенты Т1-чертежа(2184) — без conn инвент-джоб не создаётся."""
    return NodeResult(
        2185, "T2 Item", 1, 2186, 1, 1, 1, 0.0, 0.0, 0.0, 0.0, lines=[],
        blueprint_source="invention", invention_source_id=2184,
        invention_breakdown={"attempts": attempts},
    )


def test_extract_jobs_without_conn_skips_invention_job():
    """Без conn инвента не планируется как джоб —
    только как строка стоимости."""
    jobs = schedule.extract_jobs(_invention_node())
    assert len(jobs) == 1
    assert jobs[0].activity_id == 1


def test_extract_jobs_creates_invention_job_dependency(conn):
    """С conn — инвента (activity_id=8) добавляется отдельным джобом (runs=ceil(попыток),
    blueprint_type_id=Т1-источник), от которого зависит джоб постройки продукта. Попытки
    независимы (своя T1-копия на каждую) — дробятся на параллельные потоки, как и обычные
    джобы (``split_runs``), чтобы инвента не висела на критическом пути одним куском."""
    conn.execute(
        "INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (2184,8,6000)"
    )
    conn.commit()
    jobs = schedule.extract_jobs(_invention_node(attempts=2.5), conn)
    inv_jobs = [j for j in jobs if j.activity_id == 8]
    own_job = next(j for j in jobs if j.activity_id == 1)
    assert len(jobs) == 4  # 3 попытки (ceil(2.5)) параллельными джобами + 1 постройка
    assert len(inv_jobs) == 3
    assert sum(j.runs for j in inv_jobs) == 3  # ceil(2.5), поделено на 3 потока по 1
    assert all(j.blueprint_type_id == 2184 and j.deps == frozenset() for j in inv_jobs)
    # постройка ждёт ВСЕ потоки инвенты разом, не только первый/последний
    assert own_job.deps == frozenset(j.id for j in inv_jobs)


def _bpc_shortfall_node(attempts: int = 3) -> NodeResult:
    """T2 Item(2185), своя копия чертежа есть, но ранов не хватает — недостачу можно закрыть
    инвентой T1-чертежа(2184)."""
    return NodeResult(
        2185, "T2 Item", 1, 2186, 1, 1, 1, 0.0, 0.0, 0.0, 0.0, lines=[],
        blueprint_source="owned_bpc_insufficient",
        invention_source_id=2184,
        bpc_shortfall_invention_attempts=attempts,
    )


def test_extract_jobs_creates_non_blocking_invention_job_for_bpc_shortfall(conn):
    """Кейс Multispectrum Shield Hardener II (недостача ранов своей копии): джоб инвенты на
    недостачу ОБЯЗАН попасть в расписание (и в контроль сроков и стоимости) — но, в отличие
    от обычной инвенты, НЕ блокирует постройку продукта: часть прогонов уже можно строить с существующей (пусть и неполной) копией прямо сейчас."""
    conn.execute(
        "INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (2184,8,6000)"
    )
    conn.commit()
    jobs = schedule.extract_jobs(_bpc_shortfall_node(attempts=3), conn)
    inv_jobs = [j for j in jobs if j.activity_id == 8]
    own_job = next(j for j in jobs if j.activity_id == 1)
    assert sum(j.runs for j in inv_jobs) == 3
    assert all(j.blueprint_type_id == 2184 and j.deps == frozenset() for j in inv_jobs)
    assert own_job.deps == frozenset()  # НЕ ждёт инвенту недостачи — не блокирующая


def test_extract_jobs_without_conn_skips_bpc_shortfall_invention_job():
    """Без conn — как и у обычной инвенты, джоб недостачи не создаётся."""
    jobs = schedule.extract_jobs(_bpc_shortfall_node())
    assert len(jobs) == 1
    assert jobs[0].activity_id == 1


def test_extract_jobs_carries_resulting_te_onto_job(conn):
    """``extract_jobs_many`` обязан скопировать ``node.resulting_te`` на джоб (не только
    активность/раны) — иначе схема расписания не сможет использовать TE инвенты/default_te
    вообще, даже если core уже честно его посчитал."""
    node = NodeResult(2185, "T2 Item", 1, 2186, 1, 1, 1, 0.0, 0.0, 0.0, 0.0, lines=[], resulting_te=42)
    jobs = schedule.extract_jobs_many([node])
    assert jobs[0].resulting_te == 42


def test_schedule_uses_resulting_te_instead_of_owned_lookup(conn):
    """Узел с заданным ``resulting_te`` (инвента с декриптором / default_te для
    непринадлежащего чертежа) должен планироваться ИМЕННО с этим TE — не с 0, что дал бы
    обычный ``best_owned_te`` для чертежа, копии которого физически ни у кого ещё нет (реальный
    кейс: инвентируемый Т2 с декриптором TE+6 — core знает итоговый TE от выбранного
    декриптора, и расписание не должно считать его так, будто TE=0)."""
    _seed_skills_names(conn)
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (2186,1,10000);
        """
    )
    conn.commit()
    no_te = NodeResult(2185, "NoTE", 1, 2186, 1, 1, 1, 0.0, 0.0, 0.0, 0.0, lines=[])
    with_te = NodeResult(2185, "WithTE", 1, 2186, 1, 1, 1, 0.0, 0.0, 0.0, 0.0, lines=[], resulting_te=50)
    s_no = schedule.schedule_jobs(conn, CFG, schedule.extract_jobs(no_te))
    s_with = schedule.schedule_jobs(conn, CFG, schedule.extract_jobs(with_te))
    assert s_no.items[0].te == 0
    assert s_with.items[0].te == 50
    assert s_with.items[0].end < s_no.items[0].end   # TE=50 реально ускоряет джоб


def test_schedule_invention_job_uses_science_pool_and_respects_dependency(conn):
    """Джоб инвенты идёт в отдельный пул слотов "science" (Laboratory Operation), и джоб
    постройки продукта не может начаться раньше, чем закончится инвента."""
    _seed_skills_names(conn)
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_skills(character_id,skill_type_id,active_level) VALUES (7,3406,5);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (2184,8,6000),(2186,1,1200);
        """
    )
    conn.commit()
    jobs = schedule.extract_jobs(_invention_node(attempts=1.0), conn)
    s = schedule.schedule_jobs(conn, CFG, jobs)
    assert len(s.items) == 2
    inv_item = next(i for i in s.items if i.activity_id == 8)
    own_item = next(i for i in s.items if i.activity_id == 1)
    assert inv_item.pool == "science"
    assert own_item.start >= inv_item.end
    assert not s.warnings


def test_schedule_parallelizes_invention_attempts_across_science_slots(conn):
    """Реальный кейс юзера (инвента Nomad, 6 попыток): попытки независимы — своя T1-копия
    на каждую, ничто не мешает им идти параллельно, а не ОДНИМ последовательным джобом.
    С несколькими слотами "science" все попытки должны разложиться ПО РАЗНЫМ
    слотам одновременно (не 4×base последовательно), и постройка продукта — ждать САМУЮ
    ПОЗДНЮЮ из них (не только одну какую-то конкретную)."""
    _seed_skills_names(conn)
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_skills(character_id,skill_type_id,active_level) VALUES (7,24624,3);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (2184,8,6000),(2186,1,1200);
        """
    )
    conn.commit()
    jobs = schedule.extract_jobs(_invention_node(attempts=4.0), conn)
    s = schedule.schedule_jobs(conn, CFG, jobs)
    inv_items = [i for i in s.items if i.activity_id == 8]
    own_item = next(i for i in s.items if i.activity_id == 1)
    assert len(inv_items) == 4              # 4 независимые попытки — 4 отдельных джоба, не 1
    assert sum(i.runs for i in inv_items) == 4
    # 1 базовый + 3 от Advanced Laboratory Operation ур.3 = 4 слота science — все 4 попытки
    # идут ОДНОВРЕМЕННО (каждая — 1 прогон, 6000с), а НЕ 4×6000 последовательно в одном джобе.
    assert max(i.end for i in inv_items) == pytest.approx(6000.0)
    assert own_item.start == pytest.approx(max(i.end for i in inv_items))  # ждёт ВСЕ потоки


def test_schedule_respects_dependencies(conn):
    _seed_skills_names(conn)
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_skills(character_id,skill_type_id,active_level) VALUES (7,3387,5);
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,10,10,-1,-1,0),(2,7,1035,0,0,-1,-1,0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,6000),(1035,1,1200);
        """
    )
    conn.commit()
    s = schedule.schedule_jobs(conn, CFG, schedule.extract_jobs(_tree()))
    assert len(s.items) == 2
    child = next(i for i in s.items if i.product_type_id == 35)
    parent = next(i for i in s.items if i.product_type_id == 2000)
    # родитель не может начаться раньше, чем готов компонент
    assert parent.start >= child.end
    assert s.makespan == parent.end
    assert not s.warnings


def test_streams_for_deadline(conn):
    _seed_skills_names(conn)
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,86400);  -- 1 день на прогон
        """
    )
    conn.commit()
    assert streams_for_deadline(conn, CFG, 2000, 6, 3) == 2   # 3 прогона/поток → 2 потока
    assert streams_for_deadline(conn, CFG, 2000, 6, 1) == 6   # 1 прогон/поток → 6 потоков


def test_schedule_only_production_chars(conn):
    _seed_skills_names(conn)
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'A'),(8,'B');
        INSERT INTO character_skills(character_id,skill_type_id,active_level) VALUES (7,3387,5),(8,3387,5);
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0),(2,8,1000,0,0,-1,-1,0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600);
        """
    )
    conn.commit()
    node = NodeResult(2000, "W", 1, 1000, 4, 4, 4, 0.0, 0.0, 0.0, 0.0, lines=[])
    cfg2 = config_mod.loads('db_path="x"\nmanufacturing_character_ids=[7]')
    s = schedule.schedule_jobs(conn, cfg2, schedule.extract_jobs(node))
    assert len(s.items) == 4
    assert {i.character_name for i in s.items} == {"A"}  # только производственный чар


def _seed_basket(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES
            (3387,'Mass Production'),(2000,'Alpha'),(2001,'Beta');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1001,1,2001,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,600),(1001,1,600);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_skills(character_id,skill_type_id,active_level) VALUES (7,3387,2);
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0),(2,7,1001,0,0,-1,-1,0);
        """
    )
    conn.commit()


def test_plan_basket_merges_into_one_schedule(conn):
    _seed_basket(conn)
    bp = plan_basket(conn, CFG, [(2000, 4, 2), (2001, 4, 2)])
    items = bp.schedule.items
    # оба продукта в одном графике
    assert {i.product_type_id for i in items} == {2000, 2001}
    # id джобов уникальны между продуктами (после сдвига)
    ids = [i.job_id for i in items]
    assert len(ids) == len(set(ids))
    # ни один слот не забронирован дважды: интервалы по (чар,пул,слот) не пересекаются
    by_slot: dict[tuple, list] = {}
    for i in items:
        by_slot.setdefault((i.character_id, i.pool, i.slot), []).append((i.start, i.end))
    for intervals in by_slot.values():
        intervals.sort()
        for (_s1, e1), (s2, _e2) in itertools.pairwise(intervals):
            assert s2 >= e1  # следующий джоб начинается не раньше конца предыдущего


def test_consolidate_jobs_merges_shared_component():
    """Общий компонент двух продуктов сливается в один джоб; родители ждут общий пул."""
    Job = schedule.Job
    # Продукт A: компонент Pyerite(35)×2 → Alpha(2000). Продукт B: Pyerite(35)×2 → Beta(2001).
    a = [Job(0, 35, 1035, 1, "Pyerite", 2, frozenset()),
         Job(1, 2000, 1000, 1, "Alpha", 1, frozenset({0}))]
    b = [Job(2, 35, 1035, 1, "Pyerite", 2, frozenset()),
         Job(3, 2001, 1001, 1, "Beta", 1, frozenset({2}))]
    out = schedule.consolidate_jobs([a, b])
    comp = [j for j in out if j.product_type_id == 35]
    parents = [j for j in out if j.product_type_id in (2000, 2001)]
    assert len(comp) == 1                 # один общий джоб вместо двух
    assert comp[0].runs == 4              # runs суммируются (2+2)
    assert len(parents) == 2
    assert all(j.deps == frozenset({comp[0].id}) for j in parents)  # оба ждут общий джоб
    # пост-порядок: компонент раньше обоих родителей
    assert out.index(comp[0]) < min(out.index(p) for p in parents)


def test_reaction_role_excludes_manufacturing_only_char(conn):
    """Чар только в реакциях не берёт производственные джобы (и наоборот)."""
    _seed_skills_names(conn)
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'Maker'),(8,'Reactor');
        INSERT INTO character_skills(character_id,skill_type_id,active_level)
            VALUES (7,3387,5),(8,45748,5);
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0),(2,8,1000,0,0,-1,-1,0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600);
        """
    )
    conn.commit()
    node = NodeResult(2000, "W", 1, 1000, 2, 2, 2, 0.0, 0.0, 0.0, 0.0, lines=[])
    # Reactor назначен только на реакции → производственные джобы ему не идут.
    cfg2 = config_mod.loads('db_path="x"\nmanufacturing_character_ids=[7]\nreaction_character_ids=[8]')
    s = schedule.schedule_jobs(conn, cfg2, schedule.extract_jobs(node))
    assert {i.character_name for i in s.items} == {"Maker"}
