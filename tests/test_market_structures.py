"""Несколько рынков-структур ([structures] market_structures): один сводный срез под регион
C-J6MT, токен — персонаж со скоупом рынков (сначала с ассетами там; 403 → следующий), ошибка
одной структуры не роняет синк персонажей; пустой список — одна структура из taj_mahgoon_market_id."""

from __future__ import annotations

import httpx
import pytest

from forge import config as config_mod
from forge import storage
from forge.ingest.character import structure_market as sm
from forge.ingest.character import sync as char_sync
from forge.ingest.character import tokens
from forge.ingest.esi import ESI_BASE, EsiClient
from forge.storage import repositories as repo
from forge.storage import sync_state
from forge.sync import orchestrator
from forge.web.service import ForgeService
from tests.test_tokens import FakeBackend

REGION = 10000009
A, B = 1030000000002, 1049588174022
ORDERS = {
    A: [{"type_id": 34, "price": 6.0, "volume_remain": 100, "is_buy_order": False},
        {"type_id": 34, "price": 5.0, "volume_remain": 50, "is_buy_order": True}],
    B: [{"type_id": 34, "price": 5.5, "volume_remain": 10, "is_buy_order": False},
        {"type_id": 34, "price": 5.2, "volume_remain": 5, "is_buy_order": True},
        {"type_id": 35, "price": 12.0, "volume_remain": 1, "is_buy_order": False}],
}
MARKET_SCOPE = sm.MARKET_SCOPE


def _esi(denied: dict[int, set[str]] | None = None, broken: set[int] | None = None, seen: list | None = None):
    """Мок ESI: ордера структур из ORDERS; ``denied[sid]`` — токены с 403; ``broken`` — 500."""
    denied = denied or {}
    broken = broken or set()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/markets/structures/" in path:
            sid = int(path.strip("/").split("/")[-1])
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if seen is not None:
                seen.append((sid, token))
            if sid in broken:
                return httpx.Response(500, text="boom")
            if token in denied.get(sid, set()):
                return httpx.Response(403, json={"error": "Market access denied"})
            return httpx.Response(200, json=ORDERS.get(sid, []), headers={"X-Pages": "1"})
        if path.endswith("/skills/"):
            return httpx.Response(200, json={"skills": []})
        if path.endswith("/wallet/"):
            return httpx.Response(200, json=42.0)
        return httpx.Response(200, json=[], headers={"X-Pages": "1"})

    return EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE),
                     sleep=lambda _s: None)


def test_market_ids_default_is_single_legacy_structure():
    assert config_mod.Structures().market_ids() == []
    assert config_mod.Structures(taj_mahgoon_market_id=A).market_ids() == [A]
    s = config_mod.Structures(taj_mahgoon_market_id=A, market_structures=[B, B, 0])
    assert s.market_ids() == [B]        # явный список сильнее, без повторов и нулей


def test_two_structures_aggregate_into_one_snapshot(conn):
    res = sm.sync_structure_markets(conn, _esi(), [A, B], REGION, {7: "T7"})
    conn.commit()
    assert [(r.structure_id, r.orders, r.error) for r in res] == [(A, 2, None), (B, 3, None)]
    t34 = repo.market_snapshot(conn).get(type_id=34, region_id=REGION)
    assert t34["sell_min"] == 5.5 and t34["buy_max"] == 5.2         # лучшее из ОБЕИХ структур
    assert t34["sell_volume"] == 110 and t34["buy_volume"] == 55    # объёмы суммируются
    assert repo.market_snapshot(conn).get(type_id=35, region_id=REGION)["sell_min"] == 12.0


def test_403_on_first_token_tries_next(conn):
    seen: list = []
    res = sm.sync_structure_markets(conn, _esi(denied={A: {"T1"}}, seen=seen), [A], REGION,
                                    {1: "T1", 2: "T2"}, owners={A: [1]})
    assert seen == [(A, "T1"), (A, "T2")]                           # сначала тот, у кого там ассеты
    assert res[0].error is None and res[0].character_id == 2


def test_no_access_anywhere_is_an_error_not_an_exception(conn):
    res = sm.sync_structure_markets(conn, _esi(denied={A: {"T1", "T2"}}), [A, B], REGION,
                                    {1: "T1", 2: "T2"})
    assert res[0].error and "нет доступа" in res[0].error and res[1].error is None
    assert repo.market_snapshot(conn).get(type_id=35, region_id=REGION) is not None   # B записан
    res2 = sm.sync_structure_markets(conn, _esi(), [A], REGION, {})
    assert res2[0].error and MARKET_SCOPE in res2[0].error


CFG_TOML = """
db_path = "x.db"
[sso]
client_id = "CID"
[locations.c_j6mt]
name = "C-J6MT"
region_id = 10000009
[structures]
taj_mahgoon_market_id = {taj}
market_structures = {lst}
"""


