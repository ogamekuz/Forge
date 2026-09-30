"""Ликвидность «Объём/сут» по [recommend] liquidity_source / liquidity_days и синк истории
региона сбыта (C-J6MT). По умолчанию — по записям истории (daily_volume)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from forge import config as config_mod
from forge import recommend as rec
from forge import storage
from forge.ingest import market
from forge.ingest.esi import ESI_BASE, EsiClient
from forge.recommend.engine import Liquidity, daily_volume, history_daily_volume
from forge.storage import sync_state
from forge.sync import orchestrator
from forge.web.service import ForgeService

JITA, CJ = 10000002, 10000009

BASE = """
db_path = "forge.db"
[industry]
[recommend]
w_roi = 1.0
w_isk_hour = 1.0
w_liquidity = 0.0
{recommend}
[locations.jita]
name = "Jita"
system_id = 30000142
region_id = 10000002
[locations.c_j6mt]
name = "C-J6MT"
system_id = 30000772
region_id = 10000009
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006
"""


def cfg(recommend: str = "") -> config_mod.Config:
    return config_mod.loads(BASE.format(recommend=recommend))


def _hist(conn, type_id: int, region: int, rows: list[tuple[str, int]]):
    conn.executemany(
        "INSERT INTO market_history(type_id,region_id,day,volume) VALUES (?,?,?,?)",
        [(type_id, region, d, v) for d, v in rows],
    )
    conn.commit()


def _seed_history(conn):
    """Регион C-J: последний день истории — 2026-09-27 (есть у 34). Тип 2000 торговался лишь 3
    дня из последних 7 и когда-то давно; 2001 — только 20 дней назад; в Jita — своя история."""
    _hist(conn, 34, CJ, [("2026-09-27", 1)])
    _hist(conn, 2000, CJ, [("2026-09-27", 70), ("2026-09-25", 35), ("2026-09-21", 35), ("2026-08-01", 1000)])
    _hist(conn, 2001, CJ, [("2026-09-07", 700)])
    _hist(conn, 2000, JITA, [("2026-09-28", 140), ("2026-09-27", 140)])
    conn.execute("INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES (2002,?,1000.0,5000)", (CJ,))
    conn.commit()


# ------------------------------------------------------------------ расчёт

def test_default_source_is_legacy():
    c = config_mod.Config()
    assert c.recommend.liquidity_source == "sell_region" and c.recommend.liquidity_days == 30


def test_legacy_daily_volume_unchanged(conn):
    _seed_history(conn)
    # среднее по ЗАПИСЯМ (дни без сделок не в счёт) — формула по умолчанию, завышает неликвид
    assert daily_volume(conn, 2000, CJ) == pytest.approx((70 + 35 + 35 + 1000) / 4)
    assert daily_volume(conn, 2002, CJ) == 5000.0          # нет истории — выставленное на продажу
    liq = Liquidity(conn, cfg(), JITA)
    assert liq.source == "sell_region"
    for tid in (2000, 2001, 2002):
        assert liq.daily(tid, CJ) == daily_volume(conn, tid, CJ)


def test_history_window_counts_days_without_trades_as_zero(conn):
    _seed_history(conn)
    # окно 7 дней до 2026-09-27: сделки 27-го, 25-го и 21-го = 140 шт. / 7 дней
    assert history_daily_volume(conn, 2000, CJ, 7, "2026-09-27") == pytest.approx(140 / 7)
    assert history_daily_volume(conn, 2000, CJ, 3, "2026-09-27") == pytest.approx(105 / 3)
    assert history_daily_volume(conn, 2000, CJ, 7, None) == 0.0


def test_sell_region_history_source(conn):
    _seed_history(conn)
    liq = Liquidity(conn, cfg('liquidity_source = "sell_region_history"\nliquidity_days = 7'), JITA)
    assert liq.anchor(CJ) == "2026-09-27"                  # последний день истории РЕГИОНА
    assert liq.daily(2000, CJ) == pytest.approx(140 / 7)
    # торговался 20 дней назад: окно — от последнего дня региона, а не от своей сделки
    assert liq.daily(2001, CJ) == 0.0
    assert liq.daily(2002, CJ) == 0.0                      # выставлен, но не продан — оборота нет
    liq30 = Liquidity(conn, cfg('liquidity_source = "sell_region_history"'), JITA)
    assert liq30.daily(2001, CJ) == pytest.approx(700 / 30)


def test_jita_source_uses_jita_history_for_any_market(conn):
    _seed_history(conn)
    liq = Liquidity(conn, cfg('liquidity_source = "jita"\nliquidity_days = 2'), JITA)
    assert liq.anchor(JITA) == "2026-09-28"
    assert liq.daily(2000, CJ) == pytest.approx(280 / 2)   # спросили C-J — ответ по Jita
    assert liq.daily(2000, JITA) == pytest.approx(280 / 2)


def test_unknown_source_falls_back_to_legacy(conn):
    _seed_history(conn)
    liq = Liquidity(conn, cfg('liquidity_source = "nonsense"'), JITA)
    assert liq.source == "sell_region" and liq.daily(2000, CJ) == daily_volume(conn, 2000, CJ)


def _seed_products(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (34,'Tritanium',0.01),(2000,'Alpha',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,10);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES
            (34,10000009,5.0,4.0,1000000),(2000,10000009,1000.0,900.0,5000);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0);
        """
    )
    conn.commit()


