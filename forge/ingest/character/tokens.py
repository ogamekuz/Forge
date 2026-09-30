"""Хранение refresh-токенов EVE SSO в Windows Credential Manager (через keyring).

Секрет НЕ попадает ни в git, ни в файл БД: keyring пишет в системное хранилище
(на Windows — Credential Manager поверх DPAPI). Поэтому колонка ``characters.refresh_token``
хранит не токен, а состояние входа: маркер ``MARKER`` (токен в keyring) или ``MARKER_RELOGIN``
(EVE SSO его не принял — нужен новый вход), а сам токен берётся отсюда.

Запись — под client_id приложения (``service_for``): refresh-токен EVE привязан к приложению,
и копия Forge с ДРУГИМ client_id (напр. сборка для раздачи), пишущая в общую запись, ломала бы
вход этой копии (``400 invalid_grant``). Общая запись без client_id (``SERVICE``; её же читают
другие копии Forge с тем же client_id) — запасная: порядок чтения и её обновления решает
``sync.ensure_access_token``.

``backend`` инъектируется для тестов (чтобы не трогать реальное хранилище ОС).
"""

from __future__ import annotations

from typing import Protocol

SERVICE = "forge:esi:refresh_token"
MARKER = "keyring"
MARKER_RELOGIN = "relogin"


class MissingToken(LookupError):
    """В keyring нет refresh-токена персонажа — нужен вход через EVE SSO."""


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


def _default_backend() -> KeyringBackend:
    import keyring  # ленивый импорт — упрощает тесты без установленного backend

    return keyring


def service_for(client_id: str = "") -> str:
    """Имя записи keyring: под client_id приложения; пустой — общая запись без client_id."""
    return f"{SERVICE}:{client_id}" if client_id else SERVICE


def save(character_id: int, refresh_token: str, backend: KeyringBackend | None = None, *,
         client_id: str = "") -> None:
    (backend or _default_backend()).set_password(service_for(client_id), str(character_id), refresh_token)


def load(character_id: int, backend: KeyringBackend | None = None, *, client_id: str = "") -> str | None:
    return (backend or _default_backend()).get_password(service_for(client_id), str(character_id))


def delete(character_id: int, backend: KeyringBackend | None = None, *, client_id: str = "") -> None:
    try:
        (backend or _default_backend()).delete_password(service_for(client_id), str(character_id))
    except Exception:  # noqa: S110 — нет записи, не считаем ошибкой
        pass
