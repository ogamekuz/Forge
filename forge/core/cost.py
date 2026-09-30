"""Базовые расчёты стоимости (чистые функции, без сети).

Содержит: разбивку прогонов на потоки, количество материала с ME-округлением на КАЖДЫЙ
джоб, EIV и стоимость установки джоба, эффективный фрахт и landed cost покупки материала.
Рекурсию make-or-buy собирает ``sourcing``.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from ..config import FreightRoute
from . import prices
from .bom import REACTION, Material

INVENTION = 8
COPYING = 5

# Группы кораблей, которые уходят прыжком сами → вывоз = топливо за прыжок (фикс),
# а не assembled-объём × ставка ISK/m³ (его как груз не возят).
JUMP_CAPABLE_GROUPS = frozenset({
    902,   # Jump Freighter
    547,   # Carrier
    485,   # Dreadnought
    1538,  # Force Auxiliary
    659,   # Supercarrier
    30,    # Titan
    883,   # Capital Industrial Ship (Rorqual)
    898,   # Black Ops
})


@dataclass
class FacilityMult:
    """Готовые множители станции под конкретную роль джоба.

    ``system_id`` — система станции (индекс стоимости её джобов); 0 — система стройки
    (``BuildParams.gplb_system_id``), как у фолбэка без станции роли — см. ``cost_system``."""

    role: str
    material_mult: float = 1.0
    time_mult: float = 1.0
    cost_mult: float = 1.0
    facility_tax: float = 0.0
    group_ids: frozenset[int] = frozenset()
    category_ids: frozenset[int] = frozenset()
    system_id: int = 0


@dataclass(frozen=True)
class JumpRoute:
    """Сырые параметры рейсового маршрута (fixed_jump) — для расчёта числа рейсов по объёму."""

    fixed_cost: float
    vessel_capacity_m3: float
    load_factor: float


@dataclass
class BuildParams:
    """Параметры площадки/логистики для расчёта (из конфига и facilities)."""

    gplb_system_id: int
    jita_region_id: int
    cj6mt_region_id: int
    rig_material_mult: float = 1.0  # фолбэк-множитель на материалы (если нет станции роли)
    rig_cost_mult: float = 1.0      # фолбэк-множитель на стоимость джоба
    time_mult: float = 1.0          # фолбэк-множитель на время
    facility_tax: float = 0.0
    scc_surcharge: float = 0.0
    # налог/брокер продажи в C-J6MT — те же значения, что и в profit.compute_profit, продублированы
    # сюда (не только в вызовах compute_profit), чтобы cost.unit_sell_value могла честно оценить
    # выручку с побочки переработки без протаскивания их отдельными параметрами через весь sourcing.
    broker_fee: float = 0.0
    sales_tax: float = 0.0
    # эффективный фрахт ISK/m³ по ключу (откуда, куда) — используется для per_m3-маршрутов
    # (fixed_jump тоже тут есть как фолбэк, но реально считается через jump_routes ниже)
    freight: dict[tuple[str, str], float] = field(default_factory=dict)
    # минимальная стоимость доставки за заказ (только per_m3) — пол под object×freight
    min_cost: dict[tuple[str, str], float] = field(default_factory=dict)
    # сырой fixed_cost маршрутов-прыжков (вывоз самоходных капиталов = топливо за прыжок,
    # без учёта рейсов — капитал не грузится в трюм, летит сам)
    jump_fuel: dict[tuple[str, str], float] = field(default_factory=dict)
    # сырые параметры маршрутов-прыжков (для расчёта числа рейсов по объёму партии)
    jump_routes: dict[tuple[str, str], JumpRoute] = field(default_factory=dict)
    # ручная стоимость чертежа (ISK на 1 run продукта) по product_type_id
    blueprint_overrides: dict[int, float] = field(default_factory=dict)
    # станции по ролям (реакции/инвента/компоненты/производство)
    facilities: list[FacilityMult] = field(default_factory=list)
    # «всегда покупать, не строить» — группы SDE и конкретные type_id
    always_buy_groups: frozenset[int] = frozenset()
    always_buy_types: frozenset[int] = frozenset()
    # «всегда строить, не покупать» (если есть чертёж), даже когда рынок дешевле
    always_build_groups: frozenset[int] = frozenset()
    always_build_types: frozenset[int] = frozenset()
    # разрешённые хабы закупки ('jita' — Jita + доставка, 'cj' — C-J6MT); пусто — оба
    buy_hubs: frozenset[str] = frozenset({"jita", "cj"})
    # чем оценивать выручку в месте сбыта: 'sell_min' (выставить ордер) | 'buy_max' (продать сразу)
    sell_price: str = "sell_min"
    # группы кораблей, которые при вывозе летят сами (топливо за прыжок, а не объём × ставка)
    jump_capable_groups: frozenset[int] = JUMP_CAPABLE_GROUPS
    # где искать чертежи (location_id); пусто = везде
    blueprint_location_ids: frozenset[int] = frozenset()
    # опция: доля 0..1 эффективности переработки; 0.0 = не рассматривать переработку прекурсора
    # как альтернативный источник материала вообще (см. core/reprocess.py).
    reprocessing_efficiency: float = 0.0
    # Шанс инвенты со скиллами ([industry] invention_use_skills, см. blueprint.invention_skills):
    # кандидаты-инвенторы (пул «наука») и их уровни тех скиллов, которые вообще требует какая-либо
    # инвента в SDE — {character_id: {skill_type_id: level}} (кандидат без таких скиллов — с
    # пустым словарём), и имена для подписи «лучший инвентор». Выключено или кандидатов нет —
    # множитель 1.0, базовый шанс SDE.
    invention_use_skills: bool = False
    inventor_skills: dict[int, dict[int, int]] = field(default_factory=dict)
    inventor_names: dict[int, str] = field(default_factory=dict)
    # Кэш множителя скиллов по T2-чертежу (не конфигурация — как reprocess_cache ниже).
    invention_skill_cache: dict = field(default_factory=dict, repr=False, compare=False)
    # Выбор декриптора ([invention], см. blueprint.choose_decryptor): режим 'auto_cost' | 'none' |
    # 'fixed', декриптор режима fixed (0 — без), разрешённые для авто-выбора (пусто — все) и
    # оверрайды по T2-продукту {product_type_id: decryptor_type_id (0 — без декриптора)}.
    decryptor_mode: str = "auto_cost"
    decryptor_fixed: int = 0
    decryptor_allowed: frozenset[int] = frozenset()
    decryptor_per_product: dict[int, int] = field(default_factory=dict)
    # Внутренний кэш результатов _try_reprocess_node по (product_type_id, target_quantity) —
    # НЕ часть конфигурации (repr/compare выключены). Живёт РОВНО столько же, сколько сам
    # params (создаётся заново на каждый верхнеуровневый расчёт через build_params_from_config —
    # см. web/service.py::ForgeService.ctx()), поэтому не переживает между запросами и не может протухнуть.
    # Нужен: без него один и тот же материал, встречающийся в дереве заказа НЕ дедуплицированно
    # (дедуп — отдельным проходом ПОСЛЕ, в consolidate_shared_components) СОТНИ-ТЫСЯЧИ раз (напр.
    # Nomad — капитальный тираж T2-компонентов), пересчитывался бы заново на каждое вхождение —
    # замер: 0.03с без опции → 60+с (не завершается) с опцией без кэша. Единственное
    # ОБОСНОВАННОЕ допущение: игнорирует ``chain`` (защиту от циклов) — безопасно, т.к. реальные
    # цепочки переработки EVE ацикличны (CCP не допустит X→Y→X, это была бы вечная ISK-помпа).
    reprocess_cache: dict = field(default_factory=dict, repr=False, compare=False)


def facility_mults(
    params: BuildParams,
    activity_id: int,
    product_group_id: int | None,
    product_category_id: int | None = None,
) -> FacilityMult:
    """Выбрать станцию (множители) под джоб: по активности, а внутри роли — по группе/категории
    продукта.

    Роль 'component' — отдельный, самостоятельный механизм роутинга ВНУТРИ производства
    (не просто фильтр по группе на роли "manufacturing"): проверяется первой, до основной роли.
    Дальше — фильтр по группе ИЛИ категории уже для ОСНОВНОЙ роли (нужно, например, для
    reaction: риг под Composite не должен применяться к Hybrid и наоборот, group_id 429 vs 428;
    для manufacturing — риги Engineering Complex XL/L даются ПО КАТЕГОРИИ продукта, напр.
    Standup XL-Set Ship Manufacturing Efficiency действует ТОЛЬКО на Ships (category_id=6), а не
    на модули/структуры/компоненты — см. ``Facility.category_ids``). Facility матчит роль, если
    group_id ИЛИ category_id продукта попадает в её списки (любой из двух). Catch-all (facility
    без указанных групп/категорий) подходит, только если ОБА списка пусты — иначе facility,
    заточенная под конкретную группу/категорию, могла бы случайно проглотить чужой продукт,
    просто оказавшись в списке первой. Нет подходящей станции → фолбэк из единых множителей
    BuildParams.
    """
    def _scoped(f: FacilityMult) -> bool:
        return (
            bool(f.group_ids) and product_group_id is not None and product_group_id in f.group_ids
        ) or (
            bool(f.category_ids) and product_category_id is not None
            and product_category_id in f.category_ids
        )

    if activity_id == REACTION:
        role = "reaction"
    elif activity_id == INVENTION:
        role = "invention"
    elif activity_id == COPYING:
        role = "copy"
    else:  # MANUFACTURING и прочее производство
        role = "manufacturing"
        for f in params.facilities:
            if f.role == "component" and _scoped(f):
                return f

    for f in params.facilities:
        if f.role == role and _scoped(f):
            return f
    for f in params.facilities:
        if f.role == role and not f.group_ids and not f.category_ids:
            return f
    return FacilityMult(
        role=role,
        material_mult=params.rig_material_mult,
        time_mult=params.time_mult,
        cost_mult=params.rig_cost_mult,
        facility_tax=params.facility_tax,
    )


def cost_system(params: BuildParams, fm: FacilityMult) -> int:
    """Система, по индексу стоимости которой считается джоб станции ``fm``: своя система
    станции (``Facility.system_id``/авто из структуры), 0 или нет станции роли — система стройки.
    Только индекс и security ригов: логистика между системами не моделируется — материалы
    считаются доставленными в систему стройки, где бы ни стояла станция."""
    return fm.system_id or params.gplb_system_id


def effective_isk_per_m3(route: FreightRoute) -> float:
    """Привести маршрут к ISK/m³. fixed_jump нормализуется по ёмкости и загрузке."""
    if route.mode == "per_m3":
        return route.isk_per_m3
    denom = route.vessel_capacity_m3 * route.load_factor
    return route.fixed_cost / denom if denom > 0 else 0.0


def build_freight_map(routes: list[FreightRoute]) -> dict[tuple[str, str], float]:
    return {(r.from_, r.to): effective_isk_per_m3(r) for r in routes}


def jump_freight_cost(route: JumpRoute, total_volume: float) -> float:
    """Стоимость доставки ``total_volume`` м³ маршрутом-прыжком (не per_m3).

    Первая партия — один прыжок (``fixed_cost``). Если объём партии превышает вместимость
    судна (``vessel_capacity_m3 × load_factor``), на каждую ДОПОЛНИТЕЛЬНУЮ партию нужно ещё
    два прыжка (порожняком обратно за грузом + снова с грузом): N партий ⇒ (2N − 1) прыжков.
    """
    if total_volume <= 0:
        return 0.0
    capacity = route.vessel_capacity_m3 * route.load_factor
    trips = max(1, math.ceil(total_volume / capacity)) if capacity > 0 else 1
    return route.fixed_cost * (2 * trips - 1)


def route_freight_cost(params: BuildParams, from_: str, to: str, total_volume: float) -> float:
    """Стоимость доставки ``total_volume`` м³ по маршруту ``from_→to``.

    Рейсовый маршрут (``jump_routes``) — ступенчато по числу партий (см. ``jump_freight_cost``);
    иначе — линейно по эффективной ставке ISK/m³ (``freight``, обычный груз-карго), но не
    меньше минимума за доставку (``min_cost``), если он задан для этого плеча.
    """
    jr = params.jump_routes.get((from_, to))
    if jr is not None:
        return jump_freight_cost(jr, total_volume)
    if total_volume <= 0:
        return 0.0
    linear = total_volume * params.freight.get((from_, to), 0.0)
    return max(linear, params.min_cost.get((from_, to), 0.0))


def split_runs(total_runs: int, streams: int) -> list[int]:
    """Разбить total_runs на streams параллельных джобов как можно равномернее."""
    streams = max(streams, 1)
    base, rem = divmod(total_runs, streams)
    counts = [base + (1 if i < rem else 0) for i in range(streams)]
    return [c for c in counts if c > 0]


def material_quantity(
    base_quantity: int,
    runs_per_job: list[int],
    me: int,
    rig_material_mult: float,
    activity_id: int,
) -> int:
    """Суммарное количество материала по джобам. ME-округление (ceil) на КАЖДЫЙ джоб —
    поэтому больше потоков ⇒ обычно больше материалов. У реакций ME игнорируется.

    Как в EVE: модификаторы (ME × структура/риги) перемножаются, результат округляется
    до 2 знаков и только потом ceil. Без round(…, 2) плавающая запятая даёт off-by-one
    вверх (напр. 18.00117 → ceil 19 вместо 18)."""
    me_factor = 1.0 if activity_id == REACTION else (1.0 - me / 100.0)
    factor = me_factor * rig_material_mult
    return sum(max(r, math.ceil(round(base_quantity * r * factor, 2))) for r in runs_per_job)


def eiv(conn: sqlite3.Connection, materials: list[Material], total_runs: int) -> float:
    """Estimated Item Value джоба = Σ adjusted_price × base_qty × runs (база для стоимости)."""
    total = 0.0
    for m in materials:
        ap = prices.adjusted_price(conn, m.type_id)
        if ap:
            total += ap * m.base_quantity * total_runs
    return total


def job_install_cost(
    eiv_value: float,
    cost_index: float,
    rig_cost_mult: float,
    facility_tax: float,
    scc_surcharge: float,
) -> float:
    """Стоимость установки джоба: подтверждено разбивкой из клиента EVE (Job Gross Cost /
    Taxes) — facility tax и SCC surcharge берутся от EIV НАПРЯМУЮ и СКЛАДЫВАЮТСЯ с (EIV ×
    cost_index × риг-бонус), а не умножают итог джоба как множитель (1+tax+scc). Реальный
    пример (Crystalline Carbonide, EIV=61 298 963, cost_index=8%, tax=1%, scc=4%):
    Job Gross Cost = EIV×0.08 = 4 903 879; Facility tax = EIV×0.01 = 612 990; SCC = EIV×0.04 =
    2 451 959; Total = 7 968 828 — ТОЧНО EIV×(cost_index+tax+scc), не EIV×cost_index×(1+tax+scc)."""
    gross = eiv_value * cost_index * rig_cost_mult
    taxes = eiv_value * (facility_tax + scc_surcharge)
    return gross + taxes


@dataclass
class HubOption:
    """Вариант закупки материала на одном хабе."""

    hub: str               # 'jita' | 'cj'
    unit_at_cj: float      # цена за единицу, доставленная до C-J6MT (для отображения)
    landed_gplb: float     # себестоимость единицы, доставленной до GPLB-C (для сорсинга)
    available: int | None  # доступный объём (sell_volume) на хабе
    enough: bool           # хватает ли объёма на требуемое количество


def _hub_options(
    conn: sqlite3.Connection, type_id: int, params: BuildParams, quantity: int | None
) -> list[HubOption]:
    """Доступные хабы закупки с ценами и проверкой достаточности объёма.

    ``quantity`` — требуемое количество. Если задано, ``enough`` показывает, покрывает ли
    sell_volume хаба эту потребность. При quantity=None достаточность не проверяется.

    Фрахт — простое умножение объёма на ставку ISK/m³ (``params.freight``). Для fixed_jump-
    маршрутов эта ставка НЕ статична из конфига, а батчево пересчитана заранее под весь заказ
    (см. ``core.batched_freight_params``/``estimate_basket``) — здесь про рейсы ничего не знаем
    сознательно: если считать рейсы на КАЖДЫЙ материал отдельно, у заказа из N разных материалов
    каждый платит свой «минимум один рейс» — абсурдно завышает фрахт (напр. 23 материала ×
    5.5М = 126М вместо реальных ~5М на один общий рейс). Правильно — считать рейсы РАЗ на
    суммарный объём всего заказа, это и делает батч-слой выше по стеку.
    """
    vol = prices.volume(conn, type_id)
    f_cj_gplb = params.freight.get(("c_j6mt", "gplb_c"), 0.0)
    f_jita_cj = params.freight.get(("jita", "c_j6mt"), 0.0)
    allowed = params.buy_hubs or frozenset({"jita", "cj"})  # пусто в конфиге — оба хаба

    def enough_for(avail: int | None) -> bool:
        return quantity is None or (avail is not None and avail >= quantity)

    options: list[HubOption] = []
    cj_price, cj_vol = prices.sell(conn, type_id, params.cj6mt_region_id)
    if cj_price is not None and "cj" in allowed:
        options.append(HubOption(
            "cj", cj_price, cj_price + vol * f_cj_gplb, cj_vol, enough_for(cj_vol)
        ))
    jita_price, jita_vol = prices.sell(conn, type_id, params.jita_region_id)
    if jita_price is not None and "jita" in allowed:
        options.append(HubOption(
            "jita", jita_price + vol * f_jita_cj,
            jita_price + vol * (f_jita_cj + f_cj_gplb), jita_vol, enough_for(jita_vol)
        ))
    return options


def choose_hub(
    conn: sqlite3.Connection, type_id: int, params: BuildParams, quantity: int | None = None
) -> HubOption | None:
    """Выбрать хаб закупки: самый дешёвый из тех, где ХВАТАЕТ объёма на ``quantity``.

    Если ни на одном хабе объёма не хватает — берём самый дешёвый из доступных
    (``enough=False`` сигналит о дефиците). None, если нигде нет sell-цены.
    """
    options = _hub_options(conn, type_id, params, quantity)
    if not options:
        return None
    sufficient = [o for o in options if o.enough]
    pool = sufficient if sufficient else options
    return min(pool, key=lambda o: o.landed_gplb)


def hub_unit_prices(
    conn: sqlite3.Connection, type_id: int, params: BuildParams, quantity: int | None = None
) -> dict:
    """Цены за единицу по хабам + какой хаб выбран с учётом наличия объёма:
    - ``jita_to_cj``: цена в Jita + доставка Jita→C-J6MT;
    - ``cj_local``: цена прямо в C-J6MT;
    - ``chosen``: 'jita' | 'cj' | None — выбранный для закупки хаб;
    - ``*_enough`` / ``*_available``: хватает ли объёма и сколько его на каждом хабе.
    """
    by_hub = {o.hub: o for o in _hub_options(conn, type_id, params, quantity)}
    jita = by_hub.get("jita")
    cj = by_hub.get("cj")
    chosen = choose_hub(conn, type_id, params, quantity)
    return {
        "jita_to_cj": jita.unit_at_cj if jita else None,
        "cj_local": cj.unit_at_cj if cj else None,
        "chosen": chosen.hub if chosen else None,
        "jita_enough": jita.enough if jita else None,
        "cj_enough": cj.enough if cj else None,
        "jita_available": jita.available if jita else None,
        "cj_available": cj.available if cj else None,
    }


def landed_unit_cost(
    conn: sqlite3.Connection, type_id: int, params: BuildParams, quantity: int | None = None
) -> float | None:
    """Себестоимость доставки 1 единицы материала в GPLB-C (покупкой).

    Берёт хаб, выбранный ``choose_hub`` (дешёвый с достаточным объёмом под ``quantity``),
    а не просто абсолютный минимум цены. None, если нигде нет sell-цены.
    """
    chosen = choose_hub(conn, type_id, params, quantity)
    return chosen.landed_gplb if chosen else None


def unit_sell_value(
    conn: sqlite3.Connection,
    type_id: int,
    params: BuildParams,
    sell_region_id: int,
    broker_fee: float,
    sales_tax: float,
) -> float | None:
    """Реалистичная выручка с продажи 1 единицы в месте сбыта (C-J6MT) — та же формула, что
    ``profit.compute_profit`` для готового продукта («Выручка = цена в C-J6MT − налог и брокер
    структуры − вывоз(GPLB-C→C-J6MT)», см. docs/ARCHITECTURE.md), вынесенная сюда как переиспользуемая
    функция ЦЕНЫ ПРОДАЖИ (без знания о ``NodeResult``/дереве постройки — чтобы применять её и к
    сырью/побочке переработки из ``core/sourcing.py`` без цикла импорта sourcing↔profit).

    ``profit.py`` НЕ переиспользует эту функцию напрямую (риск регресса в откалиброванной
    ``compute_profit`` не стоит маленького дублирования ~10 строк — см. docs/ARCHITECTURE.md о минимализме).
    None, если нет sell-цены в месте сбыта."""
    price = prices.sale_price(conn, type_id, sell_region_id, params.sell_price)
    if price is None:
        return None
    gid = prices.group_id(conn, type_id)
    jump = params.jump_fuel.get(("gplb_c", "c_j6mt"))
    if gid in params.jump_capable_groups and jump is not None:
        export = jump
    else:
        export = prices.volume(conn, type_id) * params.freight.get(("gplb_c", "c_j6mt"), 0.0)
    return price * (1.0 - broker_fee - sales_tax) - export
