"""Рекурсивный make-or-buy и сборка стоимости узла постройки (чистые функции, без сети).

Для каждого материала сравнивается landed-цена покупки и себестоимость постройки
(если есть чертёж и не превышена глубина) — берётся дешёвый вариант. Стоимость узла =
материалы (по выбранным источникам) + стоимость установки джоба (EIV).
"""

from __future__ import annotations

import functools
import math
import sqlite3
from dataclasses import dataclass, field

from . import blueprint as bp_cost
from . import bom, cost, prices, reprocess

BP_JOB_BASE_FACTOR = 0.02  # база копи/инвент-джоба в EVE ≈ 0.02 × EIV

# Сентинел в ``_chain`` — «сейчас уже внутри сорсинга ПРЕКУРСОРА для переработки» (не настоящий
# type_id, EVE type_id всегда > 0). Не даёт переработке рекурсивно рассматривать переработку ЕЩЁ
# И для материалов прекурсора — иначе много́ярусные реакции (простая → сложная → …, у EVE есть и
# такие: часть «сложных» реакционных материалов САМИ имеют свой Unrefined-путь) каскадируют
# комбинаторно на большом дереве (капитальные T2-корабли вроде Nomad — 60+с даже с кэшами).
# Ограничение ОДНИМ уровнем переработки удерживает расчёт в разумном времени, а практически
# полезные случаи укладываются в один уровень.
_REPROCESS_MARKER = -1

# Потолок автовыставленных потоков при слиянии общего компонента (см. consolidate_shared_
# components(auto_streams=True)) — то же значение, что и planner.schedule.MAX_STREAMS_PER_NODE
# (core не импортирует planner — правило многослойности docs/ARCHITECTURE.md, — поэтому константа
# продублирована, а не переиспользована; держать оба значения синхронными при правке).
MAX_AUTO_STREAMS = 64


def _bp_job_fee(
    conn: sqlite3.Connection, eiv_basis_blueprint_type_id: int, params: cost.BuildParams, activity_id: int
) -> float:
    """Взнос за джоб копирования/инвенты (расход на попытку).

    База = 0.02 × manufacturing-EIV продукта ``eiv_basis_blueprint_type_id`` — далее обычная
    формула джоба (индекс активности × множитель станции роли × (1 + налог + SCC); в
    ``job_install_cost`` tax/scc — от EIV напрямую, не от итога). Для копирования берёт
    станцию роли ``copy``, для инвенты — роли ``invention``.

    КРИТИЧНО, какой чертёж передавать: для КОПИРОВАНИЯ — это ТОТ ЖЕ T1-чертёж, что копируется
    (T1-продукт — источник ценности копии). Для ИНВЕНТЫ — это ЧЕРТЁЖ РЕЗУЛЬТАТА (T2), а НЕ
    T1-источник! Подтверждено разбором реального тултипа EVE (инвента Nergal из Damavik
    Blueprint): «Estimated Item Value» в Job Cost — это EIV материалов Т2-чертежа Nergal
    (~133.76М), а НЕ материалов T1-чертежа Damavik Blueprint (~0.68М, в 197 раз меньше) — с
    T1-чертежом взнос за инвенту занижен на два порядка.

    Индекс активности — системы СТАНЦИИ роли (``cost.cost_system``: своя система станции, без
    станции — система стройки).
    """
    return _bp_job_fee_info(conn, eiv_basis_blueprint_type_id, params, activity_id)[0]


def _bp_job_fee_info(
    conn: sqlite3.Connection, eiv_basis_blueprint_type_id: int, params: cost.BuildParams, activity_id: int
) -> tuple[float, int, float]:
    """``_bp_job_fee`` + где он считан: (взнос, система станции роли, индекс этой системы) —
    для разбивки взносов копирования/инвенты в Калькуляторе."""
    fm = cost.facility_mults(params, activity_id, None)
    system_id = cost.cost_system(params, fm)
    ci = prices.cost_index(conn, system_id, activity_id)
    mats = bom.materials(conn, eiv_basis_blueprint_type_id, bom.MANUFACTURING)
    eiv1 = cost.eiv(conn, mats, 1)
    if eiv1 <= 0:
        return 0.0, system_id, ci
    fee = cost.job_install_cost(
        BP_JOB_BASE_FACTOR * eiv1, ci, fm.cost_mult, fm.facility_tax, params.scc_surcharge
    )
    return fee, system_id, ci


def _t1_copy_cost(conn: sqlite3.Connection, t1_bp_type_id: int, params: cost.BuildParams) -> float:
    """Стоимость копи-джоба одной 1-прогонной T1-копии (роль станции ``copy``) — EIV от
    материалов САМОГО T1-чертежа (то, что копируется)."""
    return _bp_job_fee(conn, t1_bp_type_id, params, cost.COPYING)


@dataclass
class MaterialLine:
    type_id: int
    name: str
    quantity: int
    source: str            # 'buy' | 'build' | 'unknown'
    unit_cost: float | None
    subtotal: float | None
    child: NodeResult | None = None
    buy_hub: str | None = None    # 'jita' | 'cj' — выбранный хаб закупки (если source='buy')
    buy_shortage: bool = False    # на выбранном хабе не хватает объёма под quantity


@dataclass
class NodeResult:
    product_type_id: int
    name: str
    activity_id: int
    blueprint_type_id: int
    runs: int
    streams: int
    produced: int          # всего единиц продукта = runs × qty_per_run
    material_cost: float
    job_cost: float
    total_cost: float
    unit_cost: float
    lines: list[MaterialLine] = field(default_factory=list)
    missing_prices: list[int] = field(default_factory=list)
    blueprint_cost: float = 0.0
    blueprint_source: str = ""  # owned_bpo|owned_bpc|invention|manual|missing|missing_reaction
    decryptor: str | None = None  # выбранный декриптор (если инвента с авто-оптимумом)
    # Как выбран декриптор инвенты этого узла (обычной или недостачи ранов — у узла бывает только
    # одна из двух, см. blueprint.choose_decryptor): задан вручную ([invention] none/fixed/
    # per_product) — «(задано вручную)» в Калькуляторе; ``decryptor_fallback`` — заданный вручную
    # недоступен (нет цены), взят авто-выбор — пометка в «Подготовка / проблемы».
    decryptor_manual: bool = False
    decryptor_fallback: str | None = None
    # TE, который планировщик ОБЯЗАН использовать для этого узла, а не выводить сам:
    # для invention — TE итоговой BPC (база + декриптор), для manual/missing/missing_reaction —
    # ``default_te`` («TE, если нет своего», аналог default_me). None у owned_bpo/owned_bpc —
    # там TE — свойство КОНКРЕТНОЙ физической копии/персонажа, планировщик сам выбирает лучшую
    # среди кандидатов (см. planner.schedule.schedule_jobs) — здесь фиксировать нечего.
    resulting_te: int | None = None
    # Только для реакций: копий формулы во владении / нужно под текущее число потоков.
    reaction_bp_owned: int | None = None
    reaction_bp_needed: int | None = None
    # Только для source='owned_bpc_insufficient': остаток ранов своей копии / сколько нужно.
    bpc_runs_owned: int | None = None
    bpc_runs_needed: int | None = None
    # Только для source='owned_bpc_insufficient' и когда чертёж вообще инвентится: сколько
    # ПОПЫТОК инвенты (Т1-копий) нужно ЗАПУСТИТЬ, чтобы закрыть именно недостачу (bpc_runs_needed
    # − bpc_runs_owned), не всю постройку заново — тот же формат, что и у обычной инвенты
    # («Заинвентить: X → N шт»), просто число прогонов — не весь продукт. None, если чертёж не
    # инвентится вообще (тогда остаётся только «купи копию на рынке»).
    bpc_shortfall_invention_attempts: int | None = None
    # Стоимость ОДНОЙ попытки (T1-копия + джоб-взнос инвенты) при закрытии недостачи — уже
    # ЗАЛОЖЕНА в blueprint_cost/total_cost/unit_cost этого узла (см. хвост build_node_cost:
    # недостача ранов — не «бесплатный» sunk cost, а честная цена её закрытия инвентой).
    # Тут — для «Сроки по джобам» (report.py ищет per-attempt стоимость по product_type_id
    # для строки джоба инвенты).
    bpc_shortfall_invention_fee_per_attempt: float | None = None
    # Декриптор (авто-оптимум по стоимости попытки), которым закрывается ИМЕННО недостача
    # ранов, — для показа в UI/отчёте. Аналог ``decryptor`` выше, но для ветки
    # owned_bpc_insufficient, не полной инвенты.
    bpc_shortfall_decryptor: str | None = None
    # Итоговый шанс успеха этой инвенты недостачи (скиллы лучшего инвентора × декриптор, как
    # invention_breakdown["probability"] у обычной инвенты) — для подписи в UI/отчёте.
    bpc_shortfall_probability: float | None = None
    # Только для blueprint_source='reprocess' (опция params.reprocessing_efficiency>0):
    # прекурсор, который перерабатывали, и СПРАВОЧНЫЙ ISK-эквивалент побочных продуктов
    # переработки — В material_cost/total_cost НЕ входит (побочку не планируется продавать, это
    # просто бонус сверху для UI/отчёта, не часть себестоимости).
    reprocess_source_id: int | None = None
    reprocess_source_name: str | None = None
    reprocess_byproduct_credit: float = 0.0
    # Физический выход побочки этого узла переработки (material_type_id, name, quantity) — НЕ
    # ISK, чистое количество; что РЕАЛЬНО осядет на складе физически, чтобы web-слой мог
    # показать игроку «сколько чего останется», агрегируя по всем узлам дерева (см.
    # sourcing.collect_reprocess_byproducts).
    reprocess_byproducts: tuple[tuple[int, str, int], ...] = ()
    # T1-чертёж для инвенты (напр. Fenrir для Nomad) и владеем ли им — для предупреждения.
    invention_source_id: int | None = None
    invention_source_name: str | None = None
    invention_source_owned: bool = True
    # Разбивка стоимости инвенты по компонентам попытки (для UI).
    invention_breakdown: dict | None = None
    # разбивка стоимости установки джоба (job_cost = eiv × cost_index × cost_mult × (1+tax+scc))
    eiv: float = 0.0
    cost_index: float = 0.0
    cost_mult: float = 1.0
    facility_tax: float = 0.0
    scc_surcharge: float = 0.0
    # система, по которой взят cost_index (станции этого джоба или стройки — cost.cost_system)
    cost_system_id: int = 0
    # Откуда T1-копия для инвенты этого узла (обычной или недостачи ранов): "bpo" — копи-джобом
    # со своего оригинала (тогда планировщик может поставить копи-джобы, [planner]
    # schedule_copy_jobs), "bpc" — своя копия, "manual" — ручная цена BPC, None — не во владении.
    # ``invention_t1_bpos`` — сколько своих BPO (столько копи-джобов могут идти параллельно);
    # ``invention_t1_copy`` — стоимость копи-джоба на одну попытку (= прогон копии), та же, что
    # уже заложена в blueprint_cost (для «Сроки по джобам» отчёта — без задвоения).
    invention_t1_kind: str | None = None
    invention_t1_bpos: int = 0
    invention_t1_copy: float = 0.0


