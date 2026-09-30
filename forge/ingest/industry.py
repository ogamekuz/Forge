"""Загрузка system cost indices — основа стоимости установки джоба (EIV).

ESI ``GET /industry/systems/`` отдаёт индексы по всем системам и активностям.
Складываем все; система GPLB-C среди них обязательна.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from ..storage import repositories as repo
from ..storage.db import transaction
from ..storage.models import CostIndexRow
from .esi import EsiClient

# Маппинг строковых activity из ESI в наши activity_id (см. schema.sql).
_ACTIVITY_IDS = {
    "manufacturing": 1,
    "researching_time_efficiency": 3,
    "researching_material_efficiency": 4,
    "copying": 5,
    "invention": 8,
    "reaction": 11,
}


def parse_cost_indices(data: list[dict[str, Any]]) -> list[CostIndexRow]:
    """Ответ ESI ``/industry/systems/`` → строки ``system_cost_indices``."""
    now = datetime.now(UTC).isoformat()
    rows: list[CostIndexRow] = []
    for system in data:
        system_id = system["solar_system_id"]
        for ci in system.get("cost_indices", []):
            activity_id = _ACTIVITY_IDS.get(ci["activity"])
            if activity_id is None:
                continue  # неизвестная активность — пропускаем
            rows.append(
                CostIndexRow(
                    system_id=system_id,
                    activity_id=activity_id,
                    cost_index=ci["cost_index"],
                    updated_at=now,
                )
            )
    return rows


def sync(conn: sqlite3.Connection, esi: EsiClient) -> tuple[int, str | None]:
    """Залить cost-индексы по всем системам. Возвращает (число строк, expires)."""
    resp = esi.get("/industry/systems/")
    rows = parse_cost_indices(resp.data)
    with transaction(conn):
        total = repo.system_cost_indices(conn).upsert_many(r.model_dump() for r in rows)
    return total, resp.expires
