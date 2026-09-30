-- ============================================================================
-- EVE Online Manufacturing Helper — схема БД (PostgreSQL)
-- Соло-индустриалист, покрывает T1 / T2 (инвенция + реакции) / капиталы.
--
-- Домены:
--   1. SDE / reference   — статика, обновляется раз в патч
--   2. Market            — рыночные данные, обновляются ежедневно/часто
--   3. Character (SSO)    — персональные данные персонажа
--   4. Config / Compute  — твои сетапы, профили цен и кэш результатов
--
-- Ключевая идея: граф постройки не хранится отдельно — он выводится из
-- sde_blueprint_materials + sde_blueprint_products рекурсивным CTE.
-- ============================================================================


-- ===========================================================================
-- ДОМЕН 1: SDE / REFERENCE (статика)
-- ===========================================================================

-- Каталог всех предметов игры. Центральный хаб — почти всё ссылается сюда.
CREATE TABLE sde_categories (
    category_id  INTEGER PRIMARY KEY,
    name         TEXT
);

CREATE TABLE sde_groups (
    group_id     INTEGER PRIMARY KEY,
    category_id  INTEGER REFERENCES sde_categories(category_id),
    name         TEXT
);

CREATE TABLE sde_types (
    type_id          INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    group_id         INTEGER REFERENCES sde_groups(group_id),
    category_id      INTEGER REFERENCES sde_categories(category_id),
    volume           DOUBLE PRECISION,        -- м3, для расчёта логистики
    packaged_volume  DOUBLE PRECISION,
    base_price       DOUBLE PRECISION,        -- справочная цена, НЕ рыночная
    market_group_id  INTEGER,
    is_published     BOOLEAN DEFAULT TRUE
);
CREATE INDEX idx_types_group    ON sde_types(group_id);
CREATE INDEX idx_types_category ON sde_types(category_id);

-- Солнечные системы — нужны для system cost index и логистики.
CREATE TABLE sde_systems (
    system_id  INTEGER PRIMARY KEY,
    name       TEXT,
    region_id  INTEGER,
    security   DOUBLE PRECISION
);
CREATE INDEX idx_systems_region ON sde_systems(region_id);

-- Блупринт — это тоже type_id, но с производственными активностями.
CREATE TABLE sde_blueprints (
    blueprint_type_id     INTEGER PRIMARY KEY REFERENCES sde_types(type_id),
    max_production_limit  INTEGER              -- максимум runs за один джоб
);

-- Активности блупринта и их базовое время.
-- activity_id: 1=manufacturing, 3=research_te, 4=research_me,
--              5=copying, 8=invention, 11=reaction
CREATE TABLE sde_blueprint_activities (
    blueprint_type_id  INTEGER REFERENCES sde_blueprints(blueprint_type_id),
    activity_id        SMALLINT,
    time_seconds       INTEGER,               -- на 1 run, до TE и бонусов структуры
    PRIMARY KEY (blueprint_type_id, activity_id)
);

-- Входные материалы на 1 run (до применения ME).
CREATE TABLE sde_blueprint_materials (
    blueprint_type_id  INTEGER,
    activity_id        SMALLINT,
    material_type_id   INTEGER REFERENCES sde_types(type_id),
    quantity           INTEGER,
    PRIMARY KEY (blueprint_type_id, activity_id, material_type_id)
);
CREATE INDEX idx_bp_materials_mat ON sde_blueprint_materials(material_type_id);

-- Выходные продукты. probability < 1 для инвенции.
CREATE TABLE sde_blueprint_products (
    blueprint_type_id  INTEGER,
    activity_id        SMALLINT,
    product_type_id    INTEGER REFERENCES sde_types(type_id),
    quantity           INTEGER,
    probability        DOUBLE PRECISION DEFAULT 1.0,
    PRIMARY KEY (blueprint_type_id, activity_id, product_type_id)
);
-- Этот индекс — основа резолвера "как построить тип X".
CREATE INDEX idx_bp_products_prod ON sde_blueprint_products(product_type_id, activity_id);

-- Требуемые скиллы по активности — для фильтра "могу ли я это построить".
CREATE TABLE sde_blueprint_skills (
    blueprint_type_id  INTEGER,
    activity_id        SMALLINT,
    skill_type_id      INTEGER REFERENCES sde_types(type_id),
    level              SMALLINT,
    PRIMARY KEY (blueprint_type_id, activity_id, skill_type_id)
);


-- ===========================================================================
-- ДОМЕН 2: MARKET (динамика)
-- ===========================================================================

