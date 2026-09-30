"""Загрузка SDE из дампа Fuzzwork (мини-фикстура SQLite, без сети)."""

from __future__ import annotations

import gzip
import sqlite3

import httpx

from forge.ingest import sde
from forge.storage import repositories as repo


def _make_fuzzwork_dump(path) -> None:
    """Крошечный дамп в именовании Fuzzwork с подмножеством таблиц."""
    db = sqlite3.connect(str(path))
    db.executescript(
        """
        CREATE TABLE invCategories (categoryID INT, categoryName TEXT);
        CREATE TABLE invGroups (groupID INT, categoryID INT, groupName TEXT);
        CREATE TABLE invTypes (typeID INT, typeName TEXT, groupID INT, volume REAL,
                               basePrice REAL, marketGroupID INT, published INT, portionSize INT);
        CREATE TABLE mapSolarSystems (solarSystemID INT, solarSystemName TEXT,
                                      regionID INT, security REAL);
        CREATE TABLE industryBlueprints (typeID INT, maxProductionLimit INT);
        CREATE TABLE industryActivityProducts (typeID INT, activityID INT,
                                               productTypeID INT, quantity INT);
        CREATE TABLE industryActivityProbabilities (typeID INT, activityID INT,
                                                    productTypeID INT, probability REAL);
        CREATE TABLE staStations (stationID INT, stationName TEXT, solarSystemID INT);
        CREATE TABLE invTypeMaterials (typeID INT, materialTypeID INT, quantity INT);

        INSERT INTO invCategories VALUES (6, 'Ship'), (9, 'Blueprint');
        INSERT INTO invGroups VALUES (25, 6, 'Frigate'), (105, 9, 'Frigate Blueprint');
        INSERT INTO invTypes VALUES
            (587, 'Rifter', 25, 27289.0, 100000.0, 61, 1, 1),
            (688, 'Rifter Blueprint', 105, 0.01, 0.0, NULL, 1, 1),
            (34, 'Tritanium', NULL, 0.01, 0.0, NULL, 1, 1);
        INSERT INTO mapSolarSystems VALUES (30000142, 'Jita', 10000002, 0.9);
        INSERT INTO industryBlueprints VALUES (688, 300);
        INSERT INTO industryActivityProducts VALUES (688, 1, 587, 1);
        INSERT INTO staStations VALUES
            (60003760, 'Jita 4 - Moon 4 - Caldari Navy Assembly Plant', 30000142);
        INSERT INTO invTypeMaterials VALUES (587, 34, 3000);
        """
    )
    db.commit()
    db.close()


def test_load_from_sqlite_fills_sde_tables(conn, tmp_path):
    dump = tmp_path / "fuzz.sqlite"
    _make_fuzzwork_dump(dump)

    counts = sde.load_from_sqlite(conn, dump)
    conn.commit()

    assert counts["sde_types"] == 3
    assert repo.sde_types(conn).count() == 3
    assert repo.sde_systems(conn).count() == 1
    assert repo.sde_blueprints(conn).count() == 1
    assert repo.sde_stations(conn).count() == 1
    station = repo.sde_stations(conn).get(station_id=60003760)
    assert station["name"] == "Jita 4 - Moon 4 - Caldari Navy Assembly Plant"
    assert station["system_id"] == 30000142

    assert repo.sde_reprocessing_materials(conn).count() == 1
    reproc = repo.sde_reprocessing_materials(conn).get(type_id=587, material_type_id=34)
    assert reproc["quantity"] == 3000
    assert reproc["portion_size"] == 1  # взят из invTypes.portionSize джойном

    # category_id подтянут join'ом invTypes→invGroups.
    rifter = repo.sde_types(conn).get(type_id=587)
    assert rifter["name"] == "Rifter"
    assert rifter["category_id"] == 6

    # Резолвер имён работает поверх залитой SDE.
    assert repo.resolve_system_id(conn, "Jita") == 30000142

    # probability берётся из LEFT JOIN; для обычного продукта по умолчанию 1.0.
    prod = repo.sde_blueprint_products(conn).get(
        blueprint_type_id=688, activity_id=1, product_type_id=587
    )
    assert prod["probability"] == 1.0


def test_download_sde_decompresses_gzip(tmp_path):
    """download_sde должен потоково распаковывать gzip-дамп Fuzzwork."""
    src = tmp_path / "src.sqlite"
    _make_fuzzwork_dump(src)
    raw = src.read_bytes()
    gz = gzip.compress(raw)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith(".db.gz")
        return httpx.Response(200, content=gz)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = sde.download_sde(tmp_path / "out.sqlite", url=sde.FUZZWORK_SDE_URL, client=client)

    # Распакованный файл — валидный SQLite с теми же данными.
    assert out.read_bytes() == raw
    db = sqlite3.connect(str(out))
    assert db.execute("SELECT typeName FROM invTypes WHERE typeID=587").fetchone()[0] == "Rifter"
    db.close()


def test_load_skips_missing_tables(conn, tmp_path):
    """Частичный дамп (нет industryActivityMaterials и пр.) не должен падать."""
    dump = tmp_path / "partial.sqlite"
    _make_fuzzwork_dump(dump)
    counts = sde.load_from_sqlite(conn, dump)
    assert "sde_blueprint_materials" not in counts  # таблицы не было в дампе


def test_load_dogma_attributes_filters_to_known_bonus_ids(conn, tmp_path):
    """Ингест dogma-таблиц должен брать ТОЛЬКО известные бонусные attribute_id (риги реакций/
    инженерных ригов), а не весь dgmTypeAttributes (там сотни тысяч нерелевантных строк)."""
    dump = tmp_path / "fuzz_dogma.sqlite"
    _make_fuzzwork_dump(dump)
    db = sqlite3.connect(str(dump))
    db.executescript(
        """
        CREATE TABLE dgmAttributeTypes (attributeID INT, attributeName TEXT, displayName TEXT,
                                        stackable INT, highIsGood INT);
        CREATE TABLE dgmTypeAttributes (typeID INT, attributeID INT, valueFloat REAL);
        INSERT INTO dgmAttributeTypes VALUES
            (2594, 'attributeEngRigMatBonus', 'Material Reduction Bonus', 1, 0),
            (9999, 'someUnrelatedAttribute', 'Unrelated', 0, 1);
        INSERT INTO dgmTypeAttributes VALUES
            (46486, 2594, -2.0),
            (587, 9999, 123.0);
        """
    )
    db.commit()
    db.close()

    counts = sde.load_from_sqlite(conn, dump)
    conn.commit()

    assert counts["sde_dogma_attribute_types"] == 1  # только 2594, НЕ 9999
    assert counts["sde_dogma_type_attributes"] == 1  # только строка с attribute_id=2594

    rows = conn.execute("SELECT attribute_id FROM sde_dogma_attribute_types").fetchall()
    assert [r[0] for r in rows] == [2594]
    rows = conn.execute("SELECT type_id, attribute_id, value_float FROM sde_dogma_type_attributes").fetchall()
    assert [tuple(r) for r in rows] == [(46486, 2594, -2.0)]
