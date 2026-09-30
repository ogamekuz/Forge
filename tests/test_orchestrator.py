"""orchestrator: история рынка только по реально торгуемым типам."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from forge.config import Config
from forge.ingest import market
from forge.storage import sync_state
from forge.sync import orchestrator


class _StubEsi:
    def close(self) -> None:
        pass


def _seed_snapshot(conn):
    conn.executescript(
        """
        INSERT INTO market_snapshot(type_id, region_id, sell_min, buy_max, sell_volume, buy_volume, updated_at)
        VALUES (34,10000002,6.0,5.0,100,0,'t'),   -- торгуется (sell_volume>0)
               (35,10000002,12.0,11.0,0,0,'t'),   -- мёртвый: оборота нет
               (36,10000002,1.0,1.0,0,5,'t');     -- торгуется (buy_volume>0)
        """
    )
    conn.commit()


def test_traded_type_ids_filters_by_volume(conn):
    _seed_snapshot(conn)
    assert orchestrator.traded_type_ids(conn, 10000002) == {34, 36}


def test_sync_character_marks_success_when_only_some_characters_fail(conn, monkeypatch):
    """Реальный кейс: у одного персонажа ESI стабильно отдаёт 500 (напр. на /blueprints/), у
    остальных всё синкается нормально. Сбой одного НЕ красит ВЕСЬ синк как 'error' — иначе
    юзер видел бы «ошибка синка», хотя 9 из 10 персонажей реально обновились. Полный сбой (ВСЕ
    персонажи упали) — 'error', так и должно быть."""
    from forge.ingest.character.sync import CharacterSyncResult

    def fake_sync_all_partial(conn, cfg):
        return [
            CharacterSyncResult(1, "Sierra Test", {}, error="500 Internal Server Error"),
            CharacterSyncResult(2, "Igor", {"skills": 5}),
        ]

    monkeypatch.setattr(orchestrator.character, "sync_all", fake_sync_all_partial)
    res = orchestrator.sync_character(conn, Config())
    assert res.skipped is False
    assert "Sierra Test: ОШИБКА" in res.note
    assert "Igor: skills=5" in res.note
    assert sync_state.get(conn, "character")["status"] == "ok"

    def fake_sync_all_all_failed(conn, cfg):
        return [CharacterSyncResult(1, "Sierra Test", {}, error="500 Internal Server Error")]

    monkeypatch.setattr(orchestrator.character, "sync_all", fake_sync_all_all_failed)
    orchestrator.sync_character(conn, Config())
    assert sync_state.get(conn, "character")["status"] == "error"


def test_sync_market_history_only_for_traded(conn, monkeypatch):
    _seed_snapshot(conn)

    # снапшот уже в БД — sync_snapshot не должен лезть в сеть
    monkeypatch.setattr(market, "sync_snapshot", lambda *a, **k: 0)
    captured: dict = {}

    def fake_history(conn, esi, region_id, type_ids, progress=None):
        captured["ids"] = list(type_ids)
        return 0, None

    monkeypatch.setattr(market, "sync_history", fake_history)
    monkeypatch.setattr(market, "sync_adjusted_prices", lambda *a, **k: (0, None))

    orchestrator.sync_market(conn, Config(), esi=_StubEsi(), type_ids=[34, 35, 36], force=True)
    # 35 без оборота отфильтрован — историю по нему не дёргаем
    assert sorted(captured["ids"]) == [34, 36]


def test_sync_market_skips_history_when_fresh(conn, monkeypatch):
    """Свежая суточная история не перекачивается (это и есть «12-мин зависание»)."""
    _seed_snapshot(conn)
    future = (datetime.now(UTC) + timedelta(hours=10)).isoformat()
    sync_state.mark_success(conn, "market_history", rows=1, expires=future)

    calls = {"snapshot": 0, "history": 0, "adjusted": 0}
    monkeypatch.setattr(market, "sync_snapshot", lambda *a, **k: calls.__setitem__("snapshot", calls["snapshot"] + 1) or 0)
    monkeypatch.setattr(market, "sync_history", lambda *a, **k: calls.__setitem__("history", calls["history"] + 1) or (0, None))
    monkeypatch.setattr(market, "sync_adjusted_prices", lambda *a, **k: calls.__setitem__("adjusted", calls["adjusted"] + 1) or (0, None))

    orchestrator.sync_market(conn, Config(), esi=_StubEsi(), type_ids=[34, 36], force=False)
    assert calls["snapshot"] == 1   # цены обновляются всегда
    assert calls["history"] == 0    # история свежа → пропущена
    assert calls["adjusted"] == 1
    assert sync_state.get(conn, "market")["status"] == "ok"
