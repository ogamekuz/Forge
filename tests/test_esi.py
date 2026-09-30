"""ESI-клиент: парсинг expires, пагинация по X-Pages, backoff на 420."""

from __future__ import annotations

import httpx

from forge.ingest.esi import ESI_BASE, EsiClient


def _client(handler) -> EsiClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url=ESI_BASE)
    return EsiClient(client=http, sleep=lambda _s: None)


def test_get_parses_data_and_expires():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"a": 1}],
            headers={"Expires": "Wed, 21 Oct 2099 07:28:00 GMT", "X-Pages": "1"},
        )

    esi = _client(handler)
    resp = esi.get("/x/")
    assert resp.data == [{"a": 1}]
    assert resp.pages == 1
    assert resp.expires is not None and resp.expires.startswith("2099-10-21")


def test_pagination_concatenates_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("page", "1"))
        return httpx.Response(200, json=[page], headers={"X-Pages": "3"})

    esi = _client(handler)
    resp = esi.get_paginated("/list/")
    assert resp.data == [1, 2, 3]
    assert resp.pages == 3


def test_backoff_retries_on_420_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(420, json={}, headers={"X-Esi-Error-Limit-Reset": "0"})
        return httpx.Response(200, json={"ok": True})

    esi = _client(handler)
    resp = esi.get("/y/")
    assert resp.data == {"ok": True}
    assert calls["n"] == 2  # один ретрай
