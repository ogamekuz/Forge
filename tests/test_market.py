"""Рынок public: парсеры history/aggregates/adjusted + snapshot через MockTransport."""

from __future__ import annotations

import httpx
import pytest

from forge.ingest import market
from forge.ingest.esi import ESI_BASE, EsiClient
from forge.storage import repositories as repo
from forge.sync.orchestrator import market_type_ids


def test_parse_history():
    data = [
        {"date": "2026-06-13", "average": 5.1, "highest": 5.5, "lowest": 4.9,
         "volume": 1000, "order_count": 42},
    ]
    rows = market.parse_history(34, 10000002, data)
    assert len(rows) == 1
    r = rows[0]
    assert r.type_id == 34 and r.region_id == 10000002 and r.day == "2026-06-13"
    assert r.volume == 1000 and r.order_count == 42


def test_parse_aggregates_coerces_strings():
    payload = {
        "34": {
            "buy": {"max": "5.50", "volume": "1000000"},
            "sell": {"min": "6.00", "volume": "500000"},
        }
    }
    rows = market.parse_aggregates(10000002, payload)
    assert len(rows) == 1
    r = rows[0]
    assert r.type_id == 34
    assert r.buy_max == 5.5 and r.sell_min == 6.0
    assert r.sell_volume == 500000 and r.buy_volume == 1000000
    assert r.updated_at is not None


def test_parse_adjusted():
    data = [{"type_id": 34, "adjusted_price": 5.2, "average_price": 5.0}]
    rows = market.parse_adjusted(data)
    assert rows[0].type_id == 34 and rows[0].adjusted_price == 5.2


def test_sync_snapshot_writes_rows(conn):
    payload = {
        "34": {"buy": {"max": "5.50", "volume": "10"}, "sell": {"min": "6.00", "volume": "20"}},
        "35": {"buy": {"max": "11.0", "volume": "1"}, "sell": {"min": "12.0", "volume": "2"}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "aggregates" in str(request.url)
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    n = market.sync_snapshot(conn, 10000002, [34, 35], client=client)
    conn.commit()
    assert n == 2
    assert repo.market_snapshot(conn).count() == 2
    assert repo.market_snapshot(conn).get(type_id=34, region_id=10000002)["sell_min"] == 6.0


def test_sync_history_skips_404_types(conn):
    """Тип без истории (404) пропускается, остальные грузятся."""
    def handler(request: httpx.Request) -> httpx.Response:
        tid = int(dict(request.url.params).get("type_id", "0"))
        if tid == 165:
            return httpx.Response(404, json={"error": "Not found"})
        return httpx.Response(200, json=[
            {"date": "2026-06-13", "average": 5.0, "highest": 5.5, "lowest": 4.5,
             "volume": 100, "order_count": 3}])

    esi = EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))
    n, _ = market.sync_history(conn, esi, 10000002, [34, 165, 35])
    conn.commit()
    assert n == 2  # 34 и 35 загрузились, 165 пропущен
    assert market.parse_history  # sanity


def test_sync_snapshot_chunks_long_type_lists(conn, monkeypatch):
    """Тысячи type_id режутся на чанки — иначе один GET даёт URL слишком длинный (414)."""
    monkeypatch.setattr(market, "FUZZWORK_TYPES_PER_REQUEST", 2)
    requests: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        types = dict(request.url.params)["types"].split(",")
        requests.append(types)
        return httpx.Response(200, json={
            t: {"buy": {"max": "1.0", "volume": "1"}, "sell": {"min": "2.0", "volume": "1"}}
            for t in types
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))
    n = market.sync_snapshot(conn, 10000002, [34, 35, 36, 37, 38], client=client)
    conn.commit()
    assert n == 5
    assert [len(r) for r in requests] == [2, 2, 1]  # 3 чанка по ≤2 id
    assert repo.market_snapshot(conn).count() == 5


def test_sync_history_commits_in_batches(conn, monkeypatch):
    """Прогресс коммитится пачками: исключение/kill на середине не теряет уже скачанное."""
    monkeypatch.setattr(market, "HISTORY_COMMIT_BATCH", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        tid = int(dict(request.url.params).get("type_id", "0"))
        if tid == 99:  # серверная ошибка валит синк после первого полного батча
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=[
            {"date": "2026-06-13", "average": 5.0, "highest": 5.5, "lowest": 4.5,
             "volume": 100, "order_count": 3}])

    esi = EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE),
                    sleep=lambda _s: None)
    with pytest.raises(httpx.HTTPStatusError):
        market.sync_history(conn, esi, 10000002, [34, 35, 99])
    # 34 и 35 (полный батч) закоммичены до падения на 99 — прогресс не потерян.
    assert repo.market_history(conn).count() == 2


def test_sync_history_reports_progress(conn):
    """Длинный прогон зовёт progress(done, total) — чтобы не выглядел зависшим."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"date": "2026-06-13", "average": 5.0, "highest": 5.5, "lowest": 4.5,
             "volume": 100, "order_count": 3}])

    esi = EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))
    seen: list[tuple[int, int]] = []
    ids = list(range(1, 251))  # 250 типов
    market.sync_history(conn, esi, 10000002, ids, progress=lambda d, t: seen.append((d, t)))
    conn.commit()
    assert seen[-1] == (250, 250)          # финальный вызов на 100%
    assert seen == [(100, 250), (200, 250), (250, 250)]  # каждые 100 + финал


def test_market_type_ids_includes_raw_materials(conn):
    """Входные материалы (Nocxium, Megacyte…) должны попасть в список синка Jita,
    даже если они не производятся ни одним чертежом."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id, name) VALUES (200,'Widget'),(34,'Tritanium'),(40,'Nocxium');
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1001,1,200,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1001,1,34,100),(1001,1,40,50);
        """
    )
    conn.commit()
    ids = set(market_type_ids(conn))
    assert 200 in ids   # продукт чертежа
    assert 34 in ids    # входной материал
    assert 40 in ids    # входной материал (raw, не производится)


def test_sync_adjusted_prices_writes_rows(conn):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"type_id": 34, "adjusted_price": 5.2, "average_price": 5.0}],
            headers={"Expires": "Wed, 21 Oct 2099 07:28:00 GMT"},
        )

    esi = EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))
    n, expires = market.sync_adjusted_prices(conn, esi)
    conn.commit()
    assert n == 1
    assert expires.startswith("2099-10-21")
    assert repo.market_adjusted_prices(conn).get(type_id=34)["adjusted_price"] == 5.2
