"""Репозитории: upsert идемпотентен (без дублей), обновляет, читает."""

from __future__ import annotations

from forge.storage import repositories as repo


def _type_row(type_id: int, name: str, base_price: float = 1.0) -> dict:
    return {
        "type_id": type_id,
        "name": name,
        "group_id": None,
        "category_id": None,
        "volume": None,
        "packaged_volume": None,
        "base_price": base_price,
        "market_group_id": None,
        "is_published": 1,
    }


def test_upsert_no_duplicates_on_repeat(conn):
    types = repo.sde_types(conn)
    rows = [_type_row(34, "Tritanium"), _type_row(35, "Pyerite")]
    types.upsert_many(rows)
    types.upsert_many(rows)  # повтор — не должно задвоить
    conn.commit()
    assert types.count() == 2


def test_upsert_updates_existing(conn):
    types = repo.sde_types(conn)
    types.upsert_many([_type_row(34, "Tritanium", base_price=1.0)])
    types.upsert_many([_type_row(34, "Tritanium", base_price=9.0)])
    conn.commit()
    row = types.get(type_id=34)
    assert row["base_price"] == 9.0
    assert types.count() == 1


def test_composite_pk_upsert(conn):
    ci = repo.system_cost_indices(conn)
    base = {"system_id": 30000142, "activity_id": 1, "cost_index": 0.05, "updated_at": "t"}
    ci.upsert_many([base])
    ci.upsert_many([{**base, "cost_index": 0.07}])
    conn.commit()
    assert ci.count() == 1
    assert ci.get(system_id=30000142, activity_id=1)["cost_index"] == 0.07


def test_resolve_system_and_region(conn):
    repo.sde_systems(conn).upsert_many(
        [{"system_id": 30000772, "name": "C-J6MT", "region_id": 10000010, "security": -0.3}]
    )
    conn.commit()
    assert repo.resolve_system_id(conn, "c-j6mt") == 30000772  # NOCASE
    assert repo.resolve_region_id_by_system(conn, "C-J6MT") == 10000010
    assert repo.resolve_system_id(conn, "Nowhere") is None


def test_empty_upsert_returns_zero(conn):
    assert repo.sde_types(conn).upsert_many([]) == 0