def is_unbuildable_leaf(node: NodeResult) -> bool:
    """True — узел вообще НЕ имеет дерева постройки (чистая покупка/непостроиваемое мета-дроп),
    и вызывающему стоит взять его landed-цену напрямую, а не идти в ``node.lines``.

    ЛОЖНО для ``blueprint_source == 'reprocess'`` — переработка (``_try_reprocess_node``) тоже
    даёт ``blueprint_type_id == 0`` (нет обычного industryActivity-чертежа), но дерево
    постройки у неё РЕАЛЬНОЕ (прекурсор в ``node.lines[0].child``), просто не через чертёж —
    его нельзя схлопывать в «просто купи по рынку», иначе вся экономия переработки теряется."""
    return node.blueprint_type_id == 0 and node.blueprint_source != "reprocess"


def aggregate_costs(node: NodeResult) -> tuple[float, float, float]:
    """Суммарные затраты по ВСЕМУ дереву узла: (купленные материалы, все джобы, все чертежи).

    Купленные материалы — это листья-покупки; стоимость постройки суб-компонентов
    раскладывается на джобы/чертежи/материалы рекурсивно. Сумма трёх == node.total_cost.

    Узел-обёртка переработки (``blueprint_source == "reprocess"``) — ИСКЛЮЧЕНИЕ из этой
    рекурсии: его единственная строка ссылается на РЕАЛЬНУЮ постройку прекурсора (со своими
    материалами/джобом/чертежом), но переработка — атомарная замена «на что купить/построить
    этот материал», а не отдельный джоб — поэтому весь расход прекурсора (даже если сам
    прекурсор строился по чертежу со своим джобом) считаем ОДНИМ материальным расходом узла, а
    не расщепляем на джобы/чертежи (job_cost/blueprint_cost узла переработки — всегда 0, см.
    конструктор)."""
    if node.blueprint_source == "reprocess":
        return node.material_cost, node.job_cost, node.blueprint_cost

    jobs = node.job_cost
    blueprints = node.blueprint_cost
    materials = 0.0
    for ln in node.lines:
        if ln.child is not None:
            # Суб-сборка может перепроизводить (ceil прогонов) — родитель платит лишь за
            # потреблённое (ln.quantity), поэтому масштабируем разбивку ребёнка на эту долю.
            cm, cj, cb = aggregate_costs(ln.child)
            frac = ln.quantity / ln.child.produced if ln.child.produced else 1.0
            materials += cm * frac
            jobs += cj * frac
            blueprints += cb * frac
        elif ln.subtotal is not None:
            materials += ln.subtotal  # купленный материал (лист)
    return materials, jobs, blueprints


def collect_reprocess_byproducts(roots: list[NodeResult]) -> dict[int, tuple[str, int]]:
    """Просуммировать физическую побочку переработки (``NodeResult.reprocess_byproducts``) по
    ВСЕМ деревьям ``roots`` — «что реально осядет на складе GPLB-C после такой стройки».

    Каждый УНИКАЛЬНЫЙ узел (по ``id()``) — ровно один раз: после ``consolidate_shared_
    components`` общий узел переработки достижим из НЕСКОЛЬКИХ родительских строк сразу, без
    дедупа по идентичности объекта побочка задваивалась/затраивалась бы ровно так же, как в
    самом слиянии (см. докстринг ``_walk`` внутри ``consolidate_shared_components`` — тот же
    класс двойного учёта, пример — Crystallite Alloy). ``-> {material_type_id:
    (name, суммарное_количество)}``, вызывающий сам решает, по какой цене это оценивать
    (``cost.unit_sell_value`` для реалистичной выручки с продажи)."""
    seen: set[int] = set()
    totals: dict[int, list] = {}

    def walk(n: NodeResult) -> None:
        if id(n) in seen:
            return
        seen.add(id(n))
        for mid, mname, qty in n.reprocess_byproducts:
            entry = totals.setdefault(mid, [mname, 0])
            entry[1] += qty
        for ln in n.lines:
            if ln.child is not None:
                walk(ln.child)

    for root in roots:
        walk(root)
    return {mid: (mname, qty) for mid, (mname, qty) in totals.items()}


def inbound_freight(conn: sqlite3.Connection, node: NodeResult, params: cost.BuildParams) -> float:
    """Сумма входящего фрахта по всему дереву: для каждого покупного листа объём × ставка
    до GPLB-C (с учётом выбранного хаба и доли потребления под-сборок). Это часть material_cost.

    Ставка (``params.freight``) — простое ISK/m³. Для fixed_jump-плечей она не статична из
    конфига, а батчево пересчитана заранее под ВЕСЬ заказ (см. ``core.batched_freight_params``);
    здесь про рейсы сознательно не знаем (см. комментарий в ``cost._hub_options``)."""
    f_cg = params.freight.get(("c_j6mt", "gplb_c"), 0.0)
    f_jc = params.freight.get(("jita", "c_j6mt"), 0.0)

    def walk(n: NodeResult) -> float:
        total = 0.0
        for ln in n.lines:
            if ln.child is not None:
                frac = ln.quantity / ln.child.produced if ln.child.produced else 1.0
                total += walk(ln.child) * frac
            elif ln.source == "buy" and ln.unit_cost is not None:
                vol = prices.volume(conn, ln.type_id) or 0.0
                rate = f_cg if ln.buy_hub == "cj" else (f_jc + f_cg)
                total += ln.quantity * vol * rate
        return total

    return walk(node)


