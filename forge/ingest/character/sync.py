"""Оркестрация по персонажам: добавление через SSO, refresh токена, выгрузка данных,
рынок структуры C-J6MT.

Сеть: токен-эндпоинт SSO (отдельный httpx.Client, абсолютный URL) и ESI (EsiClient).
refresh-токены берутся/сохраняются в keyring (модуль ``tokens``) под client_id приложения;
состояние входа (нужен ли новый вход) — маркер в ``characters.refresh_token``.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC

import httpx

from ...config import Config
from ...i18n import tr
from ...storage import repositories as repo
from ...storage.db import transaction
from ..esi import EsiClient
from . import data, sso, structure_market, structures, tokens


@dataclass
class CharacterSyncResult:
    character_id: int
    name: str
    counts: dict[str, int]
    error: str | None = None  # None = синк этого чара прошёл; иначе — текст исключения
    relogin: bool = False     # EVE SSO не принял сохранённый вход — нужен новый вход персонажем


def needs_relogin(exc: BaseException) -> bool:
    """Ошибка, после которой поможет только новый вход персонажем через EVE SSO."""
    return isinstance(exc, tokens.MissingToken) or (isinstance(exc, sso.SsoError) and exc.relogin)


def _set_login_state(conn: sqlite3.Connection, character_id: int, marker: str) -> None:
    """Состояние входа персонажа — маркер в ``characters.refresh_token`` (сам токен — в keyring)."""
    with transaction(conn):
        repo.characters(conn).upsert_many([{"character_id": character_id, "refresh_token": marker}])


def add_character(
    conn: sqlite3.Connection,
    cfg: Config,
    token_client: httpx.Client | None = None,
    backend: tokens.KeyringBackend | None = None,
    open_browser=None,
) -> sso.CharacterClaims:
    """Интерактивно добавить персонажа через EVE SSO (PKCE). Требует браузер.

    Повторный вход тем же персонажем безопасен: запись и токен перезаписываются, маркер «нужен
    вход» снимается. Токен пишется под client_id приложения И в общую запись: свежий вход —
    самый новый действующий токен, и копия Forge с тем же client_id сразу подхватит его (копия
    со СВОИМ client_id, что читает только общую запись, для этого персонажа потребует входа
    заново — её токен здесь всё равно не годился бы)."""
    if not cfg.sso.client_id:
        raise ValueError(tr("Не задан sso.client_id в конфиге."))
    kwargs = {}
    if open_browser is not None:
        kwargs["open_browser"] = open_browser
    token_set = sso.run_auth_flow(
        client_id=cfg.sso.client_id,
        redirect_uri=cfg.sso.redirect_uri,
        scopes=cfg.sso.scopes,
        callback_port=cfg.sso.callback_port,
        client=token_client,
        **kwargs,
    )
    claims = sso.parse_claims(token_set.access_token)
    tokens.save(claims.character_id, token_set.refresh_token, backend=backend, client_id=cfg.sso.client_id)
    tokens.save(claims.character_id, token_set.refresh_token, backend=backend)

    from datetime import datetime

    with transaction(conn):
        repo.characters(conn).upsert_many(
            [
                {
                    "character_id": claims.character_id,
                    "name": claims.name,
                    "refresh_token": tokens.MARKER,
                    "scopes": " ".join(claims.scopes),
                    "wallet_balance": None,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ]
        )
    return claims


def list_characters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return repo.characters(conn).all()


def ensure_access_token(
    client_id: str,
    character_id: int,
    token_client: httpx.Client,
    backend: tokens.KeyringBackend | None = None,
) -> sso.TokenSet:
    """Обновить access token по сохранённому refresh-токену; сохранить новый refresh.

    Сначала — токен под client_id приложения, затем общая запись (из неё переезжают токены
    без client_id; её же могла обновить копия Forge с тем же client_id). Токен, который EVE отвергла
    (400), пропускаем и пробуем следующий; сбой EVE или сети (5xx и т.п.) — сразу наружу.
    Новый refresh пишем под client_id, а в общую запись — только если она «наша» (пустая или
    с тем самым токеном): так такая копия не отвалится после ротации, а чужой токен (копии с другим
    client_id) не будет затёрт."""
    scoped = tokens.load(character_id, backend=backend, client_id=client_id)
    shared = tokens.load(character_id, backend=backend)
    candidates = [t for t in dict.fromkeys((scoped, shared)) if t]
    if not candidates:
        raise tokens.MissingToken(tr("Нет сохранённого входа (character_id={character_id}) — {hint}",
                                     character_id=character_id, hint=tr(sso.RELOGIN_HINT)))
    rejected: sso.SsoError | None = None
    for refresh in candidates:
        try:
            token_set = sso.refresh_token(token_client, client_id, refresh)
        except sso.SsoError as exc:
            if exc.status != 400:
                raise
            rejected = exc
            continue
        # EVE может ротировать refresh-токен — сохраняем актуальный.
        tokens.save(character_id, token_set.refresh_token, backend=backend, client_id=client_id)
        if shared is None or shared == refresh:
            tokens.save(character_id, token_set.refresh_token, backend=backend)
        return token_set
    assert rejected is not None
    raise rejected


def _structure_names(conn: sqlite3.Connection, ids: list[int]) -> dict[int, str]:
    """Имена структур из universe_structures (для подписи итогов синка рынков)."""
    if not ids:
        return {}
    ph = ",".join("?" for _ in ids)
    return {int(r["structure_id"]): r["name"] for r in conn.execute(
        f"SELECT structure_id, name FROM universe_structures WHERE structure_id IN ({ph}) "
        f"AND status = 'ok' AND name IS NOT NULL", ids)}


def sync(
    conn: sqlite3.Connection,
    cfg: Config,
    esi: EsiClient | None = None,
    token_client: httpx.Client | None = None,
    backend: tokens.KeyringBackend | None = None,
) -> list[CharacterSyncResult]:
    """Синк данных всех сохранённых чаров + имена структур + рынки-структуры (один раз).

    Рынки-структуры (``cfg.structures.market_ids()``): каждый — токеном персонажа со скоупом
    рынков структур (сначала те, у кого там лежат ассеты; 401/403/404 — следующий), все ордера —
    в ОДИН срез под регион C-J6MT. Результат по каждой структуре — отдельной строкой в итогах
    (``structure_market.RESULT_PREFIX``); ошибка одной структуры синк не роняет."""
    chars = list_characters(conn)
    if not chars:
        raise LookupError(tr("Нет добавленных персонажей. Запусти `forge auth add`."))
    if not cfg.sso.client_id:
        raise ValueError(tr("Не задан sso.client_id в конфиге."))

    own_esi = esi is None
    esi = esi or EsiClient()
    own_tc = token_client is None
    token_client = token_client or httpx.Client(timeout=30.0)

    results: list[CharacterSyncResult] = []
    structure_tokens: dict[int, str] = {}
    market_tokens: dict[int, str] = {}
    try:
        for ch in chars:
            cid = int(ch["character_id"])
            name = ch["name"]
            scopes = (ch["scopes"] or "").split()
            try:
                ts = ensure_access_token(cfg.sso.client_id, cid, token_client, backend=backend)
                if ch["refresh_token"] != tokens.MARKER:  # вход снова принят — снять «нужен вход»
                    _set_login_state(conn, cid, tokens.MARKER)
                if structures.STRUCTURE_SCOPE in scopes:
                    structure_tokens[cid] = ts.access_token
                if structure_market.MARKET_SCOPE in scopes:
                    market_tokens[cid] = ts.access_token
                counts = data.sync_character_data(conn, esi, cid, name, scopes, ts.access_token)
                results.append(CharacterSyncResult(cid, name, counts))
            except Exception as exc:
                # Один персонаж может упасть на устойчивой ESI-ошибке конкретно СВОЕГО ответа
                # (напр. /characters/{id}/blueprints/ стабильно отдаёт 500 у CCP для
                # одного конкретного чара — это баг на стороне ESI, не Forge, и он может не
                # чиниться неделями). Без изоляции исключение прервало бы ВЕСЬ цикл — остальные
                # чары (и рынок структуры ниже) не синкались бы, пока не починится
                # ЭТОТ один. Изолируем по чару: остальные синкаются как обычно, а этот —
                # просто попадает в результаты с error вместо counts.
                relogin = needs_relogin(exc)
                if relogin:  # EVE не принимает вход — пометить («Обзор», «Персонажи»)
                    with contextlib.suppress(sqlite3.Error):  # пометка не должна рвать цикл
                        _set_login_state(conn, cid, tokens.MARKER_RELOGIN)
                results.append(CharacterSyncResult(cid, name, {}, error=str(exc), relogin=relogin))

        # Имена/системы структур, где лежат ассеты и чертежи (нужны складу по системам) —
        # ДО рынков, чтобы их итоги подписать именами. Сбой здесь не должен ронять синк
        # персонажей — данные чаров уже записаны.
        s = cfg.structures
        if structure_tokens:
            extra = [x for x in (s.taj_mahgoon_market_id, s.gplb_engineering_complex_id,
                                 s.gplb_refinery_id, *s.market_ids(),
                                 *(f.location_id for f in cfg.facilities))
                     if x and x >= 1_000_000_000]  # только структуры игроков, не NPC-станции
            try:
                sc = structures.sync_structures(conn, esi, structure_tokens, extra_ids=extra)
                results.append(CharacterSyncResult(0, "structures", sc))
            except Exception as exc:
                results.append(CharacterSyncResult(0, "structures", {}, error=str(exc)))

        # Рынки-структуры — один сводный срез под регион C-J6MT.
        c_j6mt = cfg.locations.get("c_j6mt")
        region_id = c_j6mt.region_id if c_j6mt else 0
        market_ids = s.market_ids()
        if market_ids and region_id:
            try:
                owners = structures.owners_by_root(conn)
                sm = structure_market.sync_structure_markets(
                    conn, esi, market_ids, region_id, market_tokens, owners)
            except Exception as exc:  # напр. запись в БД — не роняем синк персонажей
                sm = [structure_market.StructureMarketResult(sid, error=str(exc)) for sid in market_ids]
            names = _structure_names(conn, market_ids)
            for r in sm:
                label = structure_market.result_label(names.get(r.structure_id) or r.structure_id)
                results.append(CharacterSyncResult(0, label, {} if r.error else {"orders": r.orders},
                                                   error=r.error))
    finally:
        if own_esi:
            esi.close()
        if own_tc:
            token_client.close()
    return results
