"""ingest — внешние данные → БД через storage. Только здесь и в sync разрешена сеть.

Подмодули: ``sde`` (статика), ``market`` (public Jita), ``industry`` (cost indices),
``character`` (SSO, часть B). ``esi`` — общий клиент ESI.
"""

from . import character, esi, industry, market, sde

__all__ = ["character", "esi", "industry", "market", "sde"]
