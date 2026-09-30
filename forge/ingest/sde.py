"""Загрузка статики SDE из SQLite-дампа Fuzzwork в таблицы ``sde_*``.

Поток: скачать сжатый дамп → распаковать → перелить нужные таблицы с маппингом
имён колонок Fuzzwork → наши ``sde_*`` через ``repositories.upsert`` (идемпотентно).

В тестах ``download_sde`` не вызывается; ``load_from_sqlite`` принимает путь к крошечной
SQLite-БД с таблицами в именовании Fuzzwork.
"""

from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path

import httpx

from ..storage import repositories as repo
from ..storage.db import transaction

# Fuzzwork отдаёт SDE как gzip-сжатый SQLite-дамп.
FUZZWORK_SDE_URL = "https://www.fuzzwork.co.uk/dump/latest-sqlite.db.gz"

# Только известные бонусные dogma-атрибуты (материал/время ригов реакций и инженерных ригов +
# их security-модификаторы — риги реакций/переработки дают БОЛЬШЕ базового бонуса в null-sec/WH;
# + встроенные бонусы САМИХ структур: strEngMatBonus/strEngCostBonus/strEngTimeBonus
# (Engineering Complex — производство/инвента/копия) и strReactionTimeMultiplier (только
# Tatara — реакции), см. forge/core/rigs.py; + manufactureTimePerLevel — «специализированные»
# скиллы сокращения времени производства (Electronic Engineering и ещё 25 подобных — только
# для чертежей, которые их требуют, см. forge/planner/timing.py) — не весь dgmTypeAttributes
# (там ~640К строк, почти всё нерелевантно).
_BONUS_ATTRIBUTE_IDS = (2593, 2594, 2595, 2713, 2714, 2355, 2356, 2357, 2600, 2601, 2602, 2721, 1982)
_BONUS_ATTRIBUTE_IDS_SQL = ",".join(str(a) for a in _BONUS_ATTRIBUTE_IDS)

# Маппинг: (целевая таблица, pk, SQL по дампу Fuzzwork с алиасами под наши колонки).
_MAPPINGS: list[tuple[str, list[str], str]] = [
    (
        "sde_categories",
        ["category_id"],
        "SELECT categoryID AS category_id, categoryName AS name FROM invCategories",
    ),
    (
        "sde_groups",
        ["group_id"],
        "SELECT groupID AS group_id, categoryID AS category_id, groupName AS name "
        "FROM invGroups",
    ),
    (
        "sde_types",
        ["type_id"],
        "SELECT t.typeID AS type_id, t.typeName AS name, t.groupID AS group_id, "
        "       g.categoryID AS category_id, t.volume AS volume, "
        "       NULL AS packaged_volume, t.basePrice AS base_price, "
        "       t.marketGroupID AS market_group_id, t.published AS is_published "
        "FROM invTypes t LEFT JOIN invGroups g ON g.groupID = t.groupID",
    ),
    (
        "sde_systems",
        ["system_id"],
        "SELECT solarSystemID AS system_id, solarSystemName AS name, "
        "       regionID AS region_id, security AS security FROM mapSolarSystems",
    ),
    (
        "sde_blueprints",
        ["blueprint_type_id"],
        "SELECT typeID AS blueprint_type_id, maxProductionLimit AS max_production_limit "
        "FROM industryBlueprints",
    ),
    (
        "sde_stations",
        ["station_id"],
        "SELECT stationID AS station_id, stationName AS name, solarSystemID AS system_id "
        "FROM staStations",
    ),
    (
        "sde_reprocessing_materials",
        ["type_id", "material_type_id"],
        "SELECT tm.typeID AS type_id, t.portionSize AS portion_size, "
        "       tm.materialTypeID AS material_type_id, tm.quantity AS quantity "
        "FROM invTypeMaterials tm JOIN invTypes t ON t.typeID = tm.typeID",
    ),
    (
        "sde_blueprint_activities",
        ["blueprint_type_id", "activity_id"],
        "SELECT typeID AS blueprint_type_id, activityID AS activity_id, "
        "       time AS time_seconds FROM industryActivity",
    ),
    (
        "sde_blueprint_materials",
        ["blueprint_type_id", "activity_id", "material_type_id"],
        "SELECT typeID AS blueprint_type_id, activityID AS activity_id, "
        "       materialTypeID AS material_type_id, quantity AS quantity "
        "FROM industryActivityMaterials",
    ),
    (
        "sde_blueprint_products",
        ["blueprint_type_id", "activity_id", "product_type_id"],
        # probability вынесена Fuzzwork в отдельную industryActivityProbabilities;
        # для обычных продуктов её нет → COALESCE до 1.0.
        "SELECT p.typeID AS blueprint_type_id, p.activityID AS activity_id, "
        "       p.productTypeID AS product_type_id, p.quantity AS quantity, "
        "       COALESCE(pr.probability, 1.0) AS probability "
        "FROM industryActivityProducts p "
        "LEFT JOIN industryActivityProbabilities pr "
        "  ON pr.typeID = p.typeID AND pr.activityID = p.activityID "
        "  AND pr.productTypeID = p.productTypeID",
    ),
    (
        "sde_blueprint_skills",
        ["blueprint_type_id", "activity_id", "skill_type_id"],
        "SELECT typeID AS blueprint_type_id, activityID AS activity_id, "
        "       skillID AS skill_type_id, level AS level FROM industryActivitySkills",
    ),
    (
        "sde_dogma_attribute_types",
        ["attribute_id"],
        "SELECT attributeID AS attribute_id, attributeName AS name, "
        "       displayName AS display_name, stackable AS stackable, "
        "       highIsGood AS high_is_good "
        f"FROM dgmAttributeTypes WHERE attributeID IN ({_BONUS_ATTRIBUTE_IDS_SQL})",
    ),
    (
        "sde_dogma_type_attributes",
        ["type_id", "attribute_id"],
        "SELECT typeID AS type_id, attributeID AS attribute_id, valueFloat AS value_float "
        f"FROM dgmTypeAttributes WHERE attributeID IN ({_BONUS_ATTRIBUTE_IDS_SQL})",
    ),
]