def inbound_volume_by_leg(conn: sqlite3.Connection, node: NodeResult) -> dict[tuple[str, str], float]:
    """Суммарный объём материалов по каждому маршрутному плечу закупки (Jita→C-J6MT,
    C-J6MT→GPLB-C) по всему дереву — для батч-расчёта рейсов на ВЕСЬ заказ разом
    (см. ``core.batched_freight_params``), а не по каждому материалу отдельно."""

    def walk(n: NodeResult) -> dict[tuple[str, str], float]:
        totals: dict[tuple[str, str], float] = {}
        for ln in n.lines:
            if ln.child is not None:
                frac = ln.quantity / ln.child.produced if ln.child.produced else 1.0
                for leg, v in walk(ln.child).items():
                    totals[leg] = totals.get(leg, 0.0) + v * frac
            elif ln.source == "buy" and ln.unit_cost is not None:
                vol = prices.volume(conn, ln.type_id) or 0.0
                v = ln.quantity * vol
                leg_cg = ("c_j6mt", "gplb_c")
                totals[leg_cg] = totals.get(leg_cg, 0.0) + v
                if ln.buy_hub != "cj":
                    leg_jc = ("jita", "c_j6mt")
                    totals[leg_jc] = totals.get(leg_jc, 0.0) + v
        return totals

    return walk(node)


@functools.lru_cache(maxsize=4096)
def _cached_reprocessing_sources(conn: sqlite3.Connection, type_id: int):
    """``reprocess.reprocessing_sources`` за большим деревом (напр. Nomad — тысячи НЕ
    дедуплицированных вхождений одного и того же материала в Проходе 1, дедуп только ПОСЛЕ,
    в ``consolidate_shared_components``) вызывалась бы по одному и тому же ``type_id`` сотни
    раз — SDE-данные неизменны в рамках соединения, кэш убирает эту избыточность (без него
    Nomad с выключенной опцией — 0.03с, с включённой — свыше 60с, тайм-аут).
    ``conn`` хэшируется по identity (обычные объекты sqlite3.Connection) — так же безопасно,
    как обычный dict-кэш по id(conn); maxsize ограничивает рост при много́м соединений подряд
    (веб-слой открывает новое соединение на каждый запрос)."""
    return reprocess.reprocessing_sources(conn, type_id)


@functools.lru_cache(maxsize=4096)
def _source_needs_target(conn: sqlite3.Connection, source_type_id: int, target_type_id: int) -> bool:
    """True, если СОБСТВЕННЫЙ чертёж ``source_type_id`` (обычная постройка, НЕ переработка)
    требует ``target_type_id`` как материал — предлагать переработку через такой прекурсор
    никогда не имеет смысла (и экономически абсурдно: пришлось бы сперва построить материал,
    чтобы затем переработать его обратно в него же).

    Пример (Nomad, эфф. переработки 55%): «Fulleroferrocene Power Conduits»
    (устаревший T3-сабсистем-компонент) сам требует Methanofullerene как ингредиент своего
    обычного чертежа — И ОДНОВРЕМЕННО легитимно числится в SDE как источник переработки ОБРАТНО
    в Methanofullerene (реальная механика EVE — реверс-инжиниринг старых компонентов). Обычный
    ``chain`` в ``_try_reprocess_node`` защищает только ОДИН рекурсивный вызов целиком —
    независимые LLC-пересборки (``web/stock_net.py``, каждая со своей ПУСТОЙ ``chain``) через
    общий ``params.reprocess_cache`` способны «замкнуть» такую пару в реальный цикл ОБЪЕКТОВ
    (не просто типов) — это ``RecursionError`` при обходе финального дерева.
    Дешёвая проверка на ОДИН уровень вглубь (не полный обход графа) отсекает именно этот
    случай заранее, ДО попытки сорсинга такого прекурсора."""
    src_bp = bom.blueprint_for_product(conn, source_type_id)
    if src_bp is None:
        return False
    return any(
        m.type_id == target_type_id
        for m in bom.materials(conn, src_bp.blueprint_type_id, src_bp.activity_id)
    )


def _try_reprocess_node(
    conn: sqlite3.Connection,
    product_type_id: int,
    name: str,
    target_quantity: int,
    streams: int,
    params: cost.BuildParams,
    *,
    default_me: int,
    default_te: int,
    max_depth: int,
    allow_build: bool,
    whole_blueprint: bool,
    depth: int,
    chain: frozenset[int],
    substream_fn,
    force_buy_extra: frozenset[int],
    me_overrides: dict[int, int] | None = None,
    te_overrides: dict[int, int] | None = None,
) -> NodeResult | None:
    """Альтернатива постройке по обычному чертежу: переработать (reprocessing — ОТДЕЛЬНАЯ от
    чертежей механика EVE, см. ``core/reprocess.py``) более дешёвый прекурсор. Опция —
    ``params.reprocessing_efficiency<=0`` выключает её полностью (None сразу).

    Прекурсор сам сорсится через ``_source_material`` (может оказаться дешевле купить его
    готовым, а не строить — то же дерево решений, что и для обычных материалов). Побочные
    продукты переработки в себестоимость НЕ засчитываются (продавать их не планируется — это
    чистый бонус сверху, а не то, от чего зависит выбор купить/строить) —
    ``reprocess_byproduct_credit``/``reprocess_byproducts`` на узле остаются только справочно (сколько физически осядет на складе и по какой цене их МОЖНО было бы продать, см.
    ``collect_reprocess_byproducts``), в ``material_cost``/``unit_cost`` не участвуют. Сама
    переработка считается МГНОВЕННОЙ/бесплатной (без отдельного джоба установки) — упрощение:
    реального налога станции на переработку Forge не знает.

    Возвращает узел ТОЛЬКО если у прекурсора вообще есть цена — иначе None (сравнивать не с
    чем, вызывающий просто останется на обычном пути постройки).

    Кэшируется в ``params.reprocess_cache`` по ``(product_type_id, target_quantity)`` — БЕЗ
    учёта ``chain`` (см. докстринг поля в ``cost.BuildParams`` — обоснование, почему это
    безопасно). Без кэша один и тот же материал, встречающийся в огромном НЕ дедуплицированном
    дереве (капитальные T2-корабли вроде Nomad) сотни-тысячи раз, пересчитывался бы заново на
    КАЖДОЕ вхождение — фактически зависание (0.03с без опции → 60+с с ней)."""
    if params.reprocessing_efficiency <= 0 or target_quantity <= 0:
        return None
    cache_key = (product_type_id, target_quantity)
    if cache_key in params.reprocess_cache:
        return params.reprocess_cache[cache_key]
    sources = _cached_reprocessing_sources(conn, product_type_id)
    if not sources:
        params.reprocess_cache[cache_key] = None
        return None

    best: NodeResult | None = None
    for src in sources:
        if src.source_type_id in chain:
            continue  # прекурсор — уже предок в этой ветке (защита от цикла)
        if _source_needs_target(conn, src.source_type_id, product_type_id):
            continue  # прекурсор сам требует целевой материал как ингредиент — см. докстринг выше
        portions = reprocess.portions_needed(
            target_quantity, src.main.quantity, params.reprocessing_efficiency
        )
        if portions <= 0:
            continue
        source_qty = portions * src.portion_size
        # Кэш ВТОРОГО уровня — по (прекурсор, кол-во ПОРЦИЙ), не по целевому количеству target_
        # quantity: portions_needed округляет ВВЕРХ до целых порций, поэтому МНОГО разных
        # target_quantity (все ветки дерева, которым нужно от 1 до 73 шт. Dysporite при выходе
        # 73/порция) схлопываются в ОДНО и то же (source_type_id, source_qty) — именно этот
        # уровень кэша реально ловит повторы в огромном дереве (кэш по (product_type_id,
        # target_quantity) выше почти никогда не совпадает — разные ветки просят разные
        # количества). ``.child`` (дорогая построенная поддерево) переиспользуется, а ОБЁРТКА
        # ``MaterialLine`` создаётся заново на каждое вхождение — сплайс в stock_net.py мутирует
        # ``ln.child`` НА КОНКРЕТНОЙ строке, шарить сам объект строки между вхождениями было бы
        # багом (одна пересборка молча аукнулась бы во ВСЕХ остальных вхождениях).
        precursor_cache_key = ("precursor", src.source_type_id, source_qty)
        cached = params.reprocess_cache.get(precursor_cache_key)
        if cached is not None:
            precursor_line = MaterialLine(
                cached.type_id, cached.name, cached.quantity, cached.source,
                cached.unit_cost, cached.subtotal, cached.child,
                buy_hub=cached.buy_hub, buy_shortage=cached.buy_shortage,
            )
        else:
            precursor_line = _source_material(
                conn, src.source_type_id, source_qty, params,
                default_me=default_me, default_te=default_te, max_depth=max_depth, allow_build=allow_build,
                whole_blueprint=whole_blueprint, depth=depth,
                chain=chain | {product_type_id, _REPROCESS_MARKER},
                substream_fn=substream_fn, force_buy_extra=force_buy_extra,
                me_overrides=me_overrides, te_overrides=te_overrides,
            )
            params.reprocess_cache[precursor_cache_key] = precursor_line
        if precursor_line.subtotal is None:
            continue  # цена прекурсора неизвестна — сравнивать не с чем

        actual_main, byproducts = reprocess.reprocess_output(src, portions, params.reprocessing_efficiency)
        if actual_main <= 0:
            continue
        byproduct_credit = 0.0
        byproduct_qtys: list[tuple[int, str, int]] = []
        sell_region = params.cj6mt_region_id or params.jita_region_id
        for y, qty in byproducts:
            if qty <= 0:
                continue
            byproduct_qtys.append((y.material_type_id, y.name, qty))
            unit = cost.unit_sell_value(
                conn, y.material_type_id, params, sell_region, params.broker_fee, params.sales_tax
            )
            if unit is not None and unit > 0:
                byproduct_credit += unit * qty

        material_cost = precursor_line.subtotal  # кредит за побочку — справочный бонус, не в цене
        unit_cost = material_cost / actual_main
        if best is not None and unit_cost >= best.unit_cost:
            continue
        missing = list(precursor_line.child.missing_prices) if precursor_line.child else []
        best = NodeResult(
            product_type_id, name, 0, 0, target_quantity, streams, actual_main,
            material_cost, 0.0, material_cost, unit_cost, [precursor_line], missing,
            blueprint_source="reprocess",
            reprocess_source_id=src.source_type_id, reprocess_source_name=src.source_name,
            reprocess_byproduct_credit=byproduct_credit,
            reprocess_byproducts=tuple(byproduct_qtys),
        )
    params.reprocess_cache[cache_key] = best
    return best


