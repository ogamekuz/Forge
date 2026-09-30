"""Рекомендации «что строить» — приоритет: максимально использовать остатки склада GPLB-C.

В отличие от ``engine.recommend`` (там себестоимость всегда полное замещение, склад не
учитывается вообще, см. правило проекта «core остатки не нетит») — здесь для каждого
кандидата (из своих чертежей) отдельно считается РЕАЛЬНАЯ прибыль: полная себестоимость минус
ISK-стоимость материалов, которые реально списались бы со склада при этой постройке. Кандидаты
ранжируются по ДВУМ метрикам сразу (нормировано, как и в ``engine.recommend``): реальная
прибыль И доля себестоимости, покрытая складом — цель не просто «выгодно», а «выгодно и при
этом не даёт остаткам просто пылиться».

Число прогонов НЕ задаётся вручную — оно подбирается отдельно для каждого кандидата так, чтобы
вычерпать самый дефицитный (относительно потребности) пересекающийся со складом материал
(``_max_runs_from_stock``): пользователь не должен гадать разумный размер партии, инструмент сам
масштабирует его под то, что реально лежит на полке.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from .. import core
from ..config import Config
from ..core import prices
from ..core.sourcing import NodeResult
from ..planner import timing
from .engine import Liquidity, _max_skill, _normalize, _winsorize, candidate_products


@dataclass
class StockRecommendation:
    product_type_id: int
    name: str
    runs: int
    capital: float            # полная себестоимость партии (полное замещение, как в Калькуляторе)
    real_capital: float       # реальный расход = capital − stock_value (то, что придётся купить)
    stock_value: float        # ISK-стоимость материалов, реально списанных со склада
    stock_utilization: float  # stock_value / capital — доля себестоимости, закрытая складом
    unit_cost: float
    real_profit: float        # прибыль С УЧЁТОМ реального (не полного) расхода
    real_roi: float           # real_profit / real_capital
    isk_per_hour: float
    daily_volume: float
    score: float = 0.0


def _stock_adjusted_costs(node: NodeResult, stock: dict[int, int]) -> tuple[float, float]:
    """Реальная стоимость материалов дерева ``node`` с учётом остатков склада + ISK-стоимость
    списанного со склада — рекурсивно, ``stock`` мутируется по ходу обхода (общий остаток не
    засчитывается дважды в разных ветках ОДНОГО кандидата, как и списание в отчёте).

    Упрощение относительно ``web.report`` (``_shrink_node``/``stock_net``): при частичном покрытии
    склад материала, который сам строится, под-дерево НЕ пересобирается заново под остаток —
    берётся пропорциональная доля его уже посчитанной стоимости (``remaining/quantity``). Для
    ранжирования кандидатов точности достаточно; точный BOM под конкретно выбранную постройку —
    в Калькуляторе/Отчёте (там честный пересчёт через ``build_node_cost``)."""
    real_cost = 0.0
    stock_value = 0.0
    for ln in node.lines:
        have = stock.get(ln.type_id, 0)
        covered = min(have, ln.quantity) if have > 0 else 0
        if covered > 0:
            stock[ln.type_id] = have - covered
            stock_value += covered * (ln.unit_cost or 0.0)
        remaining = ln.quantity - covered
        if remaining <= 0:
            continue  # материал целиком со склада — строить/покупать не нужно вообще
        if ln.child is not None:
            sub_real, sub_stock = _stock_adjusted_costs(ln.child, stock)
            frac = remaining / ln.quantity if ln.quantity else 0.0
            real_cost += sub_real * frac
            stock_value += sub_stock * frac
        else:
            real_cost += remaining * (ln.unit_cost or 0.0)
    return real_cost, stock_value


def _flatten_per_unit(node: NodeResult, scale: float, acc: dict[int, float]) -> None:
    """Свернуть дерево постройки в суммарную потребность каждого ``type_id`` (материалы И все
    промежуточные построенные компоненты — они тоже могут лежать на складе готовыми) на 1
    ЕДИНИЦУ конечного продукта верхнего узла. ``scale`` — сколько копий ЭТОГО узла нужно на эту
    1 верхнюю единицу (у корня — ``1/node.produced``)."""
    for ln in node.lines:
        need = ln.quantity * scale
        acc[ln.type_id] = acc.get(ln.type_id, 0.0) + need
        if ln.child is not None and ln.child.produced:
            _flatten_per_unit(ln.child, need / ln.child.produced, acc)


def _max_runs_from_stock(conn: sqlite3.Connection, node: NodeResult, stock: dict[int, int]) -> int:
    """Сколько прогонов верхнего продукта можно построить, упираясь ТОЛЬКО в то, что реально
    пересекается со складом — материалы, которых на складе вообще нет, и так пришлось бы
    покупать, batch они не ограничивают. Это и есть «тупо использовать всё, что есть»: партия
    масштабируется так, чтобы вычерпать самый дефицитный (относительно потребности) остаток
    среди пересекающихся, а не идти с числом прогонов, взятым из воздуха.

    Сверху зажимается ``max_production_limit`` чертежа — реальным лимитом EVE на прогоны В
    ОДНОМ джобе (без этого, при обильных минералах на складе, партия могла бы «математически»
    вырасти до сотен тысяч прогонов — нереализуемо в игре ни при каких обстоятельствах)."""
    if not node.produced or not node.runs:
        return 1
    per_unit: dict[int, float] = {}
    _flatten_per_unit(node, 1.0 / node.produced, per_unit)
    qty_per_run = node.produced / node.runs

    best_units: float | None = None
    for type_id, need in per_unit.items():
        have = stock.get(type_id, 0)
        if have <= 0 or need <= 0:
            continue
        units = have / need
        if best_units is None or units < best_units:
            best_units = units
    runs = node.runs if best_units is None else max(1, math.floor(best_units / qty_per_run))
    cap = prices.max_production_limit(conn, node.blueprint_type_id)
    return min(runs, cap) if cap else runs


def recommend_by_stock(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    min_volume: float | None = None,
    top: int = 20,
    limit_candidates: int | None = None,
    group_ids: list[int] | None = None,
    reserved: dict[int, int] | None = None,
) -> list[StockRecommendation]:
    """Отранжировать СВОИ чертежи по (реальная прибыль, доля себестоимости со склада).

    Склад — по настройке ``[stock]`` (``core.stock.on_hand``), за вычетом ``reserved`` —
    того, что уже зарезервировали под себя незавершённые стройки (``report.
    external_reservations``): иначе вкладка предлагала бы пустить в дело то, что уже обещано.

    Только ``owned=True`` (candidate_products) — рекомендация имеет смысл только для того,
    что реально можно построить прямо сейчас. Кандидаты без ХОТЬ КАКОГО-ТО пересечения со
    складом (``stock_value == 0``) отфильтровываются — иначе выдача не отличалась бы от
    обычного ``engine.recommend`` (эта вкладка — специально про использование остатков).

    Размер партии НЕ вводится вручную — для каждого кандидата отдельно подбирается число
    прогонов, вычерпывающее самый дефицитный пересекающийся со складом материал
    (``_max_runs_from_stock``): «просто использовать всё, что есть», без лишнего поля в UI.
    """
    rc = cfg.recommend
    min_volume = min_volume if min_volume is not None else rc.min_daily_volume

    params = core.build_params_from_config(cfg, conn)
    sell_region = params.cj6mt_region_id or params.jita_region_id
    max_ind = _max_skill(conn, "Industry")
    max_adv = _max_skill(conn, "Advanced Industry")
    stock_base = core.stock.subtract_reserved(core.stock.on_hand(conn, cfg), reserved)
    liq = Liquidity(conn, cfg, params.jita_region_id)

    recs: list[StockRecommendation] = []
    for type_id in candidate_products(conn, owned=True, limit=limit_candidates, group_ids=group_ids,
                                      location_ids=params.blueprint_location_ids, cfg=cfg):
        probe = core.estimate_build(conn, cfg, type_id, runs=1, streams=1, params=params)
        if probe.node.activity_id == 0 or probe.node.produced == 0:
            continue
        runs = _max_runs_from_stock(conn, probe.node, stock_base)
        est = probe if runs == 1 else core.estimate_build(conn, cfg, type_id, runs=runs, streams=1,
                                                         params=params)
        node = est.node
        pr = est.profit
        if node.activity_id == 0 or node.total_cost <= 0 or pr.profit is None or node.produced == 0:
            continue
        if pr.sell_unit_price and node.unit_cost < rc.min_cost_ratio * pr.sell_unit_price:
            continue  # битый чертёж SDE (см. engine.recommend)

        stock = dict(stock_base)  # своя копия остатков на каждого кандидата — независимая оценка
        _real_material_cost, stock_value = _stock_adjusted_costs(node, stock)
        if stock_value <= 0:
            continue  # ничего общего со складом — не относится к этой вкладке

        real_capital = node.total_cost - stock_value
        real_profit = (pr.profit or 0.0) + stock_value
        real_roi = real_profit / real_capital if real_capital > 0 else real_profit
        stock_utilization = stock_value / node.total_cost if node.total_cost else 0.0

        base = timing.base_time_per_run(conn, node.blueprint_type_id, node.activity_id)
        te = max(
            (timing.best_owned_te(conn, int(r["character_id"]), node.blueprint_type_id)
             for r in conn.execute("SELECT character_id FROM characters")),
            default=0,
        )
        time_mult = core.cost.facility_mults(
            params, node.activity_id, prices.group_id(conn, type_id), prices.category_id(conn, type_id)
        ).time_mult
        total_time = timing.job_seconds(base, node.runs, node.activity_id, te, max_ind, max_adv, time_mult)
        unit_time = total_time / node.produced if node.produced else 0.0
        isk_hour = (real_profit / node.produced) / (unit_time / 3600.0) if unit_time > 0 else 0.0
        vol = liq.daily(type_id, sell_region)
        if min_volume and vol < min_volume:
            continue

        recs.append(StockRecommendation(
            product_type_id=type_id, name=prices.type_name(conn, type_id), runs=runs,
            capital=node.total_cost, real_capital=real_capital, stock_value=stock_value,
            stock_utilization=stock_utilization, unit_cost=node.unit_cost,
            real_profit=real_profit, real_roi=real_roi, isk_per_hour=isk_hour, daily_volume=vol,
        ))

    if not recs:
        return []

    n_profit = _normalize(_winsorize([r.real_profit for r in recs], rc.winsor_pct))
    n_util = _normalize(_winsorize([r.stock_utilization for r in recs], rc.winsor_pct))
    for r, a, b in zip(recs, n_profit, n_util, strict=True):
        r.score = 0.5 * a + 0.5 * b

    recs.sort(key=lambda x: x.score, reverse=True)
    return recs[:top]