def test_recommend_daily_volume_and_filter_follow_source(conn):
    _seed_products(conn)
    _hist(conn, 2000, CJ, [("2026-09-27", 60), ("2026-09-26", 30)])
    legacy = rec.recommend(conn, cfg(), owned=True, runs=1, top=5)
    assert legacy[0].daily_volume == pytest.approx(45.0)            # по записям: (60+30)/2
    real = rec.recommend(conn, cfg('liquidity_source = "sell_region_history"'), owned=True, runs=1, top=5)
    assert real[0].daily_volume == pytest.approx(90 / 30)          # за 30 календарных дней
    # фильтр «мин. объём/сут» — по тому же источнику
    assert rec.recommend(conn, cfg('liquidity_source = "sell_region_history"'), owned=True, runs=1,
                         top=5, min_volume=10) == []
    assert rec.recommend(conn, cfg(), owned=True, runs=1, top=5, min_volume=10)


# ------------------------------------------------------------------ синк истории региона сбыта

class _Recorder:
    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        region = int(request.url.path.strip("/").split("/")[1])
        tid = int(dict(request.url.params)["type_id"])
        self.calls.append((region, tid))
        return httpx.Response(200, json=[{"date": "2026-09-27", "average": 1.0, "highest": 1.0,
                                          "lowest": 1.0, "volume": 10, "order_count": 1}],
                              headers={"Expires": "Wed, 21 Oct 2099 07:28:00 GMT"})


def _seed_snapshots(conn):
    conn.executescript(
        """
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume,buy_volume) VALUES
            (34,10000002,5.0,4.0,100,0),(35,10000002,6.0,5.0,100,0),
            (34,10000009,6.0,5.0,50,0),(36,10000009,9.0,8.0,0,7),(37,10000009,9.0,8.0,0,0);
        """
    )
    conn.commit()


def _sync(conn, c, rec_: _Recorder, force=True):
    esi = EsiClient(client=httpx.Client(transport=httpx.MockTransport(rec_.handler), base_url=ESI_BASE),
                    sleep=lambda _s: None)
    return orchestrator.sync_market(conn, c, esi=esi, type_ids=[34, 35], force=force)


@pytest.fixture
def no_snapshot_net(monkeypatch):
    monkeypatch.setattr(market, "sync_snapshot", lambda *a, **k: 0)
    monkeypatch.setattr(market, "sync_adjusted_prices", lambda *a, **k: (0, None))


def test_sync_sell_region_history_when_enabled(conn, no_snapshot_net):
    _seed_snapshots(conn)
    r = _Recorder()
    res = _sync(conn, cfg('liquidity_source = "sell_region_history"'), r)
    assert sorted(t for reg, t in r.calls if reg == JITA) == [34, 35]
    assert sorted(t for reg, t in r.calls if reg == CJ) == [34, 36]   # торгуемые на рынке-структуре
    n = conn.execute("SELECT COUNT(*) FROM market_history WHERE region_id = ?", (CJ,)).fetchone()[0]
    assert n == 2
    assert "история региона сбыта 10000009" in (res.note or "")


def test_sync_skips_sell_region_history_by_default(conn, no_snapshot_net):
    _seed_snapshots(conn)
    r = _Recorder()
    _sync(conn, cfg(), r)
    assert {reg for reg, _t in r.calls} == {JITA}                     # по умолчанию: только Jita
    r2 = _Recorder()
    _sync(conn, cfg('liquidity_source = "jita"'), r2)
    assert {reg for reg, _t in r2.calls} == {JITA}


def test_sell_region_history_respects_daily_gate_but_fills_empty(conn, no_snapshot_net):
    _seed_snapshots(conn)
    future = (datetime.now(UTC) + timedelta(hours=10)).isoformat()
    sync_state.mark_success(conn, "market_history", rows=1, expires=future)
    c = cfg('liquidity_source = "sell_region_history"')
    r = _Recorder()
    _sync(conn, c, r, force=False)
    # кэш Jita свеж — её историю не трогаем; историю C-J ещё ни разу не качали — качаем сразу
    assert {reg for reg, _t in r.calls} == {CJ}
    r2 = _Recorder()
    _sync(conn, c, r2, force=False)
    assert r2.calls == []                                             # теперь — только по суточному гейту


def test_sell_region_history_error_does_not_fail_market_sync(conn, no_snapshot_net, monkeypatch):
    _seed_snapshots(conn)
    real = market.sync_history

    def flaky(conn_, esi, region_id, type_ids, progress=None):
        if region_id == CJ:
            raise httpx.ConnectError("boom")
        return real(conn_, esi, region_id, type_ids, progress=progress)

    monkeypatch.setattr(market, "sync_history", flaky)
    res = _sync(conn, cfg('liquidity_source = "sell_region_history"'), _Recorder())
    assert "ОШИБКА" in (res.note or "")
    assert sync_state.get(conn, "market")["status"] == "ok"


# ------------------------------------------------------------------ сервис

def test_service_liquidity_info(tmp_path):
    (tmp_path / "forge.toml").write_text(BASE.format(recommend=""), encoding="utf-8")
    c = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(c)
    c.close()
    svc = ForgeService(tmp_path / "forge.toml")
    info = svc.liquidity_info()
    assert info["source"] == "sell_region" and "по записям" in info["text"] and info["warning"] is None
    svc.put_config({"recommend": {"liquidity_source": "sell_region_history", "liquidity_days": 14}})
    info = svc.liquidity_info()
    assert info["source"] == "sell_region_history" and info["days"] == 14
    assert "14 календ. дн." in info["text"] and info["warning"]       # истории региона ещё нет
    c = storage.connect(str(tmp_path / "forge.db"))
    c.execute("INSERT INTO market_history(type_id,region_id,day,volume) VALUES (34,10000009,'2026-09-27',5)")
    c.commit()
    c.close()
    info = svc.liquidity_info()
    assert info["anchor"] == "2026-09-27" and info["warning"] is None
