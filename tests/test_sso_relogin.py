"""Вход EVE SSO: понятная ошибка токен-эндпоинта («войди заново» вместо простыни httpx),
токены под client_id приложения (копия Forge с другим client_id их не ломает) с переездом из
общей записи (без client_id), пометка «нужен вход» у персонажа и сводка в итоге синка."""

from __future__ import annotations

import urllib.parse

import httpx
import pytest

from forge import storage
from forge.ingest.character import sso, tokens
from forge.ingest.character import sync as char_sync
from forge.ingest.character.sync import CharacterSyncResult
from forge.storage import repositories as repo
from forge.sync import orchestrator
from forge.web.service import ForgeService
from tests.test_character_sync import (
    CFG,
    _esi_all_endpoints,
    _esi_one_char_persistently_500,
)
from tests.test_sso import _fake_jwt
from tests.test_tokens import FakeBackend

INVALID_GRANT = {"error": "invalid_grant", "error_description": "Invalid refresh token. Token missing/expired."}


def _sso(valid: dict[str, str], calls: list | None = None, status: int = 400,
         body: dict | None = None) -> httpx.Client:
    """Мок токен-эндпоинта: refresh из ``valid`` → новый (ротация), любой другой — отказ EVE."""
    def handler(request: httpx.Request) -> httpx.Response:
        refresh = dict(urllib.parse.parse_qsl(request.content.decode()))["refresh_token"]
        if calls is not None:
            calls.append(refresh)
        if refresh in valid:
            return httpx.Response(200, json={"access_token": "AT", "refresh_token": valid[refresh],
                                             "expires_in": 1200})
        return httpx.Response(status, json=body or INVALID_GRANT)

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- ошибка токен-эндпоинта ---------------------------------------------------
def test_rejected_refresh_gives_short_relogin_error():
    with pytest.raises(sso.SsoError) as ei:
        sso.refresh_token(_sso({}), "CID", "DEAD")
    e = ei.value
    assert e.relogin and e.status == 400 and e.error == "invalid_grant"
    msg = str(e)
    assert "EVE SSO не принял сохранённый вход" in msg and "войди этим персонажем заново" in msg
    assert "Token missing/expired" in msg                      # ответ EVE виден — для разбора причин
    assert "developer.mozilla.org" not in msg and "Bad Request" not in msg   # не простыня httpx


@pytest.mark.parametrize(("status", "body", "relogin", "part"), [
    (400, {"error": "invalid_client"}, False, "Client ID"),        # настройка, вход не поможет
    (400, "<html>bad</html>", True, "HTTP 400"),                   # 400 без кода — тоже отказ в гранте
    (502, "<html>Bad gateway</html>", False, "HTTP 502"),          # сбой EVE — не токен
    (503, {"error": "temporarily_unavailable"}, False, "temporarily_unavailable"),
])
def test_sso_error_classification(status, body, relogin, part):
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(body, dict):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body)

    with pytest.raises(sso.SsoError) as ei:
        sso.refresh_token(httpx.Client(transport=httpx.MockTransport(handler)), "CID", "RT")
    assert ei.value.relogin is relogin and part in str(ei.value)


# --- хранение под client_id ---------------------------------------------------
def test_tokens_are_kept_per_client_id():
    be = FakeBackend()
    tokens.save(7, "OURS", backend=be, client_id="CID")
    tokens.save(7, "THEIRS", backend=be, client_id="OTHER")
    tokens.save(7, "SHARED", backend=be)
    assert be.store[(f"{tokens.SERVICE}:CID", "7")] == "OURS"
    assert tokens.load(7, backend=be, client_id="OTHER") == "THEIRS"
    assert tokens.load(7, backend=be) == "SHARED"                  # общая запись (без client_id)
    tokens.delete(7, backend=be, client_id="CID")
    assert tokens.load(7, backend=be, client_id="CID") is None and tokens.load(7, backend=be) == "SHARED"


