"""character — персональные данные через EVE SSO (OAuth2 PKCE). Часть B.

Подмодули: ``sso`` (PKCE-флоу/токены/JWT), ``tokens`` (хранение refresh в keyring),
``data`` (blueprints/skills/assets/jobs/wallet), ``structure_market`` (рынок C-J6MT),
``structures`` (имена/системы структур игроков для склада), ``sync`` (оркестрация по чарам).
"""

from . import data, sso, structure_market, structures, sync, tokens
from .sync import add_character, list_characters
from .sync import sync as sync_all

__all__ = [
    "add_character",
    "data",
    "list_characters",
    "sso",
    "structure_market",
    "structures",
    "sync",
    "sync_all",
    "tokens",
]
