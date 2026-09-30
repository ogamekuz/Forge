"""Персональные данные чара через авторизованный ESI: blueprints / skills / assets /
industry jobs / wallet.

ME/TE/runs живут на экземпляре блупринта (в этих данных), а не на типе — это критично
для будущих расчётов. Парсеры отделены от сети и покрыты тестами.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC
from typing import Any

from ...storage import repositories as repo
from ...storage.db import transaction
from ..esi import EsiClient
from . import tokens


# --- парсеры (без сети) -----------------------------------------------------
def parse_blueprints(character_id: int, data: list[dict[str, Any]]) -> list[dict]:
    """ESI blueprints → строки character_blueprints.

    quantity: -1 = BPO (оригинал-синглтон), -2 = BPC (копия); иначе стак копий.
    runs: -1 у BPO; у BPC — остаток прогонов.
    """
    rows = []
    for bp in data:
        quantity = bp.get("quantity", -1)
        rows.append(
            {
                "item_id": bp["item_id"],
                "character_id": character_id,
                "type_id": bp["type_id"],
                "location_id": bp.get("location_id"),
                "me": bp.get("material_efficiency", 0),
                "te": bp.get("time_efficiency", 0),
                "quantity": quantity,
                "runs": bp.get("runs", -1),
                "is_copy": 1 if quantity == -2 else 0,
            }
        )
    return rows


def parse_skills(character_id: int, data: dict[str, Any]) -> list[dict]:
    """ESI skills → строки character_skills."""
    return [
        {
            "character_id": character_id,
            "skill_type_id": s["skill_id"],
            "active_level": s.get("active_skill_level", 0),
        }
        for s in data.get("skills", [])
    ]


def parse_assets(character_id: int, data: list[dict[str, Any]]) -> list[dict]:
    """ESI assets → строки character_assets."""
    return [
        {
            "item_id": a["item_id"],
            "character_id": character_id,
            "type_id": a["type_id"],
            "location_id": a.get("location_id"),
            "quantity": a.get("quantity", 0),
        }
        for a in data
    ]


def parse_asset_flags(character_id: int, data: list[dict[str, Any]]) -> list[dict]:
    """ESI assets → строки character_asset_flags (``location_flag`` из ТОГО ЖЕ ответа ESI —
    отдельный сетевой вызов не нужен). См. ``core.stock.is_fitted_slot``."""
    return [
        {
            "item_id": a["item_id"],
            "character_id": character_id,
            "location_flag": a.get("location_flag"),
        }
        for a in data
    ]


def parse_jobs(character_id: int, data: list[dict[str, Any]]) -> list[dict]:
    """ESI industry jobs → строки character_industry_jobs."""
    return [
        {
            "job_id": j["job_id"],
            "character_id": character_id,
            "activity_id": j.get("activity_id"),
            "blueprint_type_id": j.get("blueprint_type_id"),
            "product_type_id": j.get("product_type_id"),
            "runs": j.get("runs"),
            "cost": j.get("cost"),
            "status": j.get("status"),
            "start_date": j.get("start_date"),
            "end_date": j.get("end_date"),
        }
        for j in data
    ]


# --- синки (сеть) -----------------------------------------------------------
def sync_character_data(
    conn: sqlite3.Connection,
    esi: EsiClient,
    character_id: int,
    name: str,
    scopes: list[str],
    token: str,
) -> dict[str, int]:
    """Залить все персональные данные одного чара. Возвращает счётчики строк.

    Дочерние таблицы (skills/blueprints/assets/jobs) перезаписываются целиком —
    проданные/перемещённые предметы не должны оставаться «призраками».
    """
    base = f"/characters/{character_id}"
    bp = esi.get_paginated(f"{base}/blueprints/", token=token)
    sk = esi.get(f"{base}/skills/", token=token)
    assets = esi.get_paginated(f"{base}/assets/", token=token)
    jobs = esi.get(f"{base}/industry/jobs/", params={"include_completed": "true"}, token=token)
    wallet = esi.get(f"{base}/wallet/", token=token)

    counts: dict[str, int] = {}
    from datetime import datetime

    now = datetime.now(UTC).isoformat()
    with transaction(conn):
        repo.characters(conn).upsert_many(
            [
                {
                    "character_id": character_id,
                    "name": name,
                    "refresh_token": tokens.MARKER,  # сам токен — в Credential Manager
                    "scopes": " ".join(scopes),
                    "wallet_balance": float(wallet.data),
                    "updated_at": now,
                }
            ]
        )
        counts["blueprints"] = repo.replace_character_children(
            conn, "character_blueprints", character_id, parse_blueprints(character_id, bp.data)
        )
        counts["skills"] = repo.replace_character_children(
            conn, "character_skills", character_id, parse_skills(character_id, sk.data)
        )
        counts["assets"] = repo.replace_character_children(
            conn, "character_assets", character_id, parse_assets(character_id, assets.data)
        )
        repo.replace_character_children(
            conn, "character_asset_flags", character_id,
            parse_asset_flags(character_id, assets.data),
        )
        counts["jobs"] = repo.replace_character_children(
            conn, "character_industry_jobs", character_id, parse_jobs(character_id, jobs.data)
        )
    return counts