def build_node_cost(
    conn: sqlite3.Connection,
    product_type_id: int,
    runs: int,
    streams: int,
    params: cost.BuildParams,
    *,
    default_me: int = 0,
    default_te: int = 0,
    max_depth: int = 8,
    allow_build: bool = True,
    whole_blueprint: bool = False,
    substream_fn=None,
    force_buy_extra: frozenset[int] = frozenset(),
    me_overrides: dict[int, int] | None = None,
    te_overrides: dict[int, int] | None = None,
    _depth: int = 0,
    _chain: frozenset[int] = frozenset(),
) -> NodeResult:
    """Себестоимость постройки ``runs`` прогонов продукта (с разбивкой на ``streams``).

    ``streams`` задаёт разбивку ЭТОГО (верхнего для вызова) узла. ``substream_fn`` —
    необязательная функция ``(product_type_id, runs) -> streams`` для ПОД-компонентов:
    ею, например, дробят промежуточные джобы под срок («≤ N дней на поток»). Если она не
    задана — под-компоненты строятся одним потоком. ME-округление считается на
    каждый джоб (правило 4), поэтому разбивка под-компонентов меняет итог материалов.

    ``default_te`` — TE, который планировщик использует для срока, если чертёж НЕ во владении
    и не добывается инвентой (см. ``NodeResult.resulting_te``); аналог ``default_me``, но для
    времени, а не материалов.

    ``me_overrides``/``te_overrides`` — принудительные ME/TE по КОНКРЕТНЫМ ``product_type_id``
    (не по глубине рекурсии!) — пользователь явно задал точное значение для товара корзины
    («Точный ME/TE» в корзине) или сравнивает уровни исследования BPO (``/api/compare-me``).
    Проверяются по ключу словаря на КАЖДОМ узле дерева (в т.ч. под-компонентах — рекурсия
    передаёт их дальше), поэтому пересборка верхнего узла ПОСЛЕ первого прохода (списание
    склада — ``report.py::_shrink_node``, ``stock_net.net_and_finalize``) не теряет оверрайд,
    как терял бы `_depth`-based механизм. Действуют ТОЛЬКО в ветке owned/manual/missing — у
    инвенты ME/TE — пара от выбранного декриптора, свободного параметра нет, оверрайд там
    не применяется."""
    bp = bom.blueprint_for_product(conn, product_type_id)
    name = prices.type_name(conn, product_type_id)
    if bp is None:
        # Нечего строить — это «лист» (сырьё/покупное). Узел нулевой постройки.
        return NodeResult(product_type_id, name, 0, 0, runs, streams, 0, 0.0, 0.0, 0.0, 0.0)

    # Чертёж физически не даёт запустить ОДИН джоб больше max_production_limit прогонов
    # (реальный лимит EVE, из SDE) — если runs превышает его, EVE ЗАСТАВЛЯЕТ разбить на
    # несколько отдельных джобов, и КАЖДЫЙ round'ится (ceil) на СВОИ прогоны отдельно (правило
    # 4 docs/ARCHITECTURE.md). Если взять ``streams`` как есть (параллелизм, по умолчанию 1),
    # при runs > max_production_limit получится ОДИН виртуальный джоб на все прогоны сразу,
    # то есть МЕНЬШЕ округления, чем реально происходит в игре (ceil субаддитивен: ceil(a+b) ≤
    # ceil(a)+ceil(b)) — систематическая НЕДОоценка материала (напр. Missile Guidance Enhancer II
    # ×20 и Large Shield Extender II ×30, оба max_production_limit=10, — не хватит по 1 шт.
    # материала). Поднимаем ``streams`` до МИНИМУМА джобов, которые реально
    # придётся запустить — это НЕ меняет параллелизм по своей природе (принудительные лишние
    # джобы могут идти и последовательно), но планировщик (``planner.schedule.
    # extract_jobs_many``) всё равно пересчитывает разбивку джобов ТОЛЬКО из ``node.streams`` —
    # поэтому это заодно и единственный способ, которым расписание физически может узнать, что
    # нужно НЕСКОЛЬКО джобов, а не один невозможный сверх-лимита.
    limit = prices.max_production_limit(conn, bp.blueprint_type_id)
    if limit and runs > 0:
        streams = max(streams, math.ceil(runs / limit))

    runs_per_job = cost.split_runs(runs, streams)
    mats = bom.materials(conn, bp.blueprint_type_id, bp.activity_id)

    # Станция под этот джоб (по активности и группе/категории продукта) → её множители.
    fm = cost.facility_mults(
        params, bp.activity_id, prices.group_id(conn, product_type_id),
        prices.category_id(conn, product_type_id),
    )

    def source_all(me: int) -> tuple[list[MaterialLine], list[int], float]:
        """Просорсить все материалы при данном ME: строки, недостающие цены, сумма."""
        lns: list[MaterialLine] = []
        miss: list[int] = []
        mc = 0.0
        for m in mats:
            qty = cost.material_quantity(
                m.base_quantity, runs_per_job, me, fm.material_mult, bp.activity_id
            )
            line = _source_material(
                conn, m.type_id, qty, params,
                default_me=default_me, default_te=default_te, max_depth=max_depth, allow_build=allow_build,
                whole_blueprint=whole_blueprint, depth=_depth, chain=_chain | {product_type_id},
                substream_fn=substream_fn, force_buy_extra=force_buy_extra,
                me_overrides=me_overrides, te_overrides=te_overrides,
            )
            lns.append(line)
            if line.subtotal is None:
                miss.append(m.type_id)
            else:
                mc += line.subtotal
            if line.child is not None:
                miss.extend(line.child.missing_prices)
        return lns, miss, mc

    bpc = bp_cost.blueprint_cost(
        conn, product_type_id, bp.blueprint_type_id, bp.activity_id, runs, params,
        streams_needed=len(runs_per_job),
    )

    # T1-чертёж для инвенты (Fenrir и т.п.): нужен для запуска инвенты — проверяем владение.
    inv_src_id: int | None = None
    inv_src_name: str | None = None
    inv_src_owned = True
    inv_breakdown: dict | None = None
    bpc_shortfall_attempts: int | None = None
    bpc_shortfall_fee: float | None = None
    bpc_shortfall_option: bp_cost.InventionOption | None = None
    decryptor_choice: bp_cost.DecryptorChoice | None = None
    # откуда T1-копия (bpo/bpc/manual/None), сколько своих BPO и цена копии на попытку — для
    # копи-джобов в расписании (NodeResult.invention_t1_*)
    t1_kind: str | None = None
    t1_bpos = 0
    t1_copy_cost = 0.0

    def _t1_source(src_id: int) -> tuple[bool, float, str | None, int]:
        """(владеем ли T1-источником, цена T1-копии на попытку, kind, своих BPO): ручная цена BPC
        сильнее; свой BPO/BPC → копия = копи-джоб (станция роли copy)."""
        manual = params.blueprint_overrides.get(src_id)
        if manual is not None:
            return True, manual, "manual", 0
        kind = bp_cost.owned_kind(conn, src_id, params.blueprint_location_ids)
        if kind is None:
            return False, 0.0, None, 0
        bpos = bp_cost.owned_bpo_count(conn, src_id, params.blueprint_location_ids) if kind == "bpo" else 0
        return True, _t1_copy_cost(conn, src_id, params), kind, bpos

    if bpc.source == "invention":
        t1_copy_cost = 0.0
        invention_job_fee = 0.0
        # где считаны взносы (система станции роли и её индекс) — для разбивки в Калькуляторе
        fee_where: dict[str, float | int | None] = {}
        inv_src_id = bp_cost.invention_source(conn, bp.blueprint_type_id)
        if inv_src_id is not None:
            inv_src_name = prices.type_name(conn, inv_src_id)
            # Взнос за сам джоб инвенты — платится за каждую попытку (станция роли invention).
            # EIV — от Т2-ЧЕРТЕЖА РЕЗУЛЬТАТА (bp.blueprint_type_id), НЕ от T1-источника
            # (inv_src_id) — см. docstring _bp_job_fee.
            invention_job_fee, inv_sys, inv_ci = _bp_job_fee_info(
                conn, bp.blueprint_type_id, params, cost.INVENTION)
            fee_where.update(job_fee_system_id=inv_sys, job_fee_cost_index=inv_ci)
            # Ручная цена T1-копии (напр. покупка Fenrir BPC) — расход на попытку (владение не
            # нужно — предупреждения нет); свой T1 BPO/BPC → T1-копия = стоимость копи-джоба.
            inv_src_owned, t1_copy_cost, t1_kind, t1_bpos = _t1_source(inv_src_id)
            if t1_kind in ("bpo", "bpc"):
                _fee, copy_sys, copy_ci = _bp_job_fee_info(conn, inv_src_id, params, cost.COPYING)
                fee_where.update(t1_copy_system_id=copy_sys, t1_copy_cost_index=copy_ci)
        # Авто-оптимум по декриптору: каждый меняет ME (→ материалы) и стоимость инвенты.
        # Минимизируем суммарную стоимость материалов + инвенты (job_cost от декриптора не зависит).
        options = bp_cost.invention_options(
            conn, bp.blueprint_type_id, params,
            extra_attempt_cost=t1_copy_cost + invention_job_fee,
        )
        ref_lines, ref_missing, _ = source_all(bp_cost.INVENT_BASE_ME)

        def mat_cost_at(me: int) -> float:
            total = 0.0
            for m, ln in zip(mats, ref_lines, strict=True):
                if ln.unit_cost is None:
                    continue
                total += ln.unit_cost * cost.material_quantity(
                    m.base_quantity, runs_per_job, me, fm.material_mult, bp.activity_id
                )
            return total

        def invent_total(o: bp_cost.InventionOption) -> float:
            # Целые попытки инвенты — НЕЛЬЗЯ провести дробную попытку: T1-копия, декриптор и
            # датакоры расходуются на КАЖДУЮ попытку заново, включая неудачные. Статистическая
            # АМОРТИЗАЦИЯ (per_run × runs, то есть дробное число попыток по вероятности) не
            # годится: реально готовят и запускают целое число попыток (округление ВСЕГДА вверх,
            # даже 3.1 → 4). Поэтому bpcs (целых копий под runs) × попыток на копию
            # (ceil(1/вероятность)) × цена попытки — вне зависимости от whole_blueprint
            # (тот флаг не влияет на инвенту, только на прочие активности, если появятся).
            bpcs = math.ceil(runs / o.runs_per_copy) if runs else 1
            attempts = bp_cost.attempts_for(bpcs, o.prob)
            attempt_cost = o.per_run * o.prob * o.runs_per_copy  # обратно «сырая» цена 1 попытки
            return attempts * attempt_cost

        # Декриптор — по настройке [invention] (авто-минимум по стоимости по умолчанию; либо без
        # декриптора / фиксированный / по товару — см. blueprint.choose_decryptor).
        decryptor_choice = bp_cost.choose_decryptor(
            options, product_type_id, params, key=lambda o: mat_cost_at(o.me) + invent_total(o))
        best = decryptor_choice.option
        me = best.me
        resulting_te = best.te   # TE итоговой BPC (база + декриптор) — планировщик обязан
        # использовать ИМЕННО его для срока, а не искать «владеемый» TE (копии-то ещё нет).
        bpcs_needed = math.ceil(runs / best.runs_per_copy) if runs else 1
        attempts_needed = bp_cost.attempts_for(bpcs_needed, best.prob)
        inv_total = invent_total(best)  # полная стоимость попытки — для информационной разбивки
        inv_breakdown = {
            "datacores": best.datacores,
            "t1_copy": t1_copy_cost,
            "job_fee": invention_job_fee,
            "decryptor_cost": best.decryptor_cost,
            "attempt": best.datacores + t1_copy_cost + invention_job_fee + best.decryptor_cost,
            # Итоговый шанс = база SDE × скиллы лучшего инвентора × декриптор (≤ 1.0) — именно
            # по нему посчитаны попытки; составляющие — для разбивки в Калькуляторе.
            "probability": best.prob,
            "base_probability": best.base_prob,
            "skill_mult": best.skill_mult,
            "decryptor_mult": best.decryptor_mult,
            "inventor_name": best.inventor_name,
            "skills_enabled": params.invention_use_skills,
            "runs_per_copy": best.runs_per_copy,
            "bpcs": bpcs_needed,
            # Попыток инвенты (= T1-копий) реально нужно ЗАПУСТИТЬ — целое число, округлено
            # вверх (см. invent_total выше); ИМЕННО это число уже заложено в "total" ниже.
            "attempts": attempts_needed,
            "total": inv_total,
            "per_run": inv_total / runs if runs else inv_total,
            # система станции роли invention/copy и её индекс, по которым считаны взносы
            **fee_where,
        }
        # Финальные строки при выбранном ME: масштабируем кол-ва (без повторной рекурсии).
        lines, missing, material_cost = [], list(ref_missing), 0.0
        for m, ln in zip(mats, ref_lines, strict=True):
            ln.quantity = cost.material_quantity(
                m.base_quantity, runs_per_job, me, fm.material_mult, bp.activity_id
            )
            if ln.unit_cost is not None:
                ln.subtotal = ln.unit_cost * ln.quantity
                material_cost += ln.subtotal
            lines.append(ln)

        # Датакоры и декриптор — НАСТОЯЩИЕ покупные материалы (список закупок + проверка
        # остатков на GPLB-C через тот же механизм, что и у обычных материалов постройки),
        # а не только абстрактная ISK-сумма внутри blueprint_cost: расходуются физически на
        # КАЖДУЮ попытку инвенты, игрок реально их покупает и может держать про запас на складе.
        consumables_cost = 0.0
        for dc_type_id, qty_per_attempt in bp_cost.invention_datacore_materials(conn, bp.blueprint_type_id):
            dc_line = _buy_only_line(conn, params, dc_type_id, qty_per_attempt * attempts_needed)
            lines.append(dc_line)
            if dc_line.subtotal is not None:
                consumables_cost += dc_line.subtotal
            else:
                missing.append(dc_type_id)
        if best.decryptor_type_id is not None:
            d_line = _buy_only_line(conn, params, best.decryptor_type_id, attempts_needed)
            lines.append(d_line)
            if d_line.subtotal is not None:
                consumables_cost += d_line.subtotal
            else:
                missing.append(best.decryptor_type_id)
        material_cost += consumables_cost

        # blueprint_cost — ТОЛЬКО «право» на чертёж (T1-копия + взнос за джоб инвенты),
        # без датакоров/декриптора (те уже в material_cost выше через настоящие MaterialLine).
        bp_only_total = (t1_copy_cost + invention_job_fee) * attempts_needed
        bpc = bp_cost.BlueprintCost(
            bp_only_total / runs if runs else bp_only_total, bp_only_total, "invention", best.decryptor
        )
    else:
        if bpc.source in ("owned_bpo", "owned_bpc", "owned_bpc_insufficient"):
            # Даже при нехватке ранов (owned_bpc_insufficient) ME берём от СВОЕЙ копии — она
            # физически есть и её ME governs материал ЭТОЙ постройки, нехватка ранов — только
            # предупреждение (см. blueprint_cost), не смена источника ME.
            owned_me = prices.best_owned_me(conn, bp.blueprint_type_id, params.blueprint_location_ids)
            me = owned_me if owned_me is not None else default_me
            resulting_te = None  # TE — свойство конкретной копии/чара, решает планировщик
            if bpc.source == "owned_bpc_insufficient":
                # Сколько попыток инвенты нужно, чтобы закрыть НЕДОСТАЧУ (не всю постройку) —
                # конкретное число, а не просто «докупи/заинвентни ещё» (тот же декрипторный
                # оптимум по стоимости попытки, что и в обычной инвенте — см. invent_total выше,
                # только на shortfall прогонов, а не на весь runs).
                shortfall = max(0, (bpc.bpc_runs_needed or 0) - (bpc.bpc_runs_owned or 0))
                inv_src_id = bp_cost.invention_source(conn, bp.blueprint_type_id)
                if inv_src_id is not None and shortfall > 0:
                    inv_src_name = prices.type_name(conn, inv_src_id)
                    inv_src_owned, t1_copy_cost, t1_kind, t1_bpos = _t1_source(inv_src_id)
                    invention_job_fee = _bp_job_fee(conn, bp.blueprint_type_id, params, cost.INVENTION)
                    shortfall_options = bp_cost.invention_options(
                        conn, bp.blueprint_type_id, params,
                        extra_attempt_cost=t1_copy_cost + invention_job_fee,
                    )

                    def _shortfall_invent_cost(o: bp_cost.InventionOption) -> float:
                        bpcs = math.ceil(shortfall / o.runs_per_copy)
                        attempts = bp_cost.attempts_for(bpcs, o.prob)
                        attempt_cost = o.per_run * o.prob * o.runs_per_copy
                        return attempts * attempt_cost

                    if shortfall_options:
                        # тот же выбор декриптора, что и у обычной инвенты ([invention]); ME копии
                        # недостачи на материалы ЭТОЙ постройки не влияет — минимизируем только
                        # стоимость самих попыток.
                        decryptor_choice = bp_cost.choose_decryptor(
                            shortfall_options, product_type_id, params, key=_shortfall_invent_cost)
                        best_opt = decryptor_choice.option
                        bpcs_needed = math.ceil(shortfall / best_opt.runs_per_copy)
                        bpc_shortfall_attempts = (
                            bp_cost.attempts_for(bpcs_needed, best_opt.prob) if best_opt.prob else None
                        )
                        if bpc_shortfall_attempts is not None:
                            bpc_shortfall_fee = t1_copy_cost + invention_job_fee
                            bpc_shortfall_option = best_opt
        else:  # manual | missing | missing_reaction (для реакций ME игнорируется ниже)
            me = default_me
            resulting_te = default_te
        if me_overrides and product_type_id in me_overrides:
            me = me_overrides[product_type_id]
        if te_overrides and product_type_id in te_overrides:
            resulting_te = te_overrides[product_type_id]
        lines, missing, material_cost = source_all(me)

        if bpc_shortfall_attempts is not None and bpc_shortfall_option is not None:
            # Датакоры и декриптор на попытки инвенты недостачи — НАСТОЯЩИЕ покупные материалы
            # (список закупок + проверка остатков GPLB-C), той же логикой, что и у обычной
            # инвенты выше (см. комментарий там) — иначе total_cost учёл бы только T1-копию и
            # джоб-взнос, но не датакоры, которые тоже реально расходуются на КАЖДУЮ попытку.
            for dc_type_id, qty_per_attempt in bp_cost.invention_datacore_materials(conn, bp.blueprint_type_id):
                dc_line = _buy_only_line(conn, params, dc_type_id, qty_per_attempt * bpc_shortfall_attempts)
                lines.append(dc_line)
                if dc_line.subtotal is not None:
                    material_cost += dc_line.subtotal
                else:
                    missing.append(dc_type_id)
            if bpc_shortfall_option.decryptor_type_id is not None:
                d_line = _buy_only_line(conn, params, bpc_shortfall_option.decryptor_type_id, bpc_shortfall_attempts)
                lines.append(d_line)
                if d_line.subtotal is not None:
                    material_cost += d_line.subtotal
                else:
                    missing.append(bpc_shortfall_option.decryptor_type_id)

    eiv_value = cost.eiv(conn, mats, runs)
    # Индекс — системы СТАНЦИИ этого джоба (своя система или система стройки, см. cost_system).
    cost_system_id = cost.cost_system(params, fm)
    ci = prices.cost_index(conn, cost_system_id, bp.activity_id)
    job_cost = cost.job_install_cost(
        eiv_value, ci, fm.cost_mult, fm.facility_tax, params.scc_surcharge
    )

    # Недостача ранов своей копии — НЕ «бесплатный» sunk cost: если число попыток и цена
    # попытки известны, в себестоимость идёт реальная цена её закрытия инвентой, а не 0 ISK
    # (иначе total_cost делал бы вид, что недостающие раны появятся сами, задаром).
    shortfall_cost = (
        bpc_shortfall_attempts * bpc_shortfall_fee
        if bpc_shortfall_attempts is not None and bpc_shortfall_fee is not None
        else 0.0
    )
    blueprint_cost_total = bpc.total + shortfall_cost

    produced = runs * bp.product_qty_per_run
    total = material_cost + job_cost + blueprint_cost_total
    unit = total / produced if produced else 0.0
    normal_node = NodeResult(
        product_type_id, name, bp.activity_id, bp.blueprint_type_id, runs, streams, produced,
        material_cost, job_cost, total, unit, lines, missing,
        blueprint_cost=blueprint_cost_total, blueprint_source=bpc.source, decryptor=bpc.decryptor,
        decryptor_manual=bool(decryptor_choice and decryptor_choice.manual),
        decryptor_fallback=decryptor_choice.fallback if decryptor_choice else None,
        resulting_te=resulting_te,
        invention_source_id=inv_src_id, invention_source_name=inv_src_name,
        invention_source_owned=inv_src_owned, invention_breakdown=inv_breakdown,
        eiv=eiv_value, cost_index=ci, cost_mult=fm.cost_mult,
        facility_tax=fm.facility_tax, scc_surcharge=params.scc_surcharge,
        cost_system_id=cost_system_id,
        reaction_bp_owned=bpc.reaction_owned, reaction_bp_needed=bpc.reaction_needed,
        bpc_runs_owned=bpc.bpc_runs_owned, bpc_runs_needed=bpc.bpc_runs_needed,
        bpc_shortfall_invention_attempts=bpc_shortfall_attempts,
        bpc_shortfall_invention_fee_per_attempt=bpc_shortfall_fee,
        bpc_shortfall_decryptor=bpc_shortfall_option.decryptor if bpc_shortfall_option else None,
        bpc_shortfall_probability=bpc_shortfall_option.prob if bpc_shortfall_option else None,
        invention_t1_kind=t1_kind, invention_t1_bpos=t1_bpos, invention_t1_copy=t1_copy_cost,
    )
    if (
        params.reprocessing_efficiency > 0 and bp.activity_id == bom.REACTION
        and _REPROCESS_MARKER not in _chain
    ):
        # Опция: переработка более дешёвого прекурсора может оказаться дешевле прямой
        # постройки по чертежу (напр. Dysporite дешевле через переработку
        # Unrefined Dysporite, чем напрямую через Dysprosium). Сравниваем по unit_cost и берём
        # дешевле — единообразно для ЛЮБОГО вызывающего (make-or-buy, ребилд в stock_net.py
        # при частичном покрытии складом, прямой /api/cost), т.к. решение принимается здесь, а
        # не в _source_material.
        #
        # ``bp.activity_id == bom.REACTION`` — намеренное сужение области, а не общая механика:
        # ЛЮБОЙ предмет в EVE (корабль, модуль, компонент) технически перерабатывается обратно
        # в минералы (invTypeMaterials покрывает вообще всё продаваемое), но цель здесь —
        # арбитраж «Unrefined X реакция → переработка» конкретно для реакционных материалов
        # лунной добычи, а полный перебор для КАЖДОГО T1/T2-компонента огромного дерева
        # (капитальные корабли вроде Nomad — тысячи вхождений) — фактически зависание (0.03с без
        # сужения по activity → 60+с, даже с кэшами на обоих уровнях — переработка
        # модулей/компонентов почти никогда не выгоднее покупки, но САМА ПРОВЕРКА варианта
        # комбинаторно взрывается на дереве такого масштаба). Реакционные материалы — именно то
        # применение, для которого экономика реально работает.
        reprocess_node = _try_reprocess_node(
            conn, product_type_id, name, produced, streams, params,
            default_me=default_me, default_te=default_te, max_depth=max_depth, allow_build=allow_build,
            whole_blueprint=whole_blueprint, depth=_depth, chain=_chain, substream_fn=substream_fn,
            force_buy_extra=force_buy_extra, me_overrides=me_overrides, te_overrides=te_overrides,
        )
        if reprocess_node is not None and reprocess_node.unit_cost < normal_node.unit_cost:
            return reprocess_node
    return normal_node