def test_migrates_shared_token_and_keeps_it_for_older_copies():
    """Первый запуск: токен только в общей записи → переезжает под client_id; общая запись тоже
    обновляется (она «наша»), чтобы копия Forge с тем же client_id не отвалилась после ротации."""
    be = FakeBackend()
    tokens.save(7, "RT0", backend=be)
    char_sync.ensure_access_token("CID", 7, _sso({"RT0": "RT1"}), backend=be)
    assert tokens.load(7, backend=be, client_id="CID") == "RT1"
    assert tokens.load(7, backend=be) == "RT1"


def test_other_client_copy_cannot_break_our_login():
    """Копия с другим client_id записала СВОЙ токен в общую запись (вход по нему дал бы тут
    ровно эту 400) — идём по токену под client_id, а чужой не трогаем (сломали бы ту копию)."""
    be = FakeBackend()
    tokens.save(7, "OURS", backend=be, client_id="CID")
    tokens.save(7, "FOREIGN", backend=be)
    calls: list = []
    char_sync.ensure_access_token("CID", 7, _sso({"OURS": "OURS2"}, calls), backend=be)
    assert calls == ["OURS"]
    assert tokens.load(7, backend=be, client_id="CID") == "OURS2"
    assert tokens.load(7, backend=be) == "FOREIGN"


def test_falls_back_to_shared_token_rotated_by_older_copy():
    """Другая копия Forge (тот же client_id, общая запись) обновила токен после нас, и EVE наш уже не берёт —
    пробуем общую запись и снова сводим обе к новому токену."""
    be = FakeBackend()
    tokens.save(7, "OLD", backend=be, client_id="CID")
    tokens.save(7, "NEWER", backend=be)
    calls: list = []
    char_sync.ensure_access_token("CID", 7, _sso({"NEWER": "NEXT"}, calls), backend=be)
    assert calls == ["OLD", "NEWER"]
    assert tokens.load(7, backend=be, client_id="CID") == "NEXT" and tokens.load(7, backend=be) == "NEXT"


def test_all_rejected_raises_relogin_and_keeps_tokens():
    be = FakeBackend()
    tokens.save(7, "A", backend=be, client_id="CID")
    tokens.save(7, "B", backend=be)
    with pytest.raises(sso.SsoError) as ei:
        char_sync.ensure_access_token("CID", 7, _sso({}), backend=be)
    assert char_sync.needs_relogin(ei.value)
    assert tokens.load(7, backend=be, client_id="CID") == "A" and tokens.load(7, backend=be) == "B"


def test_server_error_is_not_treated_as_bad_token():
    be = FakeBackend()
    tokens.save(7, "A", backend=be, client_id="CID")
    tokens.save(7, "B", backend=be)
    calls: list = []
    with pytest.raises(sso.SsoError) as ei:
        char_sync.ensure_access_token("CID", 7, _sso({}, calls, status=502, body={"error": "bad_gateway"}),
                                      backend=be)
    assert not char_sync.needs_relogin(ei.value) and calls == ["A"]   # второй токен не жгли


def test_missing_token_needs_relogin():
    with pytest.raises(tokens.MissingToken) as ei:
        char_sync.ensure_access_token("CID", 7, _sso({}), backend=FakeBackend())
    assert char_sync.needs_relogin(ei.value) and "Добавить персонажа (EVE SSO)" in str(ei.value)
    assert not char_sync.needs_relogin(KeyError("skills"))   # KeyError — тоже LookupError, но не про вход


# --- пометка «нужен вход» ------------------------------------------------------
def _chars(conn, *ids: int) -> None:
    repo.characters(conn).upsert_many([
        {"character_id": cid, "name": f"C{cid}", "refresh_token": tokens.MARKER,
         "scopes": "esi-skills.read_skills.v1", "wallet_balance": None, "updated_at": "t"} for cid in ids])
    conn.commit()


