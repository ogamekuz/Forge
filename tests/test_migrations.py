"""Схема создаётся, версия проставлена, init_db идемпотентен."""

from __future__ import annotations

from forge import storage
from forge.storage.migrations import current_version, init_db
from forge.storage.schema import SCHEMA_VERSION


def _tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_init_creates_tables_and_version(conn):
    tables = _tables(conn)
    for expected in (
        "sde_types",
        "sde_blueprint_materials",
        "sde_dogma_attribute_types",
        "sde_dogma_type_attributes",
        "sde_stations",
        "sde_reprocessing_materials",
        "character_asset_flags",
        "universe_structures",
        "market_history",
        "market_adjusted_prices",
        "system_cost_indices",
        "characters",
        "sync_state",
        "schema_version",
    ):
        assert expected in tables
    assert current_version(conn) == SCHEMA_VERSION
    assert SCHEMA_VERSION == 6  # поднято при добавлении universe_structures (Forge 3.0)


def test_init_is_idempotent():
    c = storage.connect(":memory:")
    assert init_db(c) == SCHEMA_VERSION
    # Повторный вызов не падает и не плодит версии.
    assert init_db(c) == SCHEMA_VERSION
    rows = c.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
    assert rows["n"] == 1
    c.close()