def consolidate_shared_components(
    conn: sqlite3.Connection,
    roots: list[NodeResult],
    params: cost.BuildParams,
    *,
    default_me: int = 0,
    default_te: int = 0,
    max_depth: int = 8,
    allow_build: bool = True,
    whole_blueprint: bool = False,
    substream_fn=None,
    force_buy_extra: frozenset[int] = frozenset(),
    me_overrides: dict[int, int] | None = None,
    te_overrides: dict[int, int] | None = None,
    max_rounds: int = 5,
    auto_streams: bool = False,
) -> list[NodeResult]:
    """Объединить ОДИНАКОВЫЙ строящийся под-компонент, встречающийся в РАЗНЫХ ветках (в том
    числе в РАЗНЫХ товарах корзины — ``roots`` может быть несколько деревьев), в ОДНУ общую
    постройку — чтобы округление (ME на джоб / реакция целыми прогонами) считалось ОДИН раз
    на суммарный спрос, а не независимо в каждой ветке.

    Пример: Photonic Metamaterials нужен и Photon Microprocessor, и Oscillator Capacitor Unit
    (оба — компоненты одного корабля) — без объединения каждая ветка независимо строит и
    округляет СВОЙ Thulium Hafnite, и суммарный «остаток» округления больше, чем при одной
    общей постройке на объединённый спрос.

    Пост-обработка ПОСЛЕ обычного ``build_node_cost`` (сам ``build_node_cost`` ничего не
    объединяет — однопроходное дерево считается как обычно; вызывающая сторона решает,
    консолидировать ли результат). Алгоритм:

    1. Обойти ВСЕ деревья ``roots``, сгруппировать строки-«дети» по (type_id,
       blueprint_type_id, activity_id) — ключ общий для всей корзины, не по дереву отдельно.
    2. Для ключей с 2+ РАЗНЫМИ объектами-детьми (не уже объединённых) — сложить их quantity,
       пересобрать ОДНИМ вызовом ``build_node_cost`` на суммарный run, подставить ОБЩИЙ объект
       во все ссылающиеся строки (пересчитав unit_cost/subtotal каждой строки под её quantity).
    3. Пересчитать material_cost/total_cost/unit_cost снизу вверх по каждому дереву.
    4. Повторить (объединение на одном уровне может вскрыть новые повторы глубже) до
       отсутствия изменений или ``max_rounds``.

    ВАЖНО для вызывающих: после консолидации дерево — уже не строгое дерево, а DAG (несколько
    строк, в том числе из РАЗНЫХ ``roots``, могут указывать на ОДИН и тот же объект NodeResult).
    Любой код, обходящий ``node.lines[].child`` (расписание, отчёт со списанием склада),
    обязан обходить каждый УНИКАЛЬНЫЙ объект (по ``id()``) РОВНО ОДИН раз — иначе задвоит
    джобы/списание склада/стоимость общего под-компонента. См.
    ``planner.schedule.extract_jobs`` и ``web.report.build_report_payload`` — оба уже
    учитывают это (множество посещённых id()).

    ``auto_streams``: объединение материалов само по себе УМЕНЬШАЕТ число джобов (меньше
    округления впустую), но ценой параллелизма — без объединения N независимых веток строятся
    ОДНОВРЕМЕННО на N разных слотах/чарах, с объединением это ОДИН узел, и без явного дробления на
    потоки (``substream_fn``, если задан — имеет приоритет, это явный выбор игрока по сроку)
    он идёт ОДНИМ последовательным джобом, что может УВЕЛИЧИТЬ общий срок постройки (напр.
    Crystalline Carbonide у Ishtar ×5 — без объединения 7 веток идут параллельно по
    разным чарам (4д 1ч), с объединением без auto_streams — одним джобом на одном слоте
    (дольше), хотя материала уходит меньше). При ``auto_streams=True`` (и без явного
    ``substream_fn``) число потоков общей постройки берётся равным числу СЛИТЫХ веток
    (``len(lines)`` — сколько независимых родительских строк на неё ссылалось, столько слотов
    реально было бы занято БЕЗ объединения), с потолком ``MAX_AUTO_STREAMS`` — экономия
    материала сохраняется (дробление на потоки НЕ меняет уже посчитанный суммарный ``runs``,
    только распределяет их по параллельным джобам — round-off всё равно один раз на весь
    объединённый спрос), а параллелизм восстанавливается автоматически, без ручного подбора
    «Макс. дней/поток».
    """
    for _ in range(max_rounds):
        occurrences: dict[tuple[int, int, int], list[MaterialLine]] = {}
        # Узел, УЖЕ объединённый в прошлом раунде (или общий с самого начала), достижим из
        # НЕСКОЛЬКИХ родительских строк сразу — без учёта посещённых _walk() рекурсировал бы в
        # него ПО РАЗУ НА КАЖДУЮ ссылающуюся строку и добавлял бы ЕГО СОБСТВЕННЫЕ строки в
        # occurrences НЕСКОЛЬКО РАЗ (дубли одного и того же объекта MaterialLine в списке) —
        # total_qty при следующем слиянии считался бы С ЗАВЫШЕНИЕМ (напр. 5×Ishtar:
        # Photonic Metamaterials, объединённый в раунде 1, сам содержит строку Crystallite Alloy;
        # эта строка задваивалась бы в occurrences каждый раз, когда Photonic Metamaterials
        # обходится через ВТОРОГО родителя — Crystallite Alloy строился бы на 480 прогонов
        # вместо верных ~86). Посещённые — ТОЛЬКО в рамках ОДНОГО раунда (сбрасываются на
        # следующий — слияние меняет топологию, следующий раунд должен обойти дерево заново).
        visited: set[int] = set()

        # _walk closes over `visited`/`occurrences` from this loop iteration, but is only ever
        # called (via `for root in roots: _walk(root)` below) within THIS same iteration, before
        # the next iteration rebinds them — not the late-binding pattern B023 warns about.
        def _walk(n: NodeResult) -> None:
            if id(n) in visited:  # noqa: B023
                return
            visited.add(id(n))  # noqa: B023
            for ln in n.lines:
                if ln.child is not None:
                    key = (ln.child.product_type_id, ln.child.blueprint_type_id, ln.child.activity_id)
                    occurrences.setdefault(key, []).append(ln)  # noqa: B023
                    _walk(ln.child)

        for root in roots:
            _walk(root)

        merged_any = False
        for (type_id, _bp_id, _act_id), lines in occurrences.items():
            distinct = {id(ln.child): ln.child for ln in lines}
            if len(distinct) < 2:
                continue  # одно вхождение (или уже объединено в прошлом раунде) — нечего сливать
            bp = bom.blueprint_for_product(conn, type_id)
            if bp is None:
                continue
            total_qty = sum(ln.quantity for ln in lines)
            sub_runs = max(1, math.ceil(total_qty / bp.product_qty_per_run))
            if substream_fn:
                sub_streams = substream_fn(type_id, sub_runs)
            elif auto_streams:
                # Столько слотов реально было бы занято БЕЗ объединения — по одному на
                # каждую независимую ссылающуюся ветку (не более MAX_AUTO_STREAMS и не
                # больше самих прогонов — дробить общий джоб мельче, чем есть работы, незачем).
                # Сознательно НЕ капаем по числу владеемых копий формулы реакции (формулы
                # всегда можно докупить, важнее минимальные срок/себестоимость; если копий
                # не хватает — report.py и так покажет
                # предупреждение missing_reaction, это достаточная информация без искусственного
                # занижения параллелизма).
                sub_streams = max(1, min(len(lines), sub_runs, MAX_AUTO_STREAMS))
            else:
                sub_streams = 1
            shared = build_node_cost(
                conn, type_id, sub_runs, sub_streams, params,
                default_me=default_me, default_te=default_te, max_depth=max_depth, allow_build=allow_build,
                whole_blueprint=whole_blueprint, substream_fn=substream_fn,
                force_buy_extra=force_buy_extra, me_overrides=me_overrides, te_overrides=te_overrides,
            )
            if not shared.produced or shared.unit_cost <= 0:
                continue  # пересборка не удалась (напр. пропала цена) — оставить как было
            for ln in lines:
                ln.child = shared
                ln.unit_cost = shared.unit_cost
                ln.subtotal = shared.unit_cost * ln.quantity
                ln.source = "build"
            merged_any = True

        seen: set[int] = set()
        for root in roots:
            _recompute_aggregates(root, seen)
        if not merged_any:
            break
    return roots


