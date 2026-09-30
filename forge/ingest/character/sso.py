"""EVE SSO v2 — OAuth2 PKCE для нативного приложения (без client secret).

Чистые, тестируемые куски (генерация PKCE, сборка URL, обмен/refresh токенов, разбор JWT)
отделены от интерактива (локальный callback-сервер + браузер).
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ...i18n import N_, tr

AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize/"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"  # noqa: S105 — URL, not a secret
ISSUERS = {"login.eveonline.com", "https://login.eveonline.com"}
# Подсказка «как войти заново» (русский ключ; показывать — ``tr(RELOGIN_HINT)``).
RELOGIN_HINT = N_("войди этим персонажем заново: «Обзор → Добавить персонажа (EVE SSO)» (или `forge auth add`)")


class SsoError(Exception):
    """Отказ токен-эндпоинта EVE SSO с разобранным ответом (RFC 6749 §5.2: ``error`` и
    ``error_description``) — вместо простыни httpx «Client error '400 Bad Request' for url …».

    ``relogin`` — сохранённый вход больше не годится: ``invalid_grant`` (refresh-токен отозван —
    смена пароля или отзыв доступа приложения на сайте EVE, перенос персонажа — либо выдан
    приложению с другим client_id) или 400 без разборчивого кода. Помогает только новый вход."""

    def __init__(self, status: int, error: str = "", description: str = "") -> None:
        self.status = status
        self.error = error
        self.description = description
        super().__init__(self._text())

    @property
    def relogin(self) -> bool:
        # Запрос собираем сами — 400 без кода тоже отказ в гранте, а не наша ошибка.
        return self.error == "invalid_grant" or (self.status == 400 and not self.error)

    @property
    def detail(self) -> str:
        return ": ".join(x for x in (self.error, self.description) if x) or f"HTTP {self.status}"

    def _text(self) -> str:
        if self.relogin:
            return tr("EVE SSO не принял сохранённый вход ({detail}) — {hint}", detail=self.detail,
                      hint=tr(RELOGIN_HINT))
        if self.error == "invalid_client":
            return tr("EVE SSO не знает приложение ({detail}) — проверь Client ID "
                      "в «Настройки → Система»", detail=self.detail)
        return f"EVE SSO: {self.detail} (HTTP {self.status})"


def _raise_for_token_error(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    error = description = ""
    try:
        body = resp.json()
    except ValueError:  # не JSON (напр. HTML-страница 502) — хватит кода ответа
        body = None
    if isinstance(body, dict):
        error = str(body.get("error") or "")
        description = str(body.get("error_description") or "")[:200]
    raise SsoError(resp.status_code, error, description)


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int
    obtained_at: float  # unix-время получения

    @property
    def expires_at(self) -> float:
        return self.obtained_at + self.expires_in

    def is_expired(self, skew: int = 60) -> bool:
        return time.time() >= self.expires_at - skew


# --- PKCE -------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Вернуть (code_verifier, code_challenge) для PKCE S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


# --- обмен токенов (тестируется через MockTransport) ------------------------
def _post_token(client: httpx.Client, data: dict[str, str]) -> TokenSet:
    resp = client.post(
        TOKEN_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "login.eveonline.com",
        },
    )
    _raise_for_token_error(resp)
    payload = resp.json()
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_in=int(payload.get("expires_in", 1200)),
        obtained_at=time.time(),
    )


def exchange_code(
    client: httpx.Client, client_id: str, code: str, code_verifier: str
) -> TokenSet:
    return _post_token(
        client,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
    )


def refresh_token(client: httpx.Client, client_id: str, refresh: str) -> TokenSet:
    return _post_token(
        client,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        },
    )


# --- разбор JWT (access token) ----------------------------------------------
@dataclass
class CharacterClaims:
    character_id: int
    name: str
    scopes: list[str]


def decode_jwt_payload(token: str) -> dict:
    """Декодировать payload JWT без проверки подписи.

    Токен получен напрямую от token-эндпоинта EVE по TLS, поэтому для локального
    личного инструмента доверяем источнику; здесь лишь извлекаем claims.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(tr("Некорректный JWT"))
    payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def parse_claims(token: str) -> CharacterClaims:
    payload = decode_jwt_payload(token)
    iss = payload.get("iss", "")
    if iss not in ISSUERS:
        raise ValueError(tr("Неожиданный issuer JWT: {iss}", iss=repr(iss)))
    sub = payload.get("sub", "")  # формат 'CHARACTER:EVE:<id>'
    if not sub.startswith("CHARACTER:EVE:"):
        raise ValueError(tr("Неожиданный sub: {sub}", sub=repr(sub)))
    character_id = int(sub.rsplit(":", 1)[1])
    scp = payload.get("scp", [])
    scopes = [scp] if isinstance(scp, str) else list(scp)
    return CharacterClaims(character_id=character_id, name=payload.get("name", ""), scopes=scopes)


# --- интерактивный флоу (браузер + локальный callback) ----------------------
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        code = qs.get("code", [None])[0]
        if code:
            # Только запрос с кодом считаем callback'ом; favicon и прочее игнорируем,
            # чтобы не сбить ожидание и не завершить сервер раньше времени.
            _CallbackHandler.code = code
            _CallbackHandler.state = qs.get("state", [None])[0]
            body = "<html><body><h2>{}</h2>{}</body></html>".format(
                tr("Forge: авторизация получена."), tr("Можно закрыть это окно и вернуться в терминал."))
        else:
            body = "<html><body>{}</body></html>".format(tr("Forge ожидает ответа EVE SSO…"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args: object) -> None:  # тишина в консоли
        pass


def run_auth_flow(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    callback_port: int,
    client: httpx.Client | None = None,
    open_browser: Callable[[str], bool] = webbrowser.open,
    timeout: float = 300.0,
) -> TokenSet:
    """Полный интерактивный PKCE-флоу. Возвращает TokenSet. Требует браузер."""
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(client_id, redirect_uri, scopes, state, challenge)

    _CallbackHandler.code = None
    _CallbackHandler.state = None
    httpd = _make_server(callback_port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    open_browser(url)
    deadline = time.time() + timeout
    while _CallbackHandler.code is None and time.time() < deadline:
        time.sleep(0.2)
    httpd.shutdown()
    httpd.server_close()

    if _CallbackHandler.code is None:
        raise TimeoutError(tr("Не дождались ответа SSO на callback."))
    if _CallbackHandler.state != state:
        raise ValueError(tr("State не совпал — возможная CSRF, прерываю."))

    own = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        return exchange_code(client, client_id, _CallbackHandler.code, verifier)
    finally:
        if own:
            client.close()


class _DualStackServer(http.server.HTTPServer):
    """Слушает и IPv6, и IPv4 — на Windows ``localhost`` часто резолвится в ::1,
    а EVE редиректит именно на localhost; чистый IPv4-сервер давал connection refused."""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


def _make_server(port: int) -> http.server.HTTPServer:
    try:
        return _DualStackServer(("", port), _CallbackHandler)
    except OSError:
        # Фолбэк на IPv4, если IPv6 недоступен.
        return http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
