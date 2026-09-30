"""core — чистые локальные расчёты: bom, sourcing, cost, profit.

Читает данные через ``storage``/``prices``, сети не делает (правило 1).
Верхняя точка входа — ``estimate_build``: BoM + make-or-buy + стоимость джоба + прибыль.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from dataclasses import dataclass

from ..config import Config, Facility
from . import blueprint, bom, cost, locations, prices, profit, rigs, sourcing, stock
from .profit import ProfitResult
from .sourcing import NodeResult

__all__ = [
    "BuildEstimate",
    "batched_freight_params",
    "blueprint",
    "bom",
    "build_params_from_config",
    "cost",
    "estimate_basket",
    "estimate_build",
    "facility_system",
    "locations",
    "prices",
    "profit",
    "rigs",
    "science_character_ids",
    "sell_region_id",
    "sourcing",
    "stock",
]


def sell_region_id(params: cost.BuildParams) -> int:
    """Регион места сбыта: C-J6MT, а если он не резолвлен — Jita."""
    return params.cj6mt_region_id or params.jita_region_id


def facility_system(
    f: Facility, resolver: locations.LocationResolver | None, build_system_id: int
) -> tuple[int, str]:
    """Система станции ``f`` и откуда она известна: ``(system_id, how)``.

    ``how``: ``'manual'`` — задана в настройке (``Facility.system_id``); ``'structure'``/
    ``'station'``/``'system'`` — по ``location_id`` (ESI ``universe_structures`` / NPC-станция SDE
    / сама система); ``'build'`` — не удалось узнать (структура ещё не резолвлена синком
    персонажей или ``location_id`` не задан) — система стройки."""
    if f.system_id:
        return int(f.system_id), "manual"
    if f.location_id and resolver is not None:
        sid, how = resolver.system_of(int(f.location_id))
        if sid and how in ("structure", "station", "system"):
            return sid, how
    return build_system_id, "build"


def science_character_ids(cfg: Config, conn: sqlite3.Connection) -> list[int]:
    """Кто может вести научные джобы (инвента/копирование — пул «наука»): персонажи БД из
    ``science_character_ids``; пусто — из ``manufacturing_character_ids``; ни одного
    списка ролей — все. Та же логика, что ``role_allowed`` в ``planner.schedule.schedule_jobs``
    (core не импортирует planner — правило слоёв docs/ARCHITECTURE.md, поэтому продублировано: держать
    синхронным при правке). Персонаж с лимитом слотов науки 0 (``[planner] slot_limits``) в пуле
    не участвует — и здесь не кандидат."""
    mfg, rx, sci = (set(cfg.manufacturing_character_ids), set(cfg.reaction_character_ids),
                    set(cfg.science_character_ids))
    ids = [int(r["character_id"]) for r in conn.execute("SELECT character_id FROM characters")]
    ids = [c for c in ids if cfg.planner.slot_limit(c, "science") != 0]
    if not (mfg or rx or sci):
        return ids
    pool = sci or mfg
    return [c for c in ids if c in pool]


def _inventor_skills(
    cfg: Config, conn: sqlite3.Connection
) -> tuple[dict[int, dict[int, int]], dict[int, str]]:
    """Уровни «инвент-скиллов» (всех, что требует хоть какая-то инвента в SDE: Encryption
    Methods + науки) у кандидатов-инвенторов и их имена — для ``blueprint.invention_skills``.
    Кандидат без единого такого скилла всё равно в словаре (с пустыми уровнями): он есть в
    пуле, просто его множитель 1.0."""
    cands = science_character_ids(cfg, conn)
    if not cands:
        return {}, {}
    ph = ",".join("?" for _ in cands)
    names = {int(r["character_id"]): r["name"] or str(r["character_id"]) for r in conn.execute(
        f"SELECT character_id, name FROM characters WHERE character_id IN ({ph})", cands)}
    skills: dict[int, dict[int, int]] = {c: {} for c in cands}
    for r in conn.execute(
        f"SELECT character_id, skill_type_id, active_level FROM character_skills "
        f"WHERE character_id IN ({ph}) AND skill_type_id IN "
        f"(SELECT DISTINCT skill_type_id FROM sde_blueprint_skills WHERE activity_id = ?)",
        (*cands, blueprint.INVENTION),
    ):
        skills[int(r["character_id"])][int(r["skill_type_id"])] = int(r["active_level"] or 0)
    return skills, names


@dataclass
class BuildEstimate:
    node: NodeResult
    profit: ProfitResult


def build_params_from_config(
    cfg: Config, conn: sqlite3.Connection | None = None
) -> cost.BuildParams:
    """Собрать параметры расчёта из конфига (локации должны быть уже резолвнуты из SDE).

    ``conn`` — опционален; если передан И у facility непуст ``fitted_type_ids``, материал/
    время считаются из реальных фитованных ригов (``core.rigs``, SDE dogma + стэкинг-пенальти
    EVE), а не из ручных material_bonus_pct/time_bonus_pct. Без ``conn`` (или для facility без
    фита) — ручные проценты как есть. С ``conn`` и ``[industry]
    invention_use_skills`` — ещё и скиллы кандидатов-инвенторов (шанс инвенты, см.
    ``blueprint.invention_skills``); без ``conn`` шанс инвенты — базовый из SDE."""
    gplb = cfg.locations.get("gplb_c")
    jita = cfg.locations.get("jita")
    cj = cfg.locations.get("c_j6mt")

    use_skills = bool(cfg.industry.invention_use_skills and conn is not None)
    inventor_skills: dict[int, dict[int, int]] = {}
    inventor_names: dict[int, str] = {}
    if use_skills and conn is not None:
        inventor_skills, inventor_names = _inventor_skills(cfg, conn)

    # Система КАЖДОЙ станции (facility_system: своя system_id → система структуры location_id →
    # система стройки) — по ней индекс стоимости её джобов (cost.cost_system) и security-
    # модификатор бонусов ригов: риги реакций/переработки и инженерные дают БОЛЬШЕ базового бонуса
    # в low/null-sec/WH (см. forge/core/rigs.py). Логистика между системами не моделируется —
    # материалы считаются доставленными в систему стройки.
    build_system = gplb.system_id if gplb else 0
    resolver = locations.LocationResolver(conn, cfg) if conn is not None else None
    sec_cache: dict[int, float | None] = {}

    def _security(system_id: int) -> float | None:
        if conn is None or not system_id:
            return None
        if system_id not in sec_cache:
            row = conn.execute(
                "SELECT security FROM sde_systems WHERE system_id = ?", (system_id,)
            ).fetchone()
            sec_cache[system_id] = row["security"] if row else None
        return sec_cache[system_id]

    systems = {id(f): facility_system(f, resolver, build_system)[0] for f in cfg.facilities}

    def _mat_time_pct(f: Facility) -> tuple[float, float]:
        # Считать из рига/структуры, если фитован риг ИЛИ указан тип структуры (структурный
        # бонус применяется даже без ригов — напр. Tatara даёт -25% времени реакции сама по себе).
        if conn is not None and (f.fitted_type_ids or f.structure_type_id):
            sec = _security(systems[id(f)])
            return (
                rigs.facility_bonus_pct(
                    conn, f.fitted_type_ids, f.role, "material", sec, f.structure_type_id
                ),
                rigs.facility_bonus_pct(
                    conn, f.fitted_type_ids, f.role, "time", sec, f.structure_type_id
                ),
            )
        return f.material_bonus_pct, f.time_bonus_pct

    def _cost_mult(f: Facility) -> float:
        # Инженерные риги МОГУТ давать cost-бонус (attributeEngRigCostBonus, напр. «Standup
        # M-Set Invention Cost Optimization II» = -12% × nullSecModifier) — считаем его наравне
        # с material/time, плюс встроенный бонус структуры (strEngCostBonus у Engineering
        # Complex). Если фитован риг ИЛИ указана структура — считаем из них (ручной
        # cost_bonus_pct игнорируется, тот же фолбэк-паттерн, что и для material/time).
        if conn is not None and (f.fitted_type_ids or f.structure_type_id):
            cost_pct = rigs.facility_bonus_pct(
                conn, f.fitted_type_ids, f.role, "cost", _security(systems[id(f)]), f.structure_type_id
            )
            return 1.0 - cost_pct / 100.0
        return 1.0 - f.cost_bonus_pct / 100.0

    return cost.BuildParams(
        gplb_system_id=gplb.system_id if gplb else 0,
        jita_region_id=jita.region_id if jita else 10000002,
        cj6mt_region_id=cj.region_id if cj else 0,
        rig_material_mult=cfg.industry.rig_material_mult,
        rig_cost_mult=cfg.industry.rig_cost_mult,
        time_mult=cfg.industry.time_mult,
        facility_tax=cfg.industry.facility_tax,
        scc_surcharge=cfg.industry.scc_surcharge,
        broker_fee=cfg.industry.broker_fee,
        sales_tax=cfg.industry.sales_tax,
        freight=cost.build_freight_map(cfg.freight_routes),
        min_cost={(r.from_, r.to): r.min_cost for r in cfg.freight_routes if r.min_cost > 0},
        jump_fuel={(r.from_, r.to): r.fixed_cost
                   for r in cfg.freight_routes if r.mode == "fixed_jump"},
        jump_routes={(r.from_, r.to): cost.JumpRoute(r.fixed_cost, r.vessel_capacity_m3, r.load_factor)
                     for r in cfg.freight_routes if r.mode == "fixed_jump"},
        blueprint_overrides={o.type_id: o.per_run for o in cfg.blueprint_overrides},
        facilities=[
            cost.FacilityMult(
                role=f.role,
                material_mult=1.0 - mat_pct / 100.0,
                time_mult=1.0 - time_pct / 100.0,
                cost_mult=_cost_mult(f),
                facility_tax=f.tax_pct / 100.0,
                group_ids=frozenset(f.group_ids),
                category_ids=frozenset(f.category_ids),
                system_id=systems[id(f)],
            )
            for f, (mat_pct, time_pct) in ((f, _mat_time_pct(f)) for f in cfg.facilities)
        ],
        always_buy_groups=frozenset(cfg.always_buy_groups),
        always_buy_types=frozenset(cfg.always_buy_types),
        always_build_groups=frozenset(cfg.always_build_groups),
        always_build_types=frozenset(cfg.always_build_types),
        buy_hubs=frozenset(h for h in cfg.market.buy_hubs if h in ("jita", "cj")) or frozenset({"jita", "cj"}),
        sell_price=cfg.market.sell_price if cfg.market.sell_price in ("sell_min", "buy_max") else "sell_min",
        jump_capable_groups=frozenset(cfg.jump_capable_groups),
        # Чертежи в контейнерах внутри выбранных локаций — тоже «свои» (см. expand_with_containers).
        blueprint_location_ids=locations.expand_with_containers(conn, cfg.blueprint_location_ids),
        reprocessing_efficiency=cfg.industry.reprocessing_efficiency,
        invention_use_skills=use_skills,
        inventor_skills=inventor_skills,
        inventor_names=inventor_names,
        decryptor_mode=(cfg.invention.decryptor_mode
                        if cfg.invention.decryptor_mode in ("auto_cost", "none", "fixed") else "auto_cost"),
        decryptor_fixed=int(cfg.invention.decryptor_type_id or 0),
        decryptor_allowed=frozenset(int(x) for x in cfg.invention.allowed_decryptors),
        decryptor_per_product={int(o.type_id): int(o.decryptor_type_id or 0)
                               for o in cfg.invention.per_product},
    )


def estimate_build(
    conn: sqlite3.Connection,
    cfg: Config,
    product_type_id: int,
    runs: int = 1,
    streams: int = 1,
    *,
    default_me: int = 0,
    default_te: int = 0,
    allow_build: bool = True,
    max_depth: int = 8,
    whole_blueprint: bool = False,
    substream_fn=None,
    force_buy_extra: frozenset[int] = frozenset(),
    me_overrides: dict[int, int] | None = None,
    te_overrides: dict[int, int] | None = None,
    params: cost.BuildParams | None = None,
) -> BuildEstimate:
    """Полная оценка постройки продукта: себестоимость и прибыль.

    ME берётся из реально имеющихся чертежей персонажей; если их нет — ``default_me``. Срок
    (TE) для планировщика аналогично — из владения/инвенты, а если ни того ни другого —
    ``default_te`` (см. ``sourcing.NodeResult.resulting_te``).
    ``allow_build`` управляет рекурсивным make-or-buy компонентов. ``whole_blueprint``
    не влияет на инвенту (та ВСЕГДА считается целыми попытками — нельзя провести дробную
    попытку, см. ``sourcing.build_node_cost``); параметр — для прочих активностей,
    если появится похожая амортизация. ``substream_fn`` — разбивка ПОД-компонентов (напр. под
    срок «≤ N дней/поток»); верхний
    продукт дробится по ``streams``. ``me_overrides``/``te_overrides`` — принудительные ME/TE
    по конкретным ``product_type_id`` (см. ``sourcing.build_node_cost``), для «Точный ME/TE»
    в корзине и для сравнения себестоимости по уровням исследования BPO (``/api/compare-me``).
    ``params`` — готовые параметры вместо пересборки из ``cfg`` (нужно для батч-расчёта фрахта
    на весь заказ, см. ``estimate_basket``); по умолчанию собираются из конфига —
    ОДИНОЧНЫЙ вызов этой функции НЕ батчит фрахт по рейсовым маршрутам сам по себе (см.
    предупреждение в ``batched_freight_params``).
    """
    params = params or build_params_from_config(cfg, conn)
    node = sourcing.build_node_cost(
        conn, product_type_id, runs, streams, params,
        default_me=default_me, default_te=default_te, max_depth=max_depth, allow_build=allow_build,
        whole_blueprint=whole_blueprint, substream_fn=substream_fn, force_buy_extra=force_buy_extra,
        me_overrides=me_overrides, te_overrides=te_overrides,
    )
    sell_region = params.cj6mt_region_id or params.jita_region_id
    pr = profit.compute_profit(
        conn, node, params, sell_region,
        broker_fee=cfg.industry.broker_fee, sales_tax=cfg.industry.sales_tax,
    )
    return BuildEstimate(node=node, profit=pr)


def batched_freight_params(
    conn: sqlite3.Connection, params: cost.BuildParams, nodes: list[NodeResult]
) -> cost.BuildParams:
    """Пересчитать эффективные ставки ISK/m³ плечей с рейсовой (fixed_jump) или минимальной
    (per_m3 + ``min_cost``) логикой под СУММАРНЫЙ объём всех ``nodes`` разом — один
    комбинированный груз на заказ, а не рейс/минимум на каждый материал по отдельности.

    Считать это на КАЖДЫЙ материал/продукт по отдельности абсурдно завышает фрахт: заказ
    из 20 разных материалов заплатил бы «минимум один рейс» (или «минимум за доставку») ×20,
    хотя реально они едут одной фурой. Здесь — суммарный объём по каждому плечу со всех
    переданных деревьев (материалы на закупку + вывоз не-капитальных продуктов), реальная
    стоимость доставки на ЭТОТ объём (``cost.route_freight_cost``), и обратно в эффективную
    ISK/m³ ДЛЯ ЭТОГО ЗАКАЗА. Возвращает новый ``BuildParams`` с этими ставками в ``freight``
    (остальные поля — как у ``params``). Плечи без ``jump_routes``/``min_cost`` не трогает —
    там и так простая линейная ставка, батчить нечего.
    """
    vol_by_leg: dict[tuple[str, str], float] = {}
    for n in nodes:
        for leg, v in sourcing.inbound_volume_by_leg(conn, n).items():
            vol_by_leg[leg] = vol_by_leg.get(leg, 0.0) + v
        gid = prices.group_id(conn, n.product_type_id)
        if gid not in params.jump_capable_groups:
            leg = ("gplb_c", "c_j6mt")
            vol_by_leg[leg] = vol_by_leg.get(leg, 0.0) + (prices.volume(conn, n.product_type_id) or 0.0) * n.produced

    new_freight = dict(params.freight)
    for leg, v in vol_by_leg.items():
        if v <= 0:
            continue
        if leg in params.jump_routes or leg in params.min_cost:
            new_freight[leg] = cost.route_freight_cost(params, leg[0], leg[1], v) / v
    return dataclasses.replace(params, freight=new_freight)


def estimate_basket(
    conn: sqlite3.Connection,
    cfg: Config,
    products: list[tuple[int, int, int]],
    **kwargs,
) -> tuple[list[BuildEstimate], cost.BuildParams]:
    """Оценка списка продуктов (может быть один) с фрахтом, посчитанным ОДНИМ рейсовым
    расчётом на ВЕСЬ список — как один комбинированный груз, а не рейс на каждый материал.

    Двухпроходно: 1) черновой расчёт по обычным (одиночным) ставкам — нужен только чтобы
    узнать объём по каждому материалу и выбранный хаб; 2) суммарный объём по плечам со ВСЕХ
    продуктов → батч-ставка (``batched_freight_params``) → финальный пересчёт с ней. ``kwargs``
    пробрасываются в ``estimate_build`` (default_me/allow_build/substream_fn/force_buy_extra/…).
    Это точка входа для ВСЕХ пользовательских запросов (Калькулятор/Расписание/Отчёт) —
    рекурсивные make-or-buy сравнения внутри ``core`` батч не делают (там просто estimate_build).

    Возвращает ``(оценки, финальные params)`` — params ОБЯЗАТЕЛЬНО переиспользовать для ЛЮБОГО
    дальнейшего расчёта по этим деревьям (напр. частичное покрытие складом в отчёте), иначе
    получится рассинхрон: материалы посчитаны по батчевой ставке, а что-то ещё — по дефолтной.
    """
    params0 = build_params_from_config(cfg, conn)
    draft = [estimate_build(conn, cfg, tid, runs=r, streams=s, params=params0, **kwargs)
             for tid, r, s in products]
    if not params0.jump_routes and not params0.min_cost:
        return draft, params0  # нет рейсов и минимумов доставки в конфиге — батчить нечего
    params1 = batched_freight_params(conn, params0, [e.node for e in draft])
    final = [estimate_build(conn, cfg, tid, runs=r, streams=s, params=params1, **kwargs)
             for tid, r, s in products]
    return final, params1
