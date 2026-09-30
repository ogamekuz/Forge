"""Рынок структуры: агрегация ордеров + синк через MockTransport."""

from __future__ import annotations

import httpx

from forge.ingest.character import structure_market as sm
from forge.ingest.esi import ESI_BASE, EsiClient
from forge.storage import repositories as repo

INSMOTHER = 10000010
ORDERS = [
    {"type_id": 34, "price": 6.0, "volume_remain": 100, "is_buy_order": False},
    {"type_id": 34, "price": 6.5, "volume_remain": 50, "is_buy_order": False},
    {"type_id": 34, "price": 5.0, "volume_remain": 200, "is_buy_order": True},
    {"type_id": 34, "price": 5.2, "volume_remain": 80, "is_buy_order": True},
    {"type_id": 35, "price": 12.0, "volume_remain": 10, "is_buy_order": False},
]


def test_aggregate_orders_best_prices():
    rows = {r.type_id: r for r in sm.aggregate_orders(INSMOTHER, ORDERS)}
    t34 = rows[34]
    assert t34.sell_min == 6.0          # минимум среди sell
    assert t34.buy_max == 5.2           # максимум среди buy
    assert t34.sell_volume == 150       # 100 + 50
    assert t34.buy_volume == 280        # 200 + 80
    assert t34.region_id == INSMOTHER

    t35 = rows[35]
    assert t35.sell_min == 12.0 and t35.buy_max is None and t35.buy_volume == 0


def test_sync_structure_market_writes_snapshot(conn):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/markets/structures/" in request.url.path
        return httpx.Response(200, json=ORDERS, headers={"X-Pages": "1"})

    esi = EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))
    n, _expires = sm.sync_structure_market(conn, esi, 1035000000000, INSMOTHER, "TOKEN")
    conn.commit()
    assert n == 2  # type 34 и 35
    assert repo.market_snapshot(conn).get(type_id=34, region_id=INSMOTHER)["buy_max"] == 5.2