def _recompute_aggregates(node: NodeResult, _seen: set[int] | None = None) -> None:
    """Пересчитать material_cost/total_cost/unit_cost снизу вверх после подмены ln.child на
    общий объект. Общий child может встречаться в lines НЕСКОЛЬКИХ родителей — пересчитываем
    каждый УНИКАЛЬНЫЙ узел (по ``id()``) ровно один раз.

    КРИТИЧНО: строка ``ln``, чей ребёнок НЕ был напрямую объединён (сам ``ln.child`` уникален,
    не расшарен), но у ЭТОГО ребёнка глубже внутри дерева слился СВОЙ материал. Слияние (см.
    ``consolidate_shared_components``) вручную обновляет ``ln.unit_cost``/``ln.subtotal``
    ТОЛЬКО у строк, что ссылаются НАПРЯМУЮ на объединяемый узел; если считать material_cost
    узла как простую сумму ЕГО СОБСТВЕННЫХ ``ln.subtotal``, не освежая их под НОВЫЙ (уже
    пересчитанный рекурсией) ``ln.child.unit_cost``, экономия от слияния дойдёт ровно на ОДИН
    уровень вверх от объединённого узла и потеряется дальше — для глубоко вложенных компонентов
    (напр. Crystallite Alloy на 3+ уровня ниже Ishtar) себестоимость ВСЕГО корабля осталась бы
    той же, что и без консолидации вообще, хотя материалов реально уходит меньше. Поэтому после
    рекурсии в ребёнка (когда его unit_cost уже финален) ``ln.unit_cost``/``ln.subtotal``
    строки освежаются под НОВУЮ цену ребёнка, и уже ПОТОМ суммируются.

    Узел-обёртка переработки (``blueprint_source == "reprocess"``) отдельного случая не требует:
    у него ровно одна строка (прекурсор), и обычная сумма ``ln.subtotal`` по строкам даёт то же
    самое, что и его ``material_cost = precursor.subtotal`` (см. ``_try_reprocess_node``)."""
    if _seen is None:
        _seen = set()
    if id(node) in _seen:
        return
    _seen.add(id(node))
    for ln in node.lines:
        if ln.child is not None:
            _recompute_aggregates(ln.child, _seen)
            ln.unit_cost = ln.child.unit_cost
            ln.subtotal = ln.child.unit_cost * ln.quantity
    material_cost = sum(ln.subtotal for ln in node.lines if ln.subtotal is not None)
    node.material_cost = material_cost
    node.total_cost = material_cost + node.job_cost + node.blueprint_cost
    node.unit_cost = node.total_cost / node.produced if node.produced else 0.0


