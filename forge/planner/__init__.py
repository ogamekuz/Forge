"""planner — расписание «как строить»: слоты, чары, время, данные для Gantt.

Использует ``core`` (что и сколько строить) + ``storage`` (чьи чертежи/скиллы). Без сети.
Верхняя точка входа — ``plan_build``.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from .. import core
from ..config import Config
from ..core import bom
from ..i18n import tr
from . import schedule, slots, timing
from .schedule import Schedule

__all__ = [
    "BasketPlan",
    "Plan",
    "deadline_substreams",
    "format_duration",
    "plan_basket",
    "plan_build",
    "schedule",
    "schedule_basket",
    "slots",
    "streams_for_deadline",
    "timing",
]


def streams_for_deadline(
    conn: sqlite3.Connection, cfg: Config, product_type_id: int, total_runs: int, max_days: float
) -> int:
    """Сколько потоков нужно, чтобы КАЖДЫЙ поток (джоб) укладывался в max_days.

    Время на прогон считаем по лучшему TE и максимальным скиллам производственных чаров
    (оптимистично). Слоты не учитываем — лимитируем только длительность потока.
    """
    bp = bom.blueprint_for_product(conn, product_type_id)
    if bp is None or max_days <= 0:
        return 1
    base = timing.base_time_per_run(conn, bp.blueprint_type_id, bp.activity_id)
    mfg_set = set(cfg.manufacturing_character_ids)
    rx_set = set(cfg.reaction_character_ids)
    role_set = rx_set if bp.activity_id == 11 else mfg_set
    pool = "reaction" if bp.activity_id == 11 else "manufacturing"
    rows = conn.execute("SELECT character_id FROM characters").fetchall()
    # лимит слотов 0 в пуле — персонаж в нём не участвует (см. schedule.apply_slot_limits)
    cids = [int(r["character_id"]) for r in rows
            if (not role_set or int(r["character_id"]) in role_set)
            and cfg.planner.slot_limit(int(r["character_id"]), pool) != 0]
    best_te = max((timing.best_owned_te(conn, c, bp.blueprint_type_id) for c in cids), default=0)
    max_ind = max((slots.skill_level(conn, c, "Industry") for c in cids), default=0)
    max_adv = max((slots.skill_level(conn, c, "Advanced Industry") for c in cids), default=0)
    max_react = max((slots.skill_level(conn, c, "Reactions") for c in cids), default=0)
    best_spec = min(
        (timing.specialization_mult(conn, c, bp.blueprint_type_id, bp.activity_id) for c in cids),
        default=1.0,
    )
    params = core.build_params_from_config(cfg, conn)
    time_mult = core.cost.facility_mults(
        params, bp.activity_id, core.prices.group_id(conn, product_type_id),
        core.prices.category_id(conn, product_type_id),
    ).time_mult
    per_run = timing.job_seconds(base, 1, bp.activity_id, best_te, max_ind, max_adv, time_mult, max_react, best_spec)
    if per_run <= 0:
        return 1
    max_runs_per_job = max(1, int((max_days * 86400) // per_run))
    return max(1, math.ceil(total_runs / max_runs_per_job))


def deadline_substreams(conn: sqlite3.Connection, cfg: Config, max_stream_days: float | None):
    """Фабрика разбивки ПОД-компонентов под срок: ``(product_type_id, runs) -> число потоков``.

    Каждый под-джоб ≤ ``max_stream_days`` дней; число потоков = ceil(runs / влезает за срок),
    волнами (числом слотов НЕ ограничено), с потолком ``schedule.MAX_STREAMS_PER_NODE``.
    Возвращает None, если срок не задан — тогда под-компоненты строятся одним потоком.
    """
    if not max_stream_days:
        return None

    cap = max(1, int(cfg.planner.max_streams_per_node or schedule.MAX_STREAMS_PER_NODE))

    def fn(product_type_id: int, runs: int) -> int:
        n = streams_for_deadline(conn, cfg, product_type_id, runs, max_stream_days)
        return max(1, min(n, cap))

    return fn


@dataclass
class Plan:
    estimate: core.BuildEstimate
    schedule: Schedule
    params: core.cost.BuildParams  # финальные (батчевые) params — переиспользовать далее


def plan_build(
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
    max_stream_days: float | None = None,
    consolidate: bool = False,
    auto_streams: bool = False,
) -> Plan:
    """Себестоимость/прибыль (core) + расписание постройки по слотам и чарам.

    ``streams`` дробит ВЕРХНИЙ продукт (явно). ``max_stream_days`` дробит ПОД-компоненты так,
    чтобы каждый их джоб укладывался в срок (≤ N дней). Разбивка решается в дереве стоимости —
    расписание берёт потоки из него (``extract_jobs`` по ``node.streams``), поэтому материалы и
    Gantt согласованы. ``consolidate`` — объединять общий под-компонент, встречающийся в
    РАЗНЫХ ветках ЭТОГО ЖЕ продукта (см. ``sourcing.consolidate_shared_components``).
    ``auto_streams`` (учитывается только при ``consolidate=True`` и без явного
    ``max_stream_days``) — автоматически возвращать общей постройке параллелизм, потерянный
    при объединении (см. docstring ``consolidate_shared_components``). ``default_te`` — TE для
    срока постройки, если чертёж не во владении и не добывается инвентой (аналог ``default_me``
    для материалов; для инвенты срок сам считается по TE итоговой BPC от декриптора).
    """
    substream_fn = deadline_substreams(conn, cfg, max_stream_days)
    ests, params = core.estimate_basket(
        conn, cfg, [(product_type_id, runs, streams)],
        default_me=default_me, default_te=default_te, allow_build=allow_build, max_depth=max_depth,
        substream_fn=substream_fn,
    )
    est = ests[0]
    if consolidate:
        core.sourcing.consolidate_shared_components(
            conn, [est.node], params,
            default_me=default_me, default_te=default_te, max_depth=max_depth, allow_build=allow_build,
            substream_fn=substream_fn, auto_streams=auto_streams,
        )
        sell_region = params.cj6mt_region_id or params.jita_region_id
        est.profit = core.profit.compute_profit(
            conn, est.node, params, sell_region,
            broker_fee=cfg.industry.broker_fee, sales_tax=cfg.industry.sales_tax,
        )
    jobs = schedule.extract_jobs(est.node, conn, copy_jobs=cfg.planner.schedule_copy_jobs)
    sched = schedule.schedule_jobs(conn, cfg, jobs)
    return Plan(estimate=est, schedule=sched, params=params)


@dataclass
class BasketPlan:
    """План для корзины: оценки по каждому продукту + один общий график."""

    estimates: list[core.BuildEstimate]
    schedule: Schedule
    params: core.cost.BuildParams  # финальные (батчевые) params — переиспользовать далее


def plan_basket(
    conn: sqlite3.Connection,
    cfg: Config,
    products: Iterable[tuple[int, int, int]],
    *,
    default_me: int = 0,
    default_te: int = 0,
    allow_build: bool = True,
    max_depth: int = 8,
    max_stream_days: float | None = None,
    consolidate: bool = False,
    auto_streams: bool = False,
    force_buy_extra: frozenset[int] = frozenset(),
    me_overrides: dict[int, int] | None = None,
    te_overrides: dict[int, int] | None = None,
) -> BasketPlan:
    """Себестоимость + ОДИН общий график постройки для корзины предметов.

    ``products`` — список ``(product_type_id, runs, streams)``. Джобы всех деревьев сливаются
    в один список (id смещаются, чтобы не пересекались) и раскладываются по общему пулу слотов
    чаров одним ``schedule_jobs`` — слоты не бронируются дважды между продуктами.

    При ``consolidate=True`` одинаковый строящийся под-компонент, встречающийся в РАЗНЫХ
    ветках (в том числе в разных товарах корзины), объединяется в ОДНУ общую постройку —
    и на уровне материалов/стоимости (``sourcing.consolidate_shared_components`` — округление
    ME/реакции считается один раз на суммарный спрос, себестоимость пересчитывается), и на
    уровне расписания (``schedule.consolidate_jobs`` — общий джоб вместо нескольких), так что
    себестоимость и график согласованы.

    ``auto_streams`` — компенсировать потерю параллелизма от объединения (см. docstring
    ``sourcing.consolidate_shared_components``): без явного ``max_stream_days`` общая постройка
    получает столько потоков, сколько веток в неё слито, вместо одного последовательного джоба.
    """
    substream_fn = deadline_substreams(conn, cfg, max_stream_days)
    estimates, params = core.estimate_basket(
        conn, cfg, list(products),
        default_me=default_me, default_te=default_te, allow_build=allow_build, max_depth=max_depth,
        substream_fn=substream_fn, force_buy_extra=force_buy_extra,
        me_overrides=me_overrides, te_overrides=te_overrides,
    )
    if consolidate:
        core.sourcing.consolidate_shared_components(
            conn, [e.node for e in estimates], params,
            default_me=default_me, default_te=default_te, max_depth=max_depth, allow_build=allow_build,
            substream_fn=substream_fn, force_buy_extra=force_buy_extra, auto_streams=auto_streams,
            me_overrides=me_overrides, te_overrides=te_overrides,
        )
        # total_cost узлов изменился (обычно уменьшился) — пересчитать прибыль/ROI, иначе
        # estimate.profit останется от СТАРОЙ (до консолидации) себестоимости.
        sell_region = params.cj6mt_region_id or params.jita_region_id
        for e in estimates:
            e.profit = core.profit.compute_profit(
                conn, e.node, params, sell_region,
                broker_fee=cfg.industry.broker_fee, sales_tax=cfg.industry.sales_tax,
            )
    sched = schedule_basket(conn, cfg, [e.node for e in estimates], consolidate=consolidate)
    return BasketPlan(estimates=estimates, schedule=sched, params=params)


def schedule_basket(conn, cfg, nodes, *, consolidate: bool = False) -> Schedule:
    """Один общий график из готовых деревьев ``nodes`` (NodeResult), раскладка по общему пулу
    слотов. Число потоков узла берётся из ``node.streams``.

    Джобы извлекаются ОДНИМ вызовом ``extract_jobs_many`` (не по одному дереву за раз с
    последующим сдвигом id) — общая мемоизация между корнями гарантирует, что общий (после
    ``sourcing.consolidate_shared_components``, которую уже должен был вызвать ``plan_basket``
    выше) под-компонент получит РОВНО один джоб, даже если он делится между РАЗНЫМИ товарами
    корзины. При ОТДЕЛЬНОМ ``extract_jobs`` на каждое дерево (своя мемоизация у каждого
    вызова) общий узел получил бы ПОЛНЫЙ (не долевой) джоб от КАЖДОГО дерева, а
    ``consolidate_jobs`` затем СЛОЖИЛ бы эти дублирующиеся runs, задваивая количество прогонов
    сверх уже объединённого спроса (пример: Hexite, общий у Sylramic Fibers и Ferrogel, — 98
    прогонов вместо верных 49).

    Вынесено из ``plan_basket``, чтобы отчёт мог пересчитать график по деревьям, из которых
    уже вычтены остатки склада (компоненты в наличии не порождают джобов).
    """
    all_jobs = schedule.extract_jobs_many(nodes, conn, copy_jobs=cfg.planner.schedule_copy_jobs)
    if consolidate:
        # extract_jobs_many уже дедуплицирует джобы ОБЩИХ (по identity, после
        # sourcing.consolidate_shared_components) под-компонентов — слияние по КЛЮЧУ здесь
        # просто подстраховка на случай узлов, не прошедших ту консолидацию заранее; на уже
        # объединённом дереве это no-op (один джоб на ключ и так уже один).
        all_jobs = schedule.consolidate_jobs([all_jobs])
    return schedule.schedule_jobs(conn, cfg, all_jobs)


def format_duration(seconds: float) -> str:
    """Секунды → '2д 3ч 15м'."""
    total = int(seconds)
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(tr("{n}д", n=d))
    if h:
        parts.append(tr("{n}ч", n=h))
    if m or not parts:
        parts.append(tr("{n}м", n=m))
    return " ".join(parts)
