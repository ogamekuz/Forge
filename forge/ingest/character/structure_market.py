"""Рынки игровых структур (1st Taj Mahgoon и другие в C-J6MT) — публично недоступны, тянем
авторизованно по токену чара с доступом.

ESI ``GET /markets/structures/{structure_id}/`` отдаёт сырые ордера (с пагинацией) и требует
скоуп ``esi-markets.structure_markets.v1`` + доступ к рынку структуры у ТОГО чара, чьим токеном
спрашиваем. Ордера ВСЕХ структур списка (``config.Structures.market_ids``) сводятся в ОДИН срез
лучших цен под регион рынка сбыта (min sell, max buy, суммарные объёмы) — иначе upsert по
``(type_id, region_id)`` перезаписал бы одну структуру другой.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from ...i18n import EN, N_, tr
from ...storage import repositories as repo
from ...storage.db import transaction
from ...storage.models import MarketSnapshotRow
from ..esi import EsiClient

MARKET_SCOPE = "esi-markets.structure_markets.v1"
# Префикс имени результата синка по структуре (CharacterSyncResult.name) — по нему оркестратор
# собирает отдельную строку «Рынки-структуры» для вкладки «Обзор». Имя пишется на языке пульта
# (``result_label``), узнаётся — на любом (``is_result``: язык могли переключить посреди синка).
RESULT_PREFIX = N_("Рынок-структура")
RESULT_LABEL = N_("Рынок-структура «{name}»")


def result_label(name: object) -> str:
    """Имя результата синка по структуре: «Рынок-структура «1st Taj Mahgoon»»."""
    return tr(RESULT_LABEL, name=name)


def is_result(name: str) -> bool:
    """Это результат синка рынка структуры (``result_label``) — на любом языке."""
    return name.startswith((RESULT_PREFIX, EN.get(RESULT_PREFIX, RESULT_PREFIX)))


@dataclass
class _Agg:
    sell_min: float | None = None
    buy_max: float | None = None
    sell_volume: int = 0
    buy_volume: int = 0


@dataclass
class StructureMarketResult:
    """Итог по одной структуре: сколько ордеров, чьим токеном, или ошибка (синк не роняет)."""

    structure_id: int
    orders: int = 0
    character_id: int | None = None
    error: str | None = None


def aggregate_orders(region_id: int, orders: list[dict[str, Any]]) -> list[MarketSnapshotRow]:
    """Сырые ордера (одной или нескольких структур) → срез лучших цен по type_id.

    sell_min — минимальная цена среди sell-ордеров; buy_max — максимальная среди buy;
    объёмы — сумма remain по соответствующей стороне.
    """
    now = datetime.now(UTC).isoformat()
    agg: dict[int, _Agg] = {}
    for o in orders:
        type_id = o["type_id"]
        price = float(o["price"])
        remain = int(o.get("volume_remain", 0))
        is_buy = bool(o.get("is_buy_order", False))
        a = agg.setdefault(type_id, _Agg())
        if is_buy:
            a.buy_max = price if a.buy_max is None else max(a.buy_max, price)
            a.buy_volume += remain
        else:
            a.sell_min = price if a.sell_min is None else min(a.sell_min, price)
            a.sell_volume += remain

    return [
        MarketSnapshotRow(
            type_id=type_id,
            region_id=region_id,
            sell_min=a.sell_min,
            buy_max=a.buy_max,
            sell_volume=a.sell_volume,
            buy_volume=a.buy_volume,
            updated_at=now,
        )
        for type_id, a in agg.items()
    ]


def sync_structure_market(
    conn: sqlite3.Connection,
    esi: EsiClient,
    structure_id: int,
    region_id: int,
    token: str,
) -> tuple[int, str | None]:
    """Залить срез рынка ОДНОЙ структуры в market_snapshot. Возвращает (число строк, expires)."""
    resp = esi.get_paginated(f"/markets/structures/{structure_id}/", token=token)
    rows = aggregate_orders(region_id, resp.data)
    with transaction(conn):
        n = repo.market_snapshot(conn).upsert_many(r.model_dump() for r in rows)
    return n, resp.expires


def _token_order(structure_id: int, tokens: dict[int, str], owners: dict[int, list[int]]) -> list[int]:
    """Кого пробовать: сначала персонажи, у которых в этой структуре лежат ассеты/чертежи (там
    точно докались и, скорее всего, есть доступ к рынку), потом остальные со скоупом рынков."""
    first = [c for c in owners.get(structure_id, []) if c in tokens]
    return first + [c for c in tokens if c not in first]


def fetch_structure_orders(
    esi: EsiClient, structure_id: int, tokens: dict[int, str], owners: dict[int, list[int]]
) -> tuple[list[dict[str, Any]], StructureMarketResult]:
    """Ордера одной структуры: перебор токенов (401/403/404 — у этого чара нет доступа → следующий).
    Прочие ошибки ESI/сети — в результат, без исключения наружу."""
    res = StructureMarketResult(structure_id)
    if not tokens:
        res.error = tr("нет персонажа со скоупом {scope}", scope=MARKET_SCOPE)
        return [], res
    denied: list[str] = []
    for cid in _token_order(structure_id, tokens, owners):
        try:
            resp = esi.get_paginated(f"/markets/structures/{structure_id}/", token=tokens[cid])
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code in (401, 403, 404):
                denied.append(str(code))
                continue
            res.error = f"ESI {code}"
            return [], res
        except httpx.HTTPError as exc:
            res.error = tr("сеть: {exc}", exc=exc)
            return [], res
        res.orders, res.character_id = len(resp.data or []), cid
        return list(resp.data or []), res
    res.error = tr("нет доступа ни у одного персонажа ({codes})", codes=", ".join(sorted(set(denied))))
    return [], res


def sync_structure_markets(
    conn: sqlite3.Connection,
    esi: EsiClient,
    structure_ids: Iterable[int],
    region_id: int,
    tokens: dict[int, str],
    owners: dict[int, list[int]] | None = None,
) -> list[StructureMarketResult]:
    """Ордера ВСЕХ ``structure_ids`` → ОДИН срез в ``market_snapshot`` под ``region_id``.

    ``tokens`` — access-токены персонажей со скоупом рынков структур; ``owners`` — structure_id
    → персонажи с ассетами там (их токены пробуем первыми). Ошибка одной структуры не роняет
    остальные: её ордеров просто нет в срезе, а причина — в результате. Срез пишется, только если
    хоть одна структура ответила (иначе прошлый срез остаётся как есть)."""
    owners = owners or {}
    all_orders: list[dict[str, Any]] = []
    results: list[StructureMarketResult] = []
    for sid in structure_ids:
        orders, res = fetch_structure_orders(esi, int(sid), tokens, owners)
        all_orders.extend(orders)
        results.append(res)
    if any(r.error is None for r in results):
        rows = aggregate_orders(region_id, all_orders)
        with transaction(conn):
            repo.market_snapshot(conn).upsert_many(r.model_dump() for r in rows)
    return results