def _buy_only_line(
    conn: sqlite3.Connection, params: cost.BuildParams, type_id: int, quantity: int
) -> MaterialLine:
    """Строка ЧИСТО покупного материала (без выбора строить/купить — сырьё для инвенты вроде
    датакоров/декрипторов чертежей не строится вообще). Тот же выбор хаба (``choose_hub``), что
    и у обычных материалов постройки (``_source_material``), поэтому landed-цена и дефицит
    объёма на хабе считаются единообразно."""
    name = prices.type_name(conn, type_id)
    choice = cost.choose_hub(conn, type_id, params, quantity)
    unit = choice.landed_gplb if choice else None
    return MaterialLine(
        type_id, name, quantity,
        "buy" if unit is not None else "unknown",
        unit, (unit * quantity) if unit is not None else None,
        buy_hub=choice.hub if choice else None,
        buy_shortage=bool(choice and not choice.enough),
    )


def _source_material(
    conn: sqlite3.Connection,
    type_id: int,
    quantity: int,
    params: cost.BuildParams,
    *,
    default_me: int,
    default_te: int = 0,
    max_depth: int,
    allow_build: bool,
    whole_blueprint: bool = False,
    depth: int,
    chain: frozenset[int],
    substream_fn=None,
    force_buy_extra: frozenset[int] = frozenset(),
    me_overrides: dict[int, int] | None = None,
    te_overrides: dict[int, int] | None = None,
) -> MaterialLine:
    """Решить buy vs build для нужного количества материала и вернуть строку."""
    name = prices.type_name(conn, type_id)
    # Хаб закупки выбираем с учётом наличия объёма под требуемое количество:
    # если на дешёвом хабе материала не хватает — берём тот, где хватает.
    buy_choice = cost.choose_hub(conn, type_id, params, quantity)
    buy_unit = buy_choice.landed_gplb if buy_choice else None

    # «Всегда покупать»: по type_id/группе из конфига ИЛИ ручной override игрока
    # (``force_buy_extra`` — «строить→купить» по под-предмету из калькулятора).
    force_buy = (
        type_id in params.always_buy_types
        or type_id in force_buy_extra
        or (params.always_buy_groups and prices.group_id(conn, type_id) in params.always_buy_groups)
    )

    build_node: NodeResult | None = None
    build_unit: float | None = None
    if allow_build and not force_buy and depth < max_depth and type_id not in chain:
        bp = bom.blueprint_for_product(conn, type_id)
        if bp is not None:
            sub_runs = max(1, math.ceil(quantity / bp.product_qty_per_run))
            sub_streams = substream_fn(type_id, sub_runs) if substream_fn else 1
            node = build_node_cost(
                conn, type_id, sub_runs, sub_streams, params,
                default_me=default_me, default_te=default_te, max_depth=max_depth, allow_build=allow_build,
                whole_blueprint=whole_blueprint, substream_fn=substream_fn,
                force_buy_extra=force_buy_extra, me_overrides=me_overrides, te_overrides=te_overrides,
                _depth=depth + 1, _chain=chain,
            )
            if node.produced and node.unit_cost > 0:
                build_unit = node.unit_cost
                build_node = node

    # «Всегда строить» ([always_build_*] конфига): если чертёж есть и постройка посчиталась —
    # строим, даже когда рынок дешевле («Всегда покупать» выше сильнее — туда не доходим).
    force_build = build_unit is not None and bool(
        type_id in params.always_build_types
        or (params.always_build_groups and prices.group_id(conn, type_id) in params.always_build_groups)
    )

    # Выбор источника.
    if buy_unit is None and build_unit is None:
        return MaterialLine(type_id, name, quantity, "unknown", None, None)
    if build_unit is not None and (force_build or buy_unit is None or build_unit < buy_unit):
        return MaterialLine(
            type_id, name, quantity, "build", build_unit, build_unit * quantity, build_node
        )
    assert buy_unit is not None  # both-None and build-cheaper cases already returned above
    return MaterialLine(
        type_id, name, quantity, "buy", buy_unit, buy_unit * quantity,
        buy_hub=buy_choice.hub if buy_choice else None,
        buy_shortage=bool(buy_choice and not buy_choice.enough),
    )
