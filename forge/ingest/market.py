"""Загрузка public-рынка (Jita / регион The Forge).

Три источника:
  1. История — ESI ``GET /markets/{region_id}/history/`` → ``market_history``.
  2. Текущие лучшие цены — агрегаты Fuzzwork → ``market_snapshot``.
  3. Adjusted/average price — ESI ``GET /markets/prices/`` → ``market_adjusted_prices`` (для EIV).

Рынок игровой структуры (C-J6MT) требует авторизации и реализуется в части B.
Парсеры отделены от сети — тестируются на замоканных ответах.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import httpx

from ..storage import repositories as repo
from ..storage.db import transaction
from ..storage.models import AdjustedPriceRow, MarketHistoryRow, MarketSnapshotRow
from .esi import EsiClient

FUZZWORK_AGGREGATES_URL = "https://market.fuzzwork.co.uk/aggregates/"

# История коммитится пачками по столько type_id — чтобы kill на середине длинного
# цикла сохранял прогресс (upsert идемпотентен), а не терял всё до финального коммита.
HISTORY_COMMIT_BATCH = 200

# Сколько type_id класть в один запрос агрегатов Fuzzwork: всё разом → URL слишком
# длинный (HTTP 414). ~1000 id ≈ безопасная длина строки запроса.
FUZZWORK_TYPES_PER_REQUEST = 1000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --- парсеры (без сети) -----------------------------------------------------
def parse_history(type_id: int, region_id: int, data: list[dict[str, Any]]) -> list[MarketHistoryRow]:
    """Ответ ESI history → строки ``market_history``."""
    return [
        MarketHistoryRow(
            type_id=type_id,
            region_id=region_id,
            day=str(d["date"]),
            average=d.get("average"),
            highest=d.get("highest"),
            lowest=d.get("lowest"),
            volume=d.get("volume"),
            order_count=d.get("order_count"),
        )
        for d in data
    ]


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    return int(float(v))


def parse_aggregates(region_id: int, payload: dict[str, Any]) -> list[MarketSnapshotRow]:
    """Ответ агрегатов Fuzzwork → строки ``market_snapshot``.

    Значения у Fuzzwork приходят строками — приводим к числам.
    """
    now = _now_iso()
    rows: list[MarketSnapshotRow] = []
    for type_id_str, agg in payload.items():
        buy = agg.get("buy", {})
        sell = agg.get("sell", {})
        rows.append(
            MarketSnapshotRow(
                type_id=int(type_id_str),
                region_id=region_id,
                sell_min=_to_float(sell.get("min")),
                buy_max=_to_float(buy.get("max")),
                sell_volume=_to_int(sell.get("volume")),
                buy_volume=_to_int(buy.get("volume")),
                updated_at=now,
            )
        )
    return rows


def parse_adjusted(data: list[dict[str, Any]]) -> list[AdjustedPriceRow]:
    """Ответ ESI ``/markets/prices/`` → строки ``market_adjusted_prices``."""
    now = _now_iso()
    return [
        AdjustedPriceRow(
            type_id=d["type_id"],
            adjusted_price=d.get("adjusted_price"),
            average_price=d.get("average_price"),
            updated_at=now,
        )
        for d in data
    ]


# --- синки (сеть) -----------------------------------------------------------
def sync_history(
    conn: sqlite3.Connection,
    esi: EsiClient,
    region_id: int,
    type_ids: Iterable[int],
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, str | None]:
    """Залить историю по списку type_id. Возвращает (число строк, expires).

    Коммитит пачками по ``HISTORY_COMMIT_BATCH`` обработанных type_id, чтобы прерывание
    длинного цикла не теряло уже скачанное (upsert идемпотентен). ``progress(done, total)``
    зовётся по ходу — длинный суточный прогон не выглядит зависшим.
    """
    type_ids = list(type_ids)
    total_types = len(type_ids)
    total = 0
    last_expires: str | None = None
    batch_rows: list[dict[str, Any]] = []
    processed = 0

    def flush() -> None:
        nonlocal total, batch_rows
        if not batch_rows:
            return
        with transaction(conn):
            total += repo.market_history(conn).upsert_many(batch_rows)
        batch_rows = []

    for i, type_id in enumerate(type_ids, 1):
        try:
            resp = esi.get(f"/markets/{region_id}/history/", params={"type_id": type_id})
        except httpx.HTTPStatusError as exc:
            # У многих типов нет рыночной истории (не торгуются) → ESI отдаёт 404/400.
            # Пропускаем такой тип, а не валим весь синк.
            if exc.response.status_code not in (400, 404):
                raise
            resp = None
        if resp is not None:
            last_expires = resp.expires or last_expires
            rows = parse_history(type_id, region_id, resp.data)
            batch_rows.extend(r.model_dump() for r in rows)
            processed += 1
            if processed % HISTORY_COMMIT_BATCH == 0:
                flush()
        if progress and (i % 100 == 0 or i == total_types):
            progress(i, total_types)
    flush()
    return total, last_expires


def sync_snapshot(
    conn: sqlite3.Connection,
    region_id: int,
    type_ids: Iterable[int],
    client: httpx.Client | None = None,
) -> int:
    """Залить срез лучших цен из агрегатов Fuzzwork. Возвращает число строк.

    type_ids режем на чанки: все id в одном GET-параметре дают слишком длинный URL
    (HTTP 414 / обрыв соединения) при тысячах типов.
    """
    type_ids = list(type_ids)
    own = client is None
    client = client or httpx.Client(timeout=60.0)
    payload: dict[str, Any] = {}
    try:
        for i in range(0, len(type_ids), FUZZWORK_TYPES_PER_REQUEST):
            chunk = type_ids[i : i + FUZZWORK_TYPES_PER_REQUEST]
            resp = client.get(
                FUZZWORK_AGGREGATES_URL,
                params={"region": region_id, "types": ",".join(map(str, chunk))},
            )
            resp.raise_for_status()
            payload.update(resp.json())
    finally:
        if own:
            client.close()
    rows = parse_aggregates(region_id, payload)
    with transaction(conn):
        return repo.market_snapshot(conn).upsert_many(r.model_dump() for r in rows)


def sync_adjusted_prices(conn: sqlite3.Connection, esi: EsiClient) -> tuple[int, str | None]:
    """Залить adjusted/average price по всем типам. Возвращает (число строк, expires)."""
    resp = esi.get("/markets/prices/")
    rows = parse_adjusted(resp.data)
    with transaction(conn):
        total = repo.market_adjusted_prices(conn).upsert_many(r.model_dump() for r in rows)
    return total, resp.expires
