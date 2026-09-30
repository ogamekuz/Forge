"""Pydantic-модели строк БД — валидация на границе ingest → storage.

Парсеры внешних данных возвращают эти модели; ``.model_dump()`` идёт в ``repositories.upsert``.
Покрыты таблицы, наполняемые в Фазе 1 (часть A). Character-таблицы — в части B.
"""

from __future__ import annotations

from pydantic import BaseModel


class SdeType(BaseModel):
    type_id: int
    name: str
    group_id: int | None = None
    category_id: int | None = None
    volume: float | None = None
    packaged_volume: float | None = None
    base_price: float | None = None
    market_group_id: int | None = None
    is_published: int = 1


class SdeSystem(BaseModel):
    system_id: int
    name: str | None = None
    region_id: int | None = None
    security: float | None = None


class MarketHistoryRow(BaseModel):
    type_id: int
    region_id: int
    day: str
    average: float | None = None
    highest: float | None = None
    lowest: float | None = None
    volume: int | None = None
    order_count: int | None = None


class MarketSnapshotRow(BaseModel):
    type_id: int
    region_id: int
    sell_min: float | None = None
    buy_max: float | None = None
    sell_volume: int | None = None
    buy_volume: int | None = None
    updated_at: str | None = None


class AdjustedPriceRow(BaseModel):
    type_id: int
    adjusted_price: float | None = None
    average_price: float | None = None
    updated_at: str | None = None


class CostIndexRow(BaseModel):
    system_id: int
    activity_id: int
    cost_index: float
    updated_at: str | None = None