def download_sde(
    dest_path: str | Path,
    url: str = FUZZWORK_SDE_URL,
    client: httpx.Client | None = None,
) -> Path:
    """Скачать и распаковать дамп Fuzzwork в ``dest_path`` (.sqlite). Возвращает путь.

    Дамп большой (~сотни МБ). Стримим и распаковываем bz2 на лету.
    """
    dest = Path(dest_path)
    own_client = client is None
    client = client or httpx.Client(timeout=300.0, follow_redirects=True)
    try:
        # 16 + MAX_WBITS → потоковая распаковка gzip.
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as out:
                for chunk in resp.iter_bytes():
                    out.write(decompressor.decompress(chunk))
                out.write(decompressor.flush())
    finally:
        if own_client:
            client.close()
    return dest


def _table_exists(src: sqlite3.Connection, name: str) -> bool:
    row = src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def load_from_sqlite(conn: sqlite3.Connection, src_path: str | Path) -> dict[str, int]:
    """Перелить таблицы из дампа Fuzzwork (``src_path``) в ``sde_*``.

    Возвращает число строк по каждой целевой таблице. Отсутствующие в дампе таблицы
    пропускаются (полезно для частичных тестовых фикстур).
    """
    src = sqlite3.connect(str(src_path))
    src.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        with transaction(conn):
            for target, pk_cols, sql in _MAPPINGS:
                source_table = _source_table_for(sql)
                if not _table_exists(src, source_table):
                    continue
                rows = [dict(r) for r in src.execute(sql)]
                counts[target] = repo.upsert(conn, target, rows, pk_cols)
    finally:
        src.close()
    return counts


def sync(
    conn: sqlite3.Connection,
    workdir: str | Path = ".",
    url: str = FUZZWORK_SDE_URL,
) -> dict[str, int]:
    """Полный синк SDE: скачать дамп и залить в БД. Возвращает счётчики строк."""
    sde_file = Path(workdir) / "sde-latest.sqlite"
    download_sde(sde_file, url=url)
    return load_from_sqlite(conn, sde_file)


def _source_table_for(sql: str) -> str:
    """Достать имя исходной таблицы из ``... FROM <table> ...`` (первое вхождение)."""
    tokens = sql.replace("\n", " ").split()
    idx = [t.upper() for t in tokens].index("FROM")
    return tokens[idx + 1]