-- Дневные свечи. Позвоночник рекомендаций: volume = ликвидность,
-- разброс highest/lowest = волатильность (риск цены за время постройки).
CREATE TABLE market_history (
    type_id      INTEGER REFERENCES sde_types(type_id),
    region_id    INTEGER,
    day          DATE,
    average      DOUBLE PRECISION,
    highest      DOUBLE PRECISION,
    lowest       DOUBLE PRECISION,
    volume       BIGINT,                       -- штук в день
    order_count  INTEGER,
    PRIMARY KEY (type_id, region_id, day)
);
CREATE INDEX idx_history_type_region ON market_history(type_id, region_id, day DESC);

-- Текущий срез лучших цен (из агрегатов Fuzzwork, обновляется часто).
CREATE TABLE market_snapshot (
    type_id      INTEGER REFERENCES sde_types(type_id),
    region_id    INTEGER,
    sell_min     DOUBLE PRECISION,
    buy_max      DOUBLE PRECISION,
    sell_volume  BIGINT,
    buy_volume   BIGINT,
    updated_at   TIMESTAMPTZ,
    PRIMARY KEY (type_id, region_id)
);

-- CCP adjusted/average price. ВАЖНО: adjusted_price идёт в Estimated Item
-- Value для расчёта стоимости джоба — это НЕ рыночная цена Jita.
CREATE TABLE market_adjusted_prices (
    type_id         INTEGER PRIMARY KEY REFERENCES sde_types(type_id),
    adjusted_price  DOUBLE PRECISION,
    average_price   DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ
);

-- System cost index по активностям — основа стоимости установки джоба.
CREATE TABLE system_cost_indices (
    system_id    INTEGER REFERENCES sde_systems(system_id),
    activity_id  SMALLINT,
    cost_index   DOUBLE PRECISION,
    updated_at   TIMESTAMPTZ,
    PRIMARY KEY (system_id, activity_id)
);


-- ===========================================================================
-- ДОМЕН 3: CHARACTER (через EVE SSO / OAuth2)
-- ===========================================================================

-- refresh_token ОБЯЗАТЕЛЬНО шифровать at-rest и не писать в логи.
CREATE TABLE characters (
    character_id    BIGINT PRIMARY KEY,
    name            TEXT,
    refresh_token   TEXT,                      -- зашифровано
    scopes          TEXT,
    wallet_balance  DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ
);

CREATE TABLE character_skills (
    character_id   BIGINT REFERENCES characters(character_id),
    skill_type_id  INTEGER REFERENCES sde_types(type_id),
    active_level   SMALLINT,
    PRIMARY KEY (character_id, skill_type_id)
);

-- Твои реальные блупринты. ME/TE живут ЗДЕСЬ, на экземпляре, а не на типе.
CREATE TABLE character_blueprints (
    item_id       BIGINT PRIMARY KEY,          -- уникальный экземпляр
    character_id  BIGINT REFERENCES characters(character_id),
    type_id       INTEGER REFERENCES sde_types(type_id),
    location_id   BIGINT,
    me            SMALLINT,                     -- 0..10
    te            SMALLINT,                     -- 0..20
    quantity      INTEGER,                      -- -1 = BPO
    runs          INTEGER,                      -- -1 = BPO; иначе остаток у BPC
    is_copy       BOOLEAN
);
CREATE INDEX idx_char_bp_type ON character_blueprints(type_id);

-- Что уже лежит в ангаре — чтобы не покупать имеющиеся материалы.
CREATE TABLE character_assets (
    item_id       BIGINT PRIMARY KEY,
    character_id  BIGINT REFERENCES characters(character_id),
    type_id       INTEGER REFERENCES sde_types(type_id),
    location_id   BIGINT,
    quantity      BIGINT
);
CREATE INDEX idx_char_assets_type ON character_assets(character_id, type_id);

-- ESI location_flag для строки character_assets (Cargo/Hangar/HiSlot0/MedSlot.../DroneBay и
-- т.п.) — отдельная таблица, не колонка в character_assets (та же причина, что и у
-- sde_reprocessing_materials: миграции здесь только CREATE TABLE, без ALTER на боевой БД).
-- Нужна, чтобы отличить предмет, реально свободный на складе, от зафитованного в слот на
-- стоящем в ангаре корабле/структуре (последнее НЕЛЬЗЯ считать доступным остатком).
CREATE TABLE character_asset_flags (
    item_id        BIGINT PRIMARY KEY,
    character_id   BIGINT REFERENCES characters(character_id),
    location_flag  TEXT
);
CREATE INDEX idx_char_asset_flags_char ON character_asset_flags(character_id);

