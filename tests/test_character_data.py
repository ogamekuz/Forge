"""Данные чара: парсеры + sync_character_data через MockTransport (без сети)."""

from __future__ import annotations

import httpx

from forge.ingest.character import data
from forge.ingest.esi import ESI_BASE, EsiClient
from forge.storage import repositories as repo


def test_parse_blueprints_flags_copy_and_original():
    rows = data.parse_blueprints(
        7,
        [
            {"item_id": 1, "type_id": 100, "location_id": 60003760,
             "material_efficiency": 10, "time_efficiency": 20, "quantity": -1, "runs": -1},
            {"item_id": 2, "type_id": 101, "location_id": 60003760,
             "material_efficiency": 2, "time_efficiency": 4, "quantity": -2, "runs": 5},
        ],
    )
    bpo, bpc = rows
    assert bpo["is_copy"] == 0 and bpo["runs"] == -1 and bpo["me"] == 10
    assert bpc["is_copy"] == 1 and bpc["runs"] == 5


def test_parse_skills_and_assets_and_jobs():
    skills = data.parse_skills(7, {"skills": [{"skill_id": 3380, "active_skill_level": 5}]})
    assert skills[0] == {"character_id": 7, "skill_type_id": 3380, "active_level": 5}

    assets = data.parse_assets(7, [{"item_id": 9, "type_id": 34, "location_id": 60003760, "quantity": 1000}])
    assert assets[0]["quantity"] == 1000

    flags = data.parse_asset_flags(
        7, [{"item_id": 9, "type_id": 34, "location_id": 60003760, "quantity": 1000, "location_flag": "Hangar"}]
    )
    assert flags[0] == {"item_id": 9, "character_id": 7, "location_flag": "Hangar"}

    jobs = data.parse_jobs(7, [{"job_id": 50, "activity_id": 1, "blueprint_type_id": 101,
                                "product_type_id": 100, "runs": 5, "cost": 1234.5,
                                "status": "active", "start_date": "s", "end_date": "e"}])
    assert jobs[0]["job_id"] == 50 and jobs[0]["cost"] == 1234.5


def _bp_row(item_id: int, character_id: int, me: int = 0) -> dict:
    return {"item_id": item_id, "character_id": character_id, "type_id": 1000,
            "location_id": 1, "me": me, "te": 0, "quantity": -1, "runs": -1, "is_copy": 0}


def test_replace_handles_blueprint_moved_between_characters(conn):
    """Чертёж переехал с чара 1 на чара 2 (общий PK item_id) — не должно падать на UNIQUE."""
    conn.execute("INSERT INTO characters(character_id,name) VALUES (1,'A'),(2,'B')")
    repo.replace_character_children(conn, "character_blueprints", 1, [_bp_row(555, 1)])
    # тот же item_id теперь у чара 2 — INSERT OR REPLACE замещает прежнего владельца
    repo.replace_character_children(conn, "character_blueprints", 2, [_bp_row(555, 2)])
    rows = conn.execute("SELECT character_id FROM character_blueprints WHERE item_id=555").fetchall()
    assert len(rows) == 1 and rows[0]["character_id"] == 2   # принадлежит новому владельцу


def test_replace_dedupes_duplicate_item_ids_in_one_batch(conn):
    """Дубликат item_id в одной пачке (перекрытие страниц ESI на «живом» ангаре) — без падения."""
    conn.execute("INSERT INTO characters(character_id,name) VALUES (1,'A')")
    repo.replace_character_children(conn, "character_blueprints", 1, [_bp_row(7, 1, me=0), _bp_row(7, 1, me=9)])
    rows = conn.execute("SELECT me FROM character_blueprints WHERE item_id=7").fetchall()
    assert len(rows) == 1 and rows[0]["me"] == 9    # остаётся последний


def _esi_for_character(cid: int) -> EsiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/blueprints/"):
            return httpx.Response(200, json=[
                {"item_id": 1, "type_id": 100, "location_id": 1,
                 "material_efficiency": 10, "time_efficiency": 20, "quantity": -1, "runs": -1}],
                headers={"X-Pages": "1"})
        if path.endswith("/skills/"):
            return httpx.Response(200, json={"skills": [{"skill_id": 3380, "active_skill_level": 5}]})
        if path.endswith("/assets/"):
            return httpx.Response(200, json=[
                {"item_id": 9, "type_id": 34, "location_id": 1, "quantity": 1000, "location_flag": "Hangar"}],
                headers={"X-Pages": "1"})
        if path.endswith("/industry/jobs/"):
            return httpx.Response(200, json=[
                {"job_id": 50, "activity_id": 1, "blueprint_type_id": 101, "product_type_id": 100,
                 "runs": 5, "cost": 10.0, "status": "active", "start_date": "s", "end_date": "e"}])
        if path.endswith("/wallet/"):
            return httpx.Response(200, json=123456.78)
        return httpx.Response(404, json={})

    return EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))


def test_sync_character_data_writes_all(conn):
    esi = _esi_for_character(7)
    counts = data.sync_character_data(conn, esi, 7, "Igor", ["esi-skills.read_skills.v1"], "TOKEN")
    conn.commit()
    assert counts == {"blueprints": 1, "skills": 1, "assets": 1, "jobs": 1}
    assert repo.characters(conn).get(character_id=7)["wallet_balance"] == 123456.78
    assert repo.characters(conn).get(character_id=7)["refresh_token"] == "keyring"
    assert repo.character_blueprints(conn).get(item_id=1)["me"] == 10
    assert repo.character_asset_flags(conn).get(item_id=9)["location_flag"] == "Hangar"


def test_sync_character_data_replaces_children(conn):
    """Повторный синк с пустыми данными убирает прежние строки (не оставляет призраков)."""
    esi = _esi_for_character(7)
    data.sync_character_data(conn, esi, 7, "Igor", [], "TOKEN")
    conn.commit()
    assert repo.character_assets(conn).count() == 1
    assert repo.character_asset_flags(conn).count() == 1

    # Второй синк, ассеты пусты.
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/skills/"):
            return httpx.Response(200, json={"skills": []})
        if path.endswith("/wallet/"):
            return httpx.Response(200, json=0.0)
        return httpx.Response(200, json=[], headers={"X-Pages": "1"})

    esi2 = EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))
    data.sync_character_data(conn, esi2, 7, "Igor", [], "TOKEN")
    conn.commit()
    assert repo.character_assets(conn).count() == 0
