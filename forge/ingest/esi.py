"""Обёртка над ESI (EVE Swagger Interface).

Отвечает за устойчивость к API: уважение cache-заголовков (``Expires``), пагинацию по
``X-Pages`` и exponential backoff на error limit (HTTP 420) и rate limit (429).

Клиент принимает готовый ``httpx.Client`` — в тестах туда подставляется
``httpx.MockTransport`` (живых вызовов в тестах нет).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any, Self

import httpx

from ..i18n import tr

ESI_BASE = "https://esi.evetech.net"
USER_AGENT = "forge/0.1 (personal EVE industry helper)"

# Параметры backoff на 420/429.
MAX_RETRIES = 5
BASE_BACKOFF = 1.0  # секунды; удваивается на каждой повторной попытке


@dataclass
class EsiResponse:
    """Результат запроса: данные + метаданные кэша/пагинации."""

    data: Any
    expires: str | None  # ISO-8601, для записи в sync_state
    pages: int


def _parse_expires(headers: httpx.Headers) -> str | None:
    """Заголовок ``Expires`` (RFC 7231) → ISO-8601 UTC, либо None."""
    raw = headers.get("expires")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


class EsiClient:
    """Тонкий клиент ESI с backoff и пагинацией."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        base_url: str = ESI_BASE,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = MAX_RETRIES,
    ):
        self._client = client or httpx.Client(
            base_url=base_url, headers={"User-Agent": USER_AGENT}, timeout=30.0
        )
        self._sleep = sleep
        self._max_retries = max_retries

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(
        self, path: str, params: dict[str, Any] | None = None, token: str | None = None
    ) -> EsiResponse:
        """GET одной страницы с retry на 420/429. ``token`` — Bearer для авторизованных эндпоинтов."""
        resp = self._request(path, params, token)
        pages = int(resp.headers.get("x-pages", "1"))
        return EsiResponse(data=resp.json(), expires=_parse_expires(resp.headers), pages=pages)

    def get_paginated(
        self, path: str, params: dict[str, Any] | None = None, token: str | None = None
    ) -> EsiResponse:
        """GET всех страниц (по ``X-Pages``); данные склеиваются в один список."""
        params = dict(params or {})
        first = self._request(path, {**params, "page": 1}, token)
        pages = int(first.headers.get("x-pages", "1"))
        data: list[Any] = list(first.json())
        expires = _parse_expires(first.headers)

        for page in range(2, pages + 1):
            resp = self._request(path, {**params, "page": page}, token)
            data.extend(resp.json())

        return EsiResponse(data=data, expires=expires, pages=pages)

    # --- внутреннее ---------------------------------------------------------
    def _request(
        self, path: str, params: dict[str, Any] | None, token: str | None = None
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        backoff = BASE_BACKOFF
        last_exc: Exception | None = None
        for _attempt in range(self._max_retries):
            resp = self._client.get(path, params=params, headers=headers)
            if resp.status_code in (420, 429):
                # Error/rate limit: ждём, ориентируясь на reset-заголовок, если он есть.
                reset = resp.headers.get("x-esi-error-limit-reset")
                wait = float(reset) if reset else backoff
                self._sleep(wait)
                backoff *= 2
                last_exc = httpx.HTTPStatusError(
                    tr("ESI rate/error limit ({code}) на {path}", code=resp.status_code, path=path),
                    request=resp.request,
                    response=resp,
                )
                continue
            # Проактивно тормозим у края error-limit: если остаток почти исчерпан,
            # ждём окно сброса, не дожидаясь 420 (всплеск 404 по неторгуемым типам).
            remaining = resp.headers.get("x-esi-error-limit-remaining")
            reset = resp.headers.get("x-esi-error-limit-reset")
            if remaining is not None and reset is not None:
                try:
                    if int(remaining) <= 2:
                        self._sleep(float(reset))
                except ValueError:
                    pass
            resp.raise_for_status()
            return resp
        # Исчерпали попытки.
        assert last_exc is not None
        raise last_exc