def _chars(conn, scopes_by_char: dict[int, str]):
    repo.characters(conn).upsert_many([
        {"character_id": cid, "name": f"C{cid}", "refresh_token": "keyring", "scopes": scopes,
         "wallet_balance": None, "updated_at": "t"} for cid, scopes in scopes_by_char.items()])
    conn.commit()
    be = FakeBackend()
    for cid in scopes_by_char:
        tokens.save(cid, "RT", backend=be)
    return be


def _token_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 1200})))


def test_structure_error_does_not_break_character_sync(conn):
    be = _chars(conn, {7: f"esi-skills.read_skills.v1 {MARKET_SCOPE}"})
    cfg = config_mod.loads(CFG_TOML.format(taj=0, lst=f"[{A}, {B}]"))
    results = char_sync.sync(conn, cfg, esi=_esi(broken={A}), token_client=_token_client(), backend=be)
    conn.commit()
    assert repo.characters(conn).get(character_id=7)["wallet_balance"] == 42.0     # персонаж синкнут
    by_name = {r.name: r for r in results if r.name.startswith(sm.RESULT_PREFIX)}
    assert set(by_name) == {f"{sm.RESULT_PREFIX} «{A}»", f"{sm.RESULT_PREFIX} «{B}»"}
    assert "500" in by_name[f"{sm.RESULT_PREFIX} «{A}»"].error
    assert by_name[f"{sm.RESULT_PREFIX} «{B}»"].counts == {"orders": 3}
    assert repo.market_snapshot(conn).get(type_id=35, region_id=REGION)["sell_min"] == 12.0


def test_empty_list_keeps_legacy_single_market_and_scope_filter(conn):
    be = _chars(conn, {7: "esi-skills.read_skills.v1", 8: f"esi-skills.read_skills.v1 {MARKET_SCOPE}"})
    cfg = config_mod.loads(CFG_TOML.format(taj=A, lst="[]"))
    seen: list = []
    results = char_sync.sync(conn, cfg, esi=_esi(seen=seen), token_client=_token_client(), backend=be)
    assert [sid for sid, _t in seen] == [A]                          # только taj_mahgoon_market_id
    mk = [r for r in results if r.name.startswith(sm.RESULT_PREFIX)]
    assert len(mk) == 1 and mk[0].counts == {"orders": 2} and mk[0].error is None
    # персонаж 7 без скоупа рынков не используется — запрос ровно один (токеном 8)
    assert len(seen) == 1


def test_orchestrator_records_structure_market_source(conn, monkeypatch):
    from forge.ingest.character.sync import CharacterSyncResult
    results = [CharacterSyncResult(7, "Igor", {"skills": 1}),
               CharacterSyncResult(0, f"{sm.RESULT_PREFIX} «1st Taj Mahgoon»", {"orders": 7752}),
               CharacterSyncResult(0, f"{sm.RESULT_PREFIX} «Other»", {}, error="нет доступа")]
    monkeypatch.setattr(orchestrator.character, "sync_all", lambda conn, cfg: results)
    orchestrator.sync_character(conn, config_mod.Config())
    st = sync_state.get(conn, "structure_market")
    assert st["status"] == "ok" and st["rows"] == 7752
    assert "«1st Taj Mahgoon»: ордеров 7752" in st["note"] and "ОШИБКА (нет доступа)" in st["note"]
    assert sync_state.get(conn, "character")["status"] == "ok"
    monkeypatch.setattr(orchestrator.character, "sync_all", lambda conn, cfg: results[2:])
    orchestrator.sync_character(conn, config_mod.Config())
    assert sync_state.get(conn, "structure_market")["status"] == "error"


def test_service_market_structures(tmp_path):
    (tmp_path / "forge.toml").write_text(
        '[locations.c_j6mt]\nname = "C-J6MT"\nsystem_id = 30000772\nregion_id = 10000009\n'
        f"[structures]\ntaj_mahgoon_market_id = {A}\n", encoding="utf-8")
    c = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(c)
    c.executescript(
        f"""
        INSERT INTO sde_systems(system_id,name,region_id,security) VALUES
            (30000772,'C-J6MT',10000009,-0.3),(30000773,'Other',10000009,-0.4);
        INSERT INTO universe_structures(structure_id,name,solar_system_id,status) VALUES
            ({A},'C-J6MT - 1st Taj Mahgoon',30000772,'ok'),({B},'Other - Market',30000773,'ok');
        """
    )
    c.commit()
    c.close()
    info = ForgeService(tmp_path / "forge.toml").market_structures()
    assert info["hub_system_id"] == 30000772 and info["effective"] == [A]
    known = {k["structure_id"]: k for k in info["known"]}
    assert known[A]["in_hub"] is True and known[B]["in_hub"] is False
    assert [k["structure_id"] for k in info["known"]] == [A, B]      # сначала структуры хаба


@pytest.mark.parametrize("lst", ["[]", f"[{B}]"])
def test_config_roundtrip(lst):
    cfg = config_mod.loads(CFG_TOML.format(taj=A, lst=lst))
    assert cfg.structures.market_ids() == ([A] if lst == "[]" else [B])