-- Структуры игроков (Upwell), в которых лежат ассеты/чертежи чаров: имя, система, тип.
-- В SDE их нет — даёт только авторизованный ESI /universe/structures/{id}/ (скоуп
-- esi-universe.read_structures.v1 + доступ к докингу). Заполняется синком персонажей; нужна,
-- чтобы склад и «Где искать чертежи» выбирались по системам, а не по голым id.
CREATE TABLE universe_structures (
    structure_id     BIGINT PRIMARY KEY,
    name             TEXT,
    solar_system_id  INTEGER REFERENCES sde_systems(system_id),
    type_id          INTEGER REFERENCES sde_types(type_id),
    owner_id         BIGINT,
    status           TEXT,        -- 'ok' | 'forbidden' (нет доступа ни у одного чара) | 'error'
    updated_at       TIMESTAMPTZ
);
CREATE INDEX idx_universe_structures_system ON universe_structures(solar_system_id);

-- Активные/прошлые джобы — учёт занятых слотов и фактической стоимости.
CREATE TABLE character_industry_jobs (
    job_id             BIGINT PRIMARY KEY,
    character_id       BIGINT REFERENCES characters(character_id),
    activity_id        SMALLINT,
    blueprint_type_id  INTEGER,
    product_type_id    INTEGER,
    runs               INTEGER,
    cost               DOUBLE PRECISION,
    status             TEXT,
    start_date         TIMESTAMPTZ,
    end_date           TIMESTAMPTZ
);


-- ===========================================================================
-- ДОМЕН 4: CONFIG / COMPUTE
-- ===========================================================================

-- Твои производственные сетапы: структура + риги + налог.
-- Бонусы ригов хранятся уже свёрнутыми в множители для простоты расчёта.
CREATE TABLE facilities (
    facility_id        SERIAL PRIMARY KEY,
    name               TEXT,
    system_id          INTEGER REFERENCES sde_systems(system_id),
    structure_type_id  INTEGER,                -- Raitaru/Azbel/Sotiyo/Athanor/Tatara
    facility_tax       DOUBLE PRECISION,       -- % заданный владельцем структуры
    rig_material_bonus DOUBLE PRECISION,       -- суммарный эффект ригов на материалы
    rig_time_bonus     DOUBLE PRECISION,
    rig_cost_bonus     DOUBLE PRECISION
);

-- Профиль ценообразования: как считаем доход и расход.
CREATE TABLE pricing_profiles (
    profile_id       SERIAL PRIMARY KEY,
    name             TEXT,
    material_source  TEXT,                      -- 'buy' | 'sell'
    sell_target      TEXT,                      -- 'sell' | 'buy'
    hub_region_id    INTEGER,
    broker_fee       DOUBLE PRECISION,
    sales_tax        DOUBLE PRECISION
);

-- Веса движка рекомендаций + жёсткие фильтры (по одному набору на профиль).
CREATE TABLE scoring_weights (
    profile_id        INTEGER PRIMARY KEY REFERENCES pricing_profiles(profile_id),
    w_roi             DOUBLE PRECISION,
    w_isk_hour        DOUBLE PRECISION,
    w_liquidity       DOUBLE PRECISION,
    w_competition     DOUBLE PRECISION,
    min_daily_volume  BIGINT,                   -- отсечь неликвид
    max_capital       DOUBLE PRECISION          -- бюджетный потолок
);

-- Кэш прибыльности = снапшот рекомендаций на момент времени.
-- Движок читает отсюда, а не пересчитывает всё на каждый запрос.
CREATE TABLE manufacturing_results (
    id                BIGSERIAL PRIMARY KEY,
    computed_at       TIMESTAMPTZ,
    product_type_id   INTEGER REFERENCES sde_types(type_id),
    blueprint_item_id BIGINT,                   -- какой именно BP использован (ME/TE!)
    facility_id       INTEGER REFERENCES facilities(facility_id),
    profile_id        INTEGER REFERENCES pricing_profiles(profile_id),
    runs              INTEGER,
    material_cost     DOUBLE PRECISION,
    job_cost          DOUBLE PRECISION,
    invention_cost    DOUBLE PRECISION,         -- ожидаемая стоимость с учётом probability
    revenue           DOUBLE PRECISION,         -- после broker fee + sales tax
    profit            DOUBLE PRECISION,
    roi               DOUBLE PRECISION,         -- profit / вложенный капитал
    isk_per_hour      DOUBLE PRECISION,
    daily_volume      BIGINT,                   -- из market_history, для ликвидности
    score             DOUBLE PRECISION          -- итоговый ранг
);
CREATE INDEX idx_results_rank ON manufacturing_results(computed_at, score DESC);
CREATE INDEX idx_results_product ON manufacturing_results(product_type_id);