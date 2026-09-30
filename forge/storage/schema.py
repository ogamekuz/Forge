"""Схема локальной БД — SQLite-адаптация ``docs/schema.sql`` (правило 6: доменную схему
не меняем; здесь лишь перенос типов под SQLite + операционная таблица ``sync_state``).

Замены типов относительно Postgres-оригинала:
    SERIAL / BIGSERIAL   → INTEGER PRIMARY KEY AUTOINCREMENT
    TIMESTAMPTZ          → TEXT (ISO-8601)
    DOUBLE PRECISION     → REAL
    BIGINT               → INTEGER (в SQLite INTEGER до 8 байт)
    BOOLEAN              → INTEGER (0/1)
    DATE                 → TEXT (ISO-8601)
    SMALLINT             → INTEGER
"""

from __future__ import annotations

# Версия схемы — поднимать при изменениях DDL.
SCHEMA_VERSION = 6

# ---------------------------------------------------------------------------
# Доменная схема (соответствует docs/schema.sql) + операционные таблицы Forge.
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
-- =====================================================================
-- ДОМЕН 1: SDE / REFERENCE
-- =====================================================================
CREATE TABLE IF NOT EXISTS sde_categories (
    category_id  INTEGER PRIMARY KEY,
    name         TEXT
);

CREATE TABLE IF NOT EXISTS sde_groups (
    group_id     INTEGER PRIMARY KEY,
    category_id  INTEGER REFERENCES sde_categories(category_id),
    name         TEXT
);

CREATE TABLE IF NOT EXISTS sde_types (
    type_id          INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    group_id         INTEGER REFERENCES sde_groups(group_id),
    category_id      INTEGER REFERENCES sde_categories(category_id),
    volume           REAL,
    packaged_volume  REAL,
    base_price       REAL,
    market_group_id  INTEGER,
    is_published     INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_types_group    ON sde_types(group_id);
CREATE INDEX IF NOT EXISTS idx_types_category ON sde_types(category_id);

CREATE TABLE IF NOT EXISTS sde_systems (
    system_id  INTEGER PRIMARY KEY,
    name       TEXT,
    region_id  INTEGER,
    security   REAL
);
CREATE INDEX IF NOT EXISTS idx_systems_region ON sde_systems(region_id);

CREATE TABLE IF NOT EXISTS sde_blueprints (
    blueprint_type_id     INTEGER PRIMARY KEY REFERENCES sde_types(type_id),
    max_production_limit   INTEGER
);

-- NPC-станции (не игровые структуры — те не в SDE, только в ESI). Нужно для подписи в
-- Настройках («Где искать чертежи») — многие чертежи так и остаются лежать в случайной
-- NPC-станции годами, а голый location_id юзеру ничего не говорит.
CREATE TABLE IF NOT EXISTS sde_stations (
    station_id  INTEGER PRIMARY KEY,
    name        TEXT,
    system_id   INTEGER REFERENCES sde_systems(system_id)
);

-- Выход переработки (reprocessing, отдельная от чертежей механика EVE) при 100% эффективности:
-- сколько material_type_id даёт переработка ОДНОГО portion_size предмета type_id. Реальный
-- выход = floor(floor(кол-во/portion_size) × quantity × эффективность) — считает core/reprocess.py.
-- portion_size продублирован на каждой строке (не в sde_types) — новая таблица проще, чем
-- ALTER TABLE на уже существующей sde_types (миграции здесь — только CREATE TABLE IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS sde_reprocessing_materials (
    type_id            INTEGER,
    portion_size       INTEGER,
    material_type_id   INTEGER REFERENCES sde_types(type_id),
    quantity           INTEGER,
    PRIMARY KEY (type_id, material_type_id)
);
CREATE INDEX IF NOT EXISTS idx_reprocessing_type ON sde_reprocessing_materials(type_id);

CREATE TABLE IF NOT EXISTS sde_blueprint_activities (
    blueprint_type_id  INTEGER REFERENCES sde_blueprints(blueprint_type_id),
    activity_id        INTEGER,
    time_seconds       INTEGER,
    PRIMARY KEY (blueprint_type_id, activity_id)
);

CREATE TABLE IF NOT EXISTS sde_blueprint_materials (
    blueprint_type_id  INTEGER,
    activity_id        INTEGER,
    material_type_id   INTEGER REFERENCES sde_types(type_id),
    quantity           INTEGER,
    PRIMARY KEY (blueprint_type_id, activity_id, material_type_id)
);
CREATE INDEX IF NOT EXISTS idx_bp_materials_mat ON sde_blueprint_materials(material_type_id);

CREATE TABLE IF NOT EXISTS sde_blueprint_products (
    blueprint_type_id  INTEGER,
    activity_id        INTEGER,
    product_type_id    INTEGER REFERENCES sde_types(type_id),
    quantity           INTEGER,
    probability        REAL DEFAULT 1.0,
    PRIMARY KEY (blueprint_type_id, activity_id, product_type_id)
);
CREATE INDEX IF NOT EXISTS idx_bp_products_prod ON sde_blueprint_products(product_type_id, activity_id);

CREATE TABLE IF NOT EXISTS sde_blueprint_skills (
    blueprint_type_id  INTEGER,
    activity_id        INTEGER,
    skill_type_id      INTEGER REFERENCES sde_types(type_id),
    level              INTEGER,
    PRIMARY KEY (blueprint_type_id, activity_id, skill_type_id)
);

-- Dogma-атрибуты (только известные бонусные attribute_id — материал/время ригов реакций и
-- инженерных ригов, см. forge/core/rigs.py) — не весь dogma (там сотни тысяч нерелевантных строк).
CREATE TABLE IF NOT EXISTS sde_dogma_attribute_types (
    attribute_id  INTEGER PRIMARY KEY,
    name          TEXT,
    display_name  TEXT,
    stackable     INTEGER DEFAULT 0,
    high_is_good  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sde_dogma_type_attributes (
    type_id       INTEGER REFERENCES sde_types(type_id),
    attribute_id  INTEGER REFERENCES sde_dogma_attribute_types(attribute_id),
    value_float   REAL,
    PRIMARY KEY (type_id, attribute_id)
);
CREATE INDEX IF NOT EXISTS idx_dogma_type_attrs_attr ON sde_dogma_type_attributes(attribute_id);

-- =====================================================================
-- ДОМЕН 2: MARKET
-- =====================================================================
CREATE TABLE IF NOT EXISTS market_history (
    type_id      INTEGER REFERENCES sde_types(type_id),
    region_id    INTEGER,
    day          TEXT,
    average      REAL,
    highest      REAL,
    lowest       REAL,
    volume       INTEGER,
    order_count  INTEGER,
    PRIMARY KEY (type_id, region_id, day)
);
CREATE INDEX IF NOT EXISTS idx_history_type_region ON market_history(type_id, region_id, day DESC);

CREATE TABLE IF NOT EXISTS market_snapshot (
    type_id      INTEGER REFERENCES sde_types(type_id),
    region_id    INTEGER,
    sell_min     REAL,
    buy_max      REAL,
    sell_volume  INTEGER,
    buy_volume   INTEGER,
    updated_at   TEXT,
    PRIMARY KEY (type_id, region_id)
);

CREATE TABLE IF NOT EXISTS market_adjusted_prices (
    type_id         INTEGER PRIMARY KEY REFERENCES sde_types(type_id),
    adjusted_price  REAL,
    average_price   REAL,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS system_cost_indices (
    system_id    INTEGER REFERENCES sde_systems(system_id),
    activity_id  INTEGER,
    cost_index   REAL,
    updated_at   TEXT,
    PRIMARY KEY (system_id, activity_id)
);

-- =====================================================================
-- ДОМЕН 3: CHARACTER (наполняется в части B)
-- =====================================================================
CREATE TABLE IF NOT EXISTS characters (
    character_id    INTEGER PRIMARY KEY,
    name            TEXT,
    refresh_token   TEXT,
    scopes          TEXT,
    wallet_balance  REAL,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS character_skills (
    character_id   INTEGER REFERENCES characters(character_id),
    skill_type_id  INTEGER REFERENCES sde_types(type_id),
    active_level   INTEGER,
    PRIMARY KEY (character_id, skill_type_id)
);

CREATE TABLE IF NOT EXISTS character_blueprints (
    item_id       INTEGER PRIMARY KEY,
    character_id  INTEGER REFERENCES characters(character_id),
    type_id       INTEGER REFERENCES sde_types(type_id),
    location_id   INTEGER,
    me            INTEGER,
    te            INTEGER,
    quantity      INTEGER,
    runs          INTEGER,
    is_copy       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_char_bp_type ON character_blueprints(type_id);

CREATE TABLE IF NOT EXISTS character_assets (
    item_id       INTEGER PRIMARY KEY,
    character_id  INTEGER REFERENCES characters(character_id),
    type_id       INTEGER REFERENCES sde_types(type_id),
    location_id   INTEGER,
    quantity      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_char_assets_type ON character_assets(character_id, type_id);

-- ESI location_flag для строки character_assets (Cargo/Hangar/HiSlot0/MedSlot.../DroneBay и
-- т.п.) — отдельная таблица, не колонка в character_assets (та же причина, что и у
-- sde_reprocessing_materials выше: миграции здесь только CREATE TABLE IF NOT EXISTS, ALTER
-- TABLE на уже существующей боевой БД не поддержан). Нужна, чтобы core.stock.gplb_on_hand мог
-- отличить предмет, реально свободный на складе, от зафитованного в слот на стоящем в ангаре
-- корабле/структуре (реальный случай юзера: Multispectrum Shield Hardener II засчитывался «на
-- складе», хотя реально прикручен к Viator, стоящему в ангаре GPLB-C).
CREATE TABLE IF NOT EXISTS character_asset_flags (
    item_id        INTEGER PRIMARY KEY,
    character_id   INTEGER REFERENCES characters(character_id),
    location_flag  TEXT
);
CREATE INDEX IF NOT EXISTS idx_char_asset_flags_char ON character_asset_flags(character_id);

-- Структуры игроков (Upwell), где лежат ассеты/чертежи чаров: имя, система, тип. В SDE их
-- нет — только авторизованный ESI /universe/structures/{id}/ (esi-universe.read_structures.v1
-- + доступ к докингу); заполняется синком персонажей (ingest/character/structures.py). Нужна,
-- чтобы склад и «Где искать чертежи» выбирались по системам, а не по голым id (схема v6,
-- см. docs/schema.sql).
CREATE TABLE IF NOT EXISTS universe_structures (
    structure_id     INTEGER PRIMARY KEY,
    name             TEXT,
    solar_system_id  INTEGER REFERENCES sde_systems(system_id),
    type_id          INTEGER REFERENCES sde_types(type_id),
    owner_id         INTEGER,
    status           TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_universe_structures_system ON universe_structures(solar_system_id);

CREATE TABLE IF NOT EXISTS character_industry_jobs (
    job_id             INTEGER PRIMARY KEY,
    character_id       INTEGER REFERENCES characters(character_id),
    activity_id        INTEGER,
    blueprint_type_id  INTEGER,
    product_type_id    INTEGER,
    runs               INTEGER,
    cost               REAL,
    status             TEXT,
    start_date         TEXT,
    end_date           TEXT
);

-- =====================================================================
-- ДОМЕН 4: CONFIG / COMPUTE
-- =====================================================================
CREATE TABLE IF NOT EXISTS facilities (
    facility_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT,
    system_id          INTEGER REFERENCES sde_systems(system_id),
    structure_type_id  INTEGER,
    facility_tax       REAL,
    rig_material_bonus REAL,
    rig_time_bonus     REAL,
    rig_cost_bonus     REAL
);

CREATE TABLE IF NOT EXISTS pricing_profiles (
    profile_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT,
    material_source  TEXT,
    sell_target      TEXT,
    hub_region_id    INTEGER,
    broker_fee       REAL,
    sales_tax        REAL
);

CREATE TABLE IF NOT EXISTS scoring_weights (
    profile_id        INTEGER PRIMARY KEY REFERENCES pricing_profiles(profile_id),
    w_roi             REAL,
    w_isk_hour        REAL,
    w_liquidity       REAL,
    w_competition     REAL,
    min_daily_volume  INTEGER,
    max_capital       REAL
);

CREATE TABLE IF NOT EXISTS manufacturing_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at       TEXT,
    product_type_id   INTEGER REFERENCES sde_types(type_id),
    blueprint_item_id INTEGER,
    facility_id       INTEGER REFERENCES facilities(facility_id),
    profile_id        INTEGER REFERENCES pricing_profiles(profile_id),
    runs              INTEGER,
    material_cost     REAL,
    job_cost          REAL,
    invention_cost    REAL,
    revenue           REAL,
    profit            REAL,
    roi               REAL,
    isk_per_hour      REAL,
    daily_volume      INTEGER,
    score             REAL
);
CREATE INDEX IF NOT EXISTS idx_results_rank ON manufacturing_results(computed_at, score DESC);
CREATE INDEX IF NOT EXISTS idx_results_product ON manufacturing_results(product_type_id);

-- =====================================================================
-- ОПЕРАЦИОННЫЕ ТАБЛИЦЫ FORGE (нет в docs/schema.sql)
-- =====================================================================

-- Версия схемы для миграций.
CREATE TABLE IF NOT EXISTS schema_version (
    version  INTEGER PRIMARY KEY
);

-- Журнал синков — основа команды `forge status`.
CREATE TABLE IF NOT EXISTS sync_state (
    source        TEXT PRIMARY KEY,   -- 'sde' | 'market' | 'industry' | 'character' | ...
    last_run      TEXT,               -- когда запускался (ISO-8601)
    last_success  TEXT,               -- когда последний раз успешно завершился
    expires       TEXT,               -- до какого момента действует ESI-кэш
    rows          INTEGER,            -- сколько строк затронуто
    status        TEXT,               -- 'ok' | 'error' | 'running'
    note          TEXT                -- сообщение/ошибка
);
"""
