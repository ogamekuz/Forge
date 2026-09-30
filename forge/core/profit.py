"""Выручка и прибыль (чистые функции, без сети).

Выручка = цена продукта в C-J6MT − брокер и налог структуры − вывоз GPLB-C→C-J6MT.
Прибыль = выручка − полная себестоимость постройки.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import cost, prices
from .sourcing import NodeResult


@dataclass
class ProfitResult:
    sell_unit_price: float | None
    revenue: float | None
    profit: float | None
    roi: float | None  # profit / total_cost
    export_freight: float = 0.0  # вывоз GPLB-C→C-J6MT на 1 единицу (топливо за прыжок или объём×ставка)
    export_is_jump: bool = False  # True если вывоз = топливо за прыжок (капитал)


def compute_profit(
    conn: sqlite3.Connection,
    node: NodeResult,
    params: cost.BuildParams,
    sell_region_id: int,
    broker_fee: float,
    sales_tax: float,
) -> ProfitResult:
    # Продажа строго в месте сбыта (sell_region_id = C-J6MT). Без отката на Jita:
    # нет цены в C-J → нет оценки выручки (предмет не попадёт в рекомендации). Какой ценой
    # оценивать (sell-ордер или сразу в buy-ордер) — [market] sell_price (params.sell_price).
    price = prices.sale_price(conn, node.product_type_id, sell_region_id, params.sell_price)
    if price is None:
        return ProfitResult(None, None, None, None)

    # Вывоз GPLB-C→C-J6MT. Самоходный капитал уходит прыжком сам (не грузится в трюм) →
    # фикс топливо за прыжок НА ШТУКУ (jump_fuel). Прочее везут как груз → assembled-объём ×
    # ставка ISK/m³ (params.freight) — для fixed_jump-плеча эта ставка батчево пересчитана
    # заранее под ВЕСЬ заказ (см. core.batched_freight_params), здесь про рейсы не знаем
    # (см. комментарий в cost._hub_options — почему НЕ считаем рейс на каждый продукт отдельно).
    gid = prices.group_id(conn, node.product_type_id)
    jump = params.jump_fuel.get(("gplb_c", "c_j6mt"))
    if gid in params.jump_capable_groups and jump is not None:
        export, export_is_jump = jump, True
    else:
        export = prices.volume(conn, node.product_type_id) * params.freight.get(("gplb_c", "c_j6mt"), 0.0)
        export_is_jump = False
    revenue_unit = price * (1.0 - broker_fee - sales_tax) - export
    revenue = revenue_unit * node.produced
    profit = revenue - node.total_cost
    roi = profit / node.total_cost if node.total_cost else None
    return ProfitResult(price, revenue, profit, roi, export, export_is_jump)