def test_sync_marks_rejected_login_and_clears_it_once_accepted(conn):
    _chars(conn, 7, 8)
    be = FakeBackend()
    tokens.save(7, "DEAD", backend=be)
    tokens.save(8, "GOOD", backend=be)
    results = char_sync.sync(conn, CFG, esi=_esi_all_endpoints(), token_client=_sso({"GOOD": "GOOD"}),
                             backend=be)
    by_id = {r.character_id: r for r in results}
    assert by_id[7].relogin and "войди этим персонажем заново" in (by_id[7].error or "")
    assert by_id[8].error is None and not by_id[8].relogin
    assert repo.characters(conn).get(character_id=7)["refresh_token"] == tokens.MARKER_RELOGIN
    assert repo.characters(conn).get(character_id=8)["refresh_token"] == tokens.MARKER

    # Вход снова принят — пометка снимается сразу после refresh, даже если потом упадёт ESI.
    tokens.save(7, "FRESH", backend=be, client_id="CID")
    results = char_sync.sync(conn, CFG, esi=_esi_one_char_persistently_500(7),
                             token_client=_sso({"FRESH": "FRESH2", "GOOD": "GOOD"}), backend=be)
    r7 = next(r for r in results if r.character_id == 7)
    assert r7.error and "500" in r7.error and not r7.relogin
    assert repo.characters(conn).get(character_id=7)["refresh_token"] == tokens.MARKER


def test_add_character_saves_token_for_client_and_shared(conn, monkeypatch):
    """Повторный вход: токен — под client_id и в общую запись (копия Forge с тем же client_id подхватит),
    пометка «нужен вход» снята."""
    _chars(conn, 7)
    repo.characters(conn).upsert_many([{"character_id": 7, "refresh_token": tokens.MARKER_RELOGIN}])
    conn.commit()
    jwt = _fake_jwt({"iss": "login.eveonline.com", "sub": "CHARACTER:EVE:7", "name": "C7",
                     "scp": ["esi-skills.read_skills.v1"]})
    monkeypatch.setattr(sso, "run_auth_flow", lambda **_kw: sso.TokenSet(jwt, "FRESH", 1200, 0.0))
    be = FakeBackend()
    tokens.save(7, "DEAD", backend=be)
    char_sync.add_character(conn, CFG, backend=be)
    assert tokens.load(7, backend=be, client_id="CID") == "FRESH" and tokens.load(7, backend=be) == "FRESH"
    assert repo.characters(conn).get(character_id=7)["refresh_token"] == tokens.MARKER


# --- сводка в итоге синка и сервис --------------------------------------------
def test_character_note_groups_identical_errors_first():
    err = str(sso.SsoError(400, "invalid_grant"))
    note = orchestrator.character_note([
        CharacterSyncResult(1, "Lima Test", {}, error=err, relogin=True),
        CharacterSyncResult(2, "Nova Test", {"skills": 5}),
        CharacterSyncResult(3, "Kalaratri Omanid", {}, error=err, relogin=True),
        CharacterSyncResult(4, "Sierra Test", {}, error="ESI 500"),
    ])
    assert note is not None
    assert note.startswith(f"Lima Test, Kalaratri Omanid: ОШИБКА ({err})")   # одна запись на семерых
    assert note.count("ОШИБКА") == 2 and "Sierra Test: ОШИБКА (ESI 500)" in note
    assert note.endswith("Nova Test: skills=5")
    assert orchestrator.character_note([]) is None


def test_service_reports_characters_to_relogin(tmp_path):
    (tmp_path / "forge.toml").write_text('db_path = "forge.db"\n', encoding="utf-8")
    c = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(c)
    c.execute("INSERT INTO characters(character_id,name,refresh_token) VALUES (7,'Lima Test',?),"
              "(8,'Nova Test',?),(9,'Alt',NULL)", (tokens.MARKER_RELOGIN, tokens.MARKER))
    c.commit()
    c.close()
    svc = ForgeService(tmp_path / "forge.toml")
    assert svc.status()["relogin"] == ["Lima Test"]
    assert {ch["name"]: ch["relogin"] for ch in svc.characters()} == \
        {"Lima Test": True, "Nova Test": False, "Alt": False}
