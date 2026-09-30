"""Конфиг: парсинг TOML, alias freight, резолв локаций из SDE."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge.storage import repositories as repo

SAMPLE = """
db_path = "x.db"

[locations.jita]
name = "Jita"
system_id = 30000142
region_id = 10000002

[locations.gplb_c]
name = "GPLB-C"

[structures]
taj_mahgoon_market_id = 123

[[characters]]
name = "Main"
production = true

[[freight_routes]]
from = "jita"
to = "c_j6mt"
mode = "per_m3"
isk_per_m3 = 850.0
"""


def test_loads_parses_sections():
    cfg = config_mod.loads(SAMPLE)
    assert cfg.db_path == "x.db"
    assert cfg.locations["jita"].system_id == 30000142
    assert cfg.structures.taj_mahgoon_market_id == 123
    assert cfg.characters[0].name == "Main" and cfg.characters[0].production is True


def test_freight_route_alias():
    cfg = config_mod.loads(SAMPLE)
    route = cfg.freight_routes[0]
    assert route.from_ == "jita"
    assert route.to == "c_j6mt"
    assert route.isk_per_m3 == 850.0


def test_resolve_locations_fills_ids_from_sde(conn):
    repo.sde_systems(conn).upsert_many(
        [{"system_id": 30002776, "name": "GPLB-C", "region_id": 10000051, "security": -0.5}]
    )
    conn.commit()
    cfg = config_mod.loads(SAMPLE)
    config_mod.resolve_locations(cfg, conn)
    assert cfg.locations["gplb_c"].system_id == 30002776
    assert cfg.locations["gplb_c"].region_id == 10000051
    # Уже заданный Jita не меняется.
    assert cfg.locations["jita"].system_id == 30000142


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        config_mod.load(tmp_path / "nope.toml")


def test_save_roundtrip(tmp_path):
    cfg = config_mod.loads(SAMPLE)
    cfg.industry.scc_surcharge = 0.04
    cfg.industry.broker_fee = 0.015
    path = tmp_path / "out.toml"
    config_mod.save(cfg, path)
    reloaded = config_mod.load(path)
    assert reloaded.industry.scc_surcharge == 0.04
    assert reloaded.industry.broker_fee == 0.015
    assert reloaded.freight_routes[0].from_ == "jita"   # alias сохранён
    assert reloaded.locations["jita"].system_id == 30000142
