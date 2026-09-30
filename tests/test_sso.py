"""EVE SSO: PKCE, authorize URL, разбор JWT, обмен/refresh токенов (мокнуто)."""

from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest

from forge.ingest.character import sso


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _fake_jwt(payload: dict) -> str:
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps(payload).encode())
    return f"{header}.{body}.signature"


def test_generate_pkce_challenge_matches_verifier():
    verifier, challenge = sso.generate_pkce()
    expected = _b64url(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge


def test_build_authorize_url():
    url = sso.build_authorize_url(
        "CID", "http://localhost:8765/callback", ["a.v1", "b.v1"], "STATE", "CHAL"
    )
    assert url.startswith(sso.AUTHORIZE_URL)
    assert "client_id=CID" in url
    assert "code_challenge_method=S256" in url
    assert "scope=a.v1+b.v1" in url
    assert "state=STATE" in url


def test_parse_claims():
    token = _fake_jwt(
        {
            "iss": "login.eveonline.com",
            "sub": "CHARACTER:EVE:91234567",
            "name": "Igor Pilot",
            "scp": ["esi-skills.read_skills.v1", "esi-assets.read_assets.v1"],
        }
    )
    claims = sso.parse_claims(token)
    assert claims.character_id == 91234567
    assert claims.name == "Igor Pilot"
    assert "esi-skills.read_skills.v1" in claims.scopes


def test_parse_claims_single_scope_string():
    token = _fake_jwt({"iss": "login.eveonline.com", "sub": "CHARACTER:EVE:1", "name": "X", "scp": "one.v1"})
    assert sso.parse_claims(token).scopes == ["one.v1"]


def test_parse_claims_bad_issuer():
    token = _fake_jwt({"iss": "evil.example", "sub": "CHARACTER:EVE:1", "name": "X"})
    with pytest.raises(ValueError):
        sso.parse_claims(token)


def test_exchange_code_posts_form():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 1200}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ts = sso.exchange_code(client, "CID", "CODE", "VERIFIER")
    assert ts.access_token == "AT" and ts.refresh_token == "RT"
    assert captured["url"] == sso.TOKEN_URL
    assert "grant_type=authorization_code" in captured["body"]
    assert "code_verifier=VERIFIER" in captured["body"]


def test_refresh_token():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "grant_type=refresh_token" in request.content.decode()
        return httpx.Response(
            200, json={"access_token": "AT2", "refresh_token": "RT2", "expires_in": 1200}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ts = sso.refresh_token(client, "CID", "OLD_RT")
    assert ts.access_token == "AT2" and ts.refresh_token == "RT2"
    assert not ts.is_expired()
