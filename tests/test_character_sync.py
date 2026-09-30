"""Оркестрация чаров: ensure_access_token (refresh) и полный sync (мокнуто)."""

from __future__ import annotations

import httpx

from forge import config as config_mod
from forge.ingest.character import sync as char_sync
from forge.ingest.character import tokens
from forge.ingest.esi import ESI_BASE, EsiClient
from forge.storage import repositories as repo
from tests.test_tokens import FakeBackend

CFG = config_mod.loads(
    """
db_path = "x.db"
[sso]
client_id = "CID"
callback_port = 8765
[locations.c_j6mt]
name = "C-J6MT"
region_id = 10000010
"""
)


def _token_client(new_refresh: str = "RT_NEW") -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "grant_type=refresh_token" in request.content.decode()
        return httpx.Response(
            200, json={"access_token": "AT", "refresh_token": new_refresh, "expires_in": 1200}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ensure_access_token_refreshes_and_rotates():
    be = FakeBackend()
    tokens.save(7, "RT_OLD", backend=be)
    ts = char_sync.ensure_access_token("CID", 7, _token_client("RT_ROTATED"), backend=be)
    assert ts.access_token == "AT"
    # Новый refresh-токен сохранён обратно.
    assert tokens.load(7, backend=be) == "RT_ROTATED"


def _esi_all_endpoints() -> EsiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/skills/"):
            return httpx.Response(200, json={"skills": []})
        if path.endswith("/wallet/"):
            return httpx.Response(200, json=42.0)
        return httpx.Response(200, json=[], headers={"X-Pages": "1"})

    return EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))


def test_full_sync_processes_stored_characters(conn):
    repo.characters(conn).upsert_many(
        [{"character_id": 7, "name": "Igor", "refresh_token": "keyring",
          "scopes": "esi-skills.read_skills.v1", "wallet_balance": None, "updated_at": "t"}]
    )
    conn.commit()
    be = FakeBackend()
    tokens.save(7, "RT", backend=be)

    results = char_sync.sync(
        conn, CFG, esi=_esi_all_endpoints(), token_client=_token_client(), backend=be
    )
    conn.commit()
    assert any(r.character_id == 7 for r in results)
    assert repo.characters(conn).get(character_id=7)["wallet_balance"] == 42.0


def _esi_one_char_persistently_500(broken_character_id: int) -> EsiClient:
    """Мок реального кейса: ESI стабильно отдаёт 500 на ЛЮБОЙ эндпоинт ОДНОГО конкретного
    персонажа (напр. /characters/{id}/blueprints/) — у остальных всё в порядке."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if f"/characters/{broken_character_id}/" in path:
            return httpx.Response(500, text="Internal Server Error")
        if path.endswith("/skills/"):
            return httpx.Response(200, json={"skills": []})
        if path.endswith("/wallet/"):
            return httpx.Response(200, json=42.0)
        return httpx.Response(200, json=[], headers={"X-Pages": "1"})

    return EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))


def test_sync_isolates_one_character_failure_from_the_rest(conn):
    """Реальный кейс юзера: ESI стабильно отдаёт 500 на /characters/{id}/blueprints/ ОДНОГО
    чара — это не должно прерывать ВЕСЬ цикл: остальные 9 персонажей синкаются, не дожидаясь
    именно этого одного. Персонаж с ошибкой ПЕРВЫЙ в списке — чтобы
    доказать, что цикл реально продолжается ПОСЛЕ него, а не просто последний элемент не считан."""
    repo.characters(conn).upsert_many([
        {"character_id": 90000003, "name": "Sierra Test", "refresh_token": "keyring",
         "scopes": "esi-skills.read_skills.v1", "wallet_balance": None, "updated_at": "t"},
        {"character_id": 7, "name": "Igor", "refresh_token": "keyring",
         "scopes": "esi-skills.read_skills.v1", "wallet_balance": None, "updated_at": "t"},
    ])
    conn.commit()
    be = FakeBackend()
    tokens.save(90000003, "RT", backend=be)
    tokens.save(7, "RT", backend=be)

    results = char_sync.sync(
        conn, CFG, esi=_esi_one_char_persistently_500(90000003),
        token_client=_token_client(), backend=be,
    )
    conn.commit()

    broken = next(r for r in results if r.character_id == 90000003)
    ok = next(r for r in results if r.character_id == 7)
    assert broken.error is not None and "500" in broken.error
    assert broken.counts == {}
    assert ok.error is None
    # Здоровый персонаж синкнулся как обычно, несмотря на то что упавший шёл ПЕРВЫМ в списке.
    assert repo.characters(conn).get(character_id=7)["wallet_balance"] == 42.0
