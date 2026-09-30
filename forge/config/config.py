"""Чтение конфига (TOML) и резолв имён локаций в id.

Read-only данные для ``core``/``recommend``/``planner``. Сеть не используется.
Конфиг читается стандартным ``tomllib`` (Python 3.11+). Имена локаций без id резолвятся
из SDE в памяти при загрузке (дёшево — пара выборок), чтобы не плодить схему.
"""

from __future__ import annotations

import sqlite3
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from ..i18n import tr
from ..storage import repositories as repo

DEFAULT_CONFIG_PATH = "forge.toml"


class Location(BaseModel):
    name: str
    system_id: int = 0
    region_id: int = 0


class Structures(BaseModel):
    """Игровые структуры (id из игры).

    ``market_structures`` — рынки-структуры, чьи ордера тянутся при синке персонажей (токеном
    персонажа со скоупом рынков структур) и сводятся в ОДИН срез под регион рынка сбыта (C-J6MT):
    min sell, max buy, суммарные объёмы. Пусто — единственный рынок ``taj_mahgoon_market_id``."""

    taj_mahgoon_market_id: int = 0
    gplb_engineering_complex_id: int = 0
    gplb_refinery_id: int = 0
    market_structures: list[int] = Field(default_factory=list)

    def market_ids(self) -> list[int]:
        """Рынки-структуры для синка: список ``market_structures`` (без повторов и нулей), а
        если он пуст — ``taj_mahgoon_market_id`` (если задан)."""
        ids = [int(x) for x in self.market_structures if x]
        if not ids and self.taj_mahgoon_market_id:
            ids = [int(self.taj_mahgoon_market_id)]
        return list(dict.fromkeys(ids))


class Industry(BaseModel):
    """Параметры производства для расчётов core. Множители ригов/структуры < 1 = экономия.

    rig_material_mult — множитель на материалы (1.0 = без бонуса);
    rig_cost_mult — множитель на стоимость джоба; facility_tax/scc_surcharge — доли (0.01 = 1%);
    broker_fee/sales_tax — для расчёта выручки при продаже.
    """

    rig_material_mult: float = 1.0
    rig_cost_mult: float = 1.0
    time_mult: float = 1.0          # бонус структуры/ригов на время джоба (planner)
    facility_tax: float = 0.0
    scc_surcharge: float = 0.0
    broker_fee: float = 0.0
    sales_tax: float = 0.0
    # Опция (выкл. по умолчанию): доля 0..1 эффективности переработки (reprocessing) —
    # реальное число со своей станции/скиллов/ригов (Forge их для переработки не считает
    # отдельно, в отличие от материального бонуса чертежей). 0.0 = не рассматривать
    # переработку прекурсора как альтернативу постройке/покупке материала вообще.
    reprocessing_efficiency: float = 0.0
    # Шанс инвенты со скиллами инвентора (формула EVE: база × (1 + Encryption/40 + (наука1 +
    # наука2)/30) × декриптор; скиллы — те, что требует T1-чертёж-источник). Без скиллов
    # попытки и себестоимость T2 завышены, выбор декриптора смещён, поэтому по умолчанию вкл;
    # false — «голый» шанс из SDE, без скиллов.
    # Инвентор — лучший по множителю среди пула «наука» (science_character_ids, пусто —
    # manufacturing_character_ids, ни одного списка ролей — все персонажи).
    invention_use_skills: bool = True


class RecommendGroup(BaseModel):
    """Настраиваемая группа для ТОП-списка «что строить» (напр. Capital ships)."""

    name: str
    group_ids: list[int] = Field(default_factory=list)   # sde_groups.group_id


class Recommend(BaseModel):
    """Веса и фильтры движка рекомендаций «что строить»."""

    w_roi: float = 1.0
    w_isk_hour: float = 1.0
    w_liquidity: float = 0.5
    min_daily_volume: int = 0       # отсечь неликвид (0 = без фильтра)
    max_capital: float = 0.0        # бюджетный потолок на партию (0 = без лимита)
    runs: int = 1                   # сколько прогонов оценивать по умолчанию
    min_cost_ratio: float = 0.01    # отсечь битые чертежи: себестоимость < доли от цены продажи
    winsor_pct: float = 0.05        # винзоризация выбросов метрик с каждого хвоста перед нормировкой
    top_per_group: int = 5          # размер ТОП-списка на каждую группу
    groups: list[RecommendGroup] = Field(default_factory=list)  # группы ТОП (сколько угодно)
    # «Запреты»: эти предметы/группы/категории никогда не рекомендуются (ни в ТОП, ни в
    # «из остатков», ни в «дешевле купить») — напр. то, что не хочешь/не можешь продавать.
    exclude_type_ids: list[int] = Field(default_factory=list)
    exclude_group_ids: list[int] = Field(default_factory=list)
    exclude_category_ids: list[int] = Field(default_factory=list)
    # Скан «дешевле купить, чем строить»:
    buy_cheaper_max_savings_pct: float = 0.9  # отсечь артефакты SDE (экономия выше — мусор, напр. компрессия)
    buy_cheaper_exclude_categories: list[int] = Field(default_factory=lambda: [25])  # 25 = Asteroid (руда/лёд)
    # Ликвидность — «Объём/сут», фильтр min_daily_volume и вес w_liquidity (ТОП, «из остатков»,
    # «дешевле купить» — там рынок = хаб покупки). Откуда брать оборот:
    # - "sell_region" — среднее по последним 30 ЗАПИСЯМ истории региона (дни без
    #   сделок в истории ESI отсутствуют — у неликвида завышено), нет истории — выставленный на
    #   продажу объём снапшота (не проданное!). Историю C-J6MT синкает только режим
    #   "sell_region_history" → здесь по сути снапшот;
    # - "sell_region_history" — реальный оборот региона сбыта по истории ESI (в неё попадают и
    #   сделки в структурах игроков — проверено на Insmother, где нет NPC-станций); включает
    #   суточный синк истории этого региона по типам, торгуемым на рынке-структуре;
    # - "jita" — оборот Jita как прокси (история The Forge уже синкается).
    # liquidity_days — окно в КАЛЕНДАРНЫХ днях до последнего дня истории региона, дни без
    # сделок = 0 (для "sell_region_history"/"jita"; "sell_region" — по 30 записям, см. выше).
    liquidity_source: str = "sell_region"
    liquidity_days: int = 30


class Facility(BaseModel):
    """Станция (структура) под определённую роль джобов. Бонусы в % (экономия), налог в %.

    role — маршрут: 'reaction' | 'invention' | 'component' | 'manufacturing' | 'copy'.
    group_ids — какие группы продуктов роутятся именно сюда (пусто = catch-all для роли).
    category_ids — то же самое, но по КАТЕГОРИИ продукта (грубее group_ids: напр. «Ship»
    category=6 разом покрывает все группы кораблей). Нужно для ригов Engineering Complex,
    которые в EVE масштаба XL/L даются ПАРАМИ по категории (Standup XL-Set Ship Manufacturing
    Efficiency действует ТОЛЬКО на Ships, Equipment and Consumable — только на Module/Charge и
    т.п., Structure and Component — только на Structure/Component) — одна физическая станция с
    несколькими такими ригами описывается НЕСКОЛЬКИМИ facility-записями (одна на риг), каждая
    со своим category_ids; факилити подходит, если group_id ИЛИ category_id продукта совпадает.
    fitted_type_ids — реально фитованные риги/сервис-модули структуры; если непусто, из них
    СЧИТАЮТСЯ material_bonus_pct/time_bonus_pct (через forge.core.rigs, из SDE dogma-атрибутов
    + стэкинг-пенальти EVE) — ручные material_bonus_pct/time_bonus_pct тогда игнорируются и
    остаются только как фолбэк для facility, где fitted_type_ids ещё не заполнен.
    structure_type_id — тип САМОЙ структуры (Tatara/Athanor/Raitaru/Azbel/Sotiyo), даёт
    ВСТРОЕННЫЙ бонус (не риговый): strEngMatBonus/strEngTimeBonus у Engineering Complex,
    strReactionTimeMultiplier у Tatara (у Athanor его нет вообще). Комбинируется с ригами
    перемножением множителей (структурный бонус — отдельная, не риговая, группа стэкинга).
    system_id — солнечная система станции: по ней индекс стоимости джобов этой роли и
    security-модификатор бонусов ригов. 0 — авто: система структуры ``location_id`` (ESI
    ``universe_structures``, NPC-станция — SDE), а если не известна — система стройки.
    Логистика между системами НЕ моделируется: материалы считаются доставленными в систему
    стройки, куда бы ни стояла станция.
    """

    name: str
    role: str
    material_bonus_pct: float = 0.0
    time_bonus_pct: float = 0.0
    cost_bonus_pct: float = 0.0
    tax_pct: float = 0.0
    group_ids: list[int] = Field(default_factory=list)
    category_ids: list[int] = Field(default_factory=list)
    location_id: int = 0  # id структуры в EVE (для подписи локаций и фильтра чертежей)
    fitted_type_ids: list[int] = Field(default_factory=list)  # риги/сервис-модули структуры
    structure_type_id: int = 0  # тип структуры (Tatara/Athanor/Raitaru/Azbel/Sotiyo)
    system_id: int = 0  # система станции (0 — авто: из location_id, иначе система стройки)


class BlueprintOverride(BaseModel):
    """Ручная стоимость чертежа предмета (ISK на 1 run продукта)."""

    type_id: int
    per_run: float = 0.0


class DecryptorOverride(BaseModel):
    """Декриптор для инвенты конкретного T2-товара: ``type_id`` — сам T2-продукт (напр. Nomad),
    ``decryptor_type_id`` — декриптор (0 = без декриптора)."""

    type_id: int
    decryptor_type_id: int = 0


class Invention(BaseModel):
    """Выбор декриптора инвенты (и обычной инвенты, и закрытия недостачи ранов своей копии).

    ``decryptor_mode``:
    - ``"auto_cost"`` (по умолчанию) — вариант с минимальной стоимостью (материалы по ME копии +
      попытки инвенты) среди разрешённых ``allowed_decryptors`` (пусто — все 8) и «без декриптора»;
    - ``"none"`` — всегда без декриптора;
    - ``"fixed"`` — всегда ``decryptor_type_id`` (0 — без декриптора).
    ``per_product`` сильнее режима: для перечисленных T2-товаров — их декриптор. Заданный вручную
    декриптор без цены на рынке — недоступен: берётся авто-выбор с пометкой в «Подготовка /
    проблемы» (Калькулятор и отчёт). ``allowed_decryptors`` ограничивает только авто-выбор (в т.ч.
    этот фолбэк); явный выбор (fixed/per_product) действует и вне списка."""

    decryptor_mode: str = "auto_cost"
    decryptor_type_id: int = 0
    allowed_decryptors: list[int] = Field(default_factory=list)
    per_product: list[DecryptorOverride] = Field(default_factory=list)


class Sso(BaseModel):
    """EVE SSO (OAuth2 PKCE). client_id публичный, секрет не нужен и не хранится."""

    client_id: str = ""
    callback_port: int = 8765
    scopes: list[str] = Field(
        default_factory=lambda: [
            "esi-characters.read_blueprints.v1",
            "esi-skills.read_skills.v1",
            "esi-assets.read_assets.v1",
            "esi-industry.read_character_jobs.v1",
            "esi-wallet.read_character_wallet.v1",
            "esi-markets.structure_markets.v1",
            "esi-universe.read_structures.v1",
        ]
    )

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.callback_port}/callback"


class Character(BaseModel):
    name: str
    production: bool = False


class AutoSync(BaseModel):
    """Фоновое автообновление (планировщик в команде ``web``). По умолчанию выключено.

    ``character_minutes`` — как часто синкать персонажей (+ структурный рынок C-J6MT);
    ``market_minutes`` — рынок Jita + индустрия-индексы. Интервалы уважают ESI-кэш
    оркестратора (синк всё равно пропускается, пока кэш свеж)."""

    enabled: bool = False
    character_minutes: int = 60
    market_minutes: int = 30


class StockKeep(BaseModel):
    """«Неприкосновенный запас»: столько штук ``type_id`` на складе не трогать никогда
    (напр. топливо для структур) — в расчёт склада идёт только излишек сверх этого."""

    type_id: int
    quantity: int = 0


class Stock(BaseModel):
    """Что считать складом (остатками) — для отчёта по стройке и «Что построить из остатков».

    ``mode``:
    - ``"auto"`` — структуры стройки из ``[structures]`` (gplb_*) + все
      ``blueprint_location_ids`` (плюс контейнеры в них);
    - ``"custom"`` — ровно то, что выбрано ниже (вкладка «Склад» пульта).

    ``system_ids`` — ВСЕ локации в этих системах (станции, структуры — в т.ч. новые, после
    очередного синка), ``location_ids`` — отдельные станции/структуры/контейнеры. Исключения
    (``exclude_location_ids``) действуют и внутри выбранной системы — напр. «всё в GPLB-C, кроме
    вон того ангара с личными фитами». ``character_ids`` — чьи ассеты считать (пусто — всех).

    Предметы вне системы стройки годятся, но их надо довезти: для хабов закупки (Jita, C-J6MT)
    их объём добавляется в соответствующее плечо фрахта отчёта; для прочих систем доставка
    не известна, и отчёт помечает её отдельно.
    """

    mode: str = "auto"
    system_ids: list[int] = Field(default_factory=list)
    location_ids: list[int] = Field(default_factory=list)
    exclude_location_ids: list[int] = Field(default_factory=list)
    character_ids: list[int] = Field(default_factory=list)
    # Модули в слотах фита (HiSlot/MedSlot/LoSlot/RigSlot/SubSystemSlot/ServiceSlot) кораблей и
    # структур, стоящих на складе, — не свободный остаток (снятие с фита не бесплатно).
    exclude_fitted: bool = True
    # Собранный корабль (с фитом/грузом внутри) — это «чей-то корабль», а не корпус на складе.
    # ESI is_singleton не храним, поэтому признак — внутри корабля что-то лежит.
    exclude_assembled_ships: bool = False
    # Дополнительные location_flag, которые НЕ считать складом (напр. "DroneBay", "Cargo",
    # "FleetHangar", "AssetSafety"). Сравнение по префиксу; действует и на содержимое
    # (всё, что лежит внутри контейнера/корабля с таким флагом).
    exclude_flags: list[str] = Field(default_factory=list)
    # «Запреты»: эти предметы/группы никогда не берутся со склада (стратегический запас).
    exclude_type_ids: list[int] = Field(default_factory=list)
    exclude_group_ids: list[int] = Field(default_factory=list)
    keep: list[StockKeep] = Field(default_factory=list)


class Market(BaseModel):
    """Где покупать и по какой цене считать продажу.

    ``buy_hubs`` — разрешённые хабы закупки материалов: ``"jita"`` (Jita + доставка) и/или
    ``"cj"`` (C-J6MT). Пусто — оба. ``sell_price`` — чем оценивать выручку в месте
    сбыта: ``"sell_min"`` (выставить sell-ордер по лучшей цене, по умолчанию) или ``"buy_max"``
    (продать сразу в лучший buy-ордер — быстрее, обычно дешевле)."""

    buy_hubs: list[str] = Field(default_factory=lambda: ["jita", "cj"])
    sell_price: str = "sell_min"


class SlotLimit(BaseModel):
    """Сколько слотов персонажа ``character_id`` Forge может занять — по пулам (производство /
    реакции / наука). Отрицательное (по умолчанию -1) — все по скиллам; 0 — персонаж в этом
    пуле не участвует. Уже запущенные джобы (любые) занимают слоты по скиллам до окончания:
    Forge получает min(лимит, свободно по скиллам) — напр. свободно 3 из 11 при лимите 5 →
    3 сейчас и до 5 по мере окончания идущих джобов (см. planner.schedule.apply_slot_limits)."""

    character_id: int
    manufacturing: int = -1
    reaction: int = -1
    science: int = -1

    def limit(self, pool: str) -> int | None:
        """Лимит пула или None — не ограничен (все слоты по скиллам)."""
        v = int(getattr(self, pool, -1))
        return v if v >= 0 else None


class Planner(BaseModel):
    """Параметры расписания.

    ``compare_max_days`` — какие сроки «Макс. N дней/поток» перебирать в «Сравнить варианты»
    (дробные — части суток: 0.5 = 12 часов). ``max_streams_per_node`` — потолок числа
    параллельных потоков одного компонента при разбивке под срок. ``slot_limits`` — сколько
    слотов Forge может занять у конкретных персонажей (``SlotLimit``; вкладка «Персонажи»)."""

    compare_max_days: list[float] = Field(default_factory=lambda: [5.0, 3.0, 2.0, 1.0, 0.5])
    max_streams_per_node: int = 64
    # Слоты, занятые уже запущенными джобами (ESI status=active), освобождаются только к их
    # окончанию — иначе ETA оптимистичен. Выключить — считать все слоты свободными.
    account_running_jobs: bool = True
    # Кому ставить джоб (владелец = у кого чертёж/формула/T1-источник инвенты лежит в «Где искать
    # чертежи»): "any" — любому персонажу роли с самым ранним окончанием (по умолчанию; передачи
    # чертежей — только предупреждением); "prefer_owner" — владельцу, если он закончит не позже
    # лучшего на owner_slack_hours; "owner_only" — только владельцу, а если среди назначенных в
    # роли его нет — любому, с явным предупреждением.
    owner_policy: str = "any"
    owner_slack_hours: float = 24.0
    slot_limits: list[SlotLimit] = Field(default_factory=list)
    # Копирование T1-чертежа для инвенты как джобы расписания: если T1-копия получается
    # копи-джобом со СВОЕГО BPO (не ручная цена BPC), перед инвентой встают копи-джобы (пул
    # «наука»), инвента ждёт свои копии. Выкл (по умолчанию) — стоимость копий учтена, а их
    # время в срок не попадает.
    schedule_copy_jobs: bool = False

    def slot_limit(self, character_id: int, pool: str) -> int | None:
        """Лимит слотов персонажа в пуле или None — все по скиллам."""
        for sl in self.slot_limits:
            if int(sl.character_id) == int(character_id):
                return sl.limit(pool)
        return None


class Ui(BaseModel):
    """Значения пульта по умолчанию (корзина, фильтры вкладок) — на математику не влияют."""

    default_me: int = 0
    default_te: int = 0
    default_consolidate: bool = True
    default_auto_streams: bool = False
    recommend_top: int = 30
    buy_cheaper_top: int = 60
    buy_cheaper_min_volume: float = 50.0
    stock_top: int = 30
    # Язык пульта и HTML-отчётов: "ru" (по умолчанию) или "en" — переключатель RUS/ENG в шапке.
    lang: str = "ru"


class FreightRoute(BaseModel):
    from_: str = Field(alias="from")
    to: str
    mode: str = "per_m3"
    isk_per_m3: float = 0.0
    fixed_cost: float = 0.0
    vessel_capacity_m3: float = 0.0
    load_factor: float = 0.0
    # Минимальная стоимость доставки за заказ (только per_m3): если объём×ставка меньше этого
    # порога — платим порог (минимальный фрахт за рейс/доставку, даже мелкую). 0 = нет минимума.
    min_cost: float = 0.0

    model_config = {"populate_by_name": True}


class Config(BaseModel):
    db_path: str = "forge.db"
    sso: Sso = Field(default_factory=Sso)
    industry: Industry = Field(default_factory=Industry)
    recommend: Recommend = Field(default_factory=Recommend)
    autosync: AutoSync = Field(default_factory=AutoSync)
    # Роли персонажей в стройке. Пустые все списки = участвуют все. Если хоть один список
    # непуст — участвуют только перечисленные (в своём пуле); остальные «нигде».
    manufacturing_character_ids: list[int] = Field(default_factory=list)
    reaction_character_ids: list[int] = Field(default_factory=list)
    # Наука (инвента/копирование, слоты Laboratory Operation). Пусто — наукой
    # занимаются те же, кто отмечен на производство.
    science_character_ids: list[int] = Field(default_factory=list)
    locations: dict[str, Location] = Field(default_factory=dict)
    structures: Structures = Field(default_factory=Structures)
    characters: list[Character] = Field(default_factory=list)
    freight_routes: list[FreightRoute] = Field(default_factory=list)
    blueprint_overrides: list[BlueprintOverride] = Field(default_factory=list)
    facilities: list[Facility] = Field(default_factory=list)
    # «Всегда покупать, не строить» — по группам SDE и/или конкретным type_id (напр. все Fuel Block).
    always_buy_groups: list[int] = Field(default_factory=list)
    always_buy_types: list[int] = Field(default_factory=list)
    # «Всегда строить, не покупать» (если есть чертёж) — даже когда рынок дешевле: напр. свои
    # компоненты ради контроля качества/объёма. «Всегда покупать» сильнее, если заданы оба.
    always_build_groups: list[int] = Field(default_factory=list)
    always_build_types: list[int] = Field(default_factory=list)
    # Где искать чертежи (location_id станций/структур). Пусто = искать везде;
    # иначе «своими» считаются только чертежи, физически лежащие в этих локациях.
    blueprint_location_ids: list[int] = Field(default_factory=list)
    # Группы кораблей, которые при вывозе на рынок летят САМИ (прыжком) — вывоз = топливо за
    # прыжок за штуку, а не объём × ставка (их не грузят в трюм). По умолчанию — JF, карриеры,
    # дреды, FAX, суперы, титаны, Rorqual, Black Ops (= JUMP_CAPABLE_GROUPS в core/cost.py).
    jump_capable_groups: list[int] = Field(
        default_factory=lambda: [902, 547, 485, 1538, 659, 30, 883, 898]
    )
    stock: Stock = Field(default_factory=Stock)
    market: Market = Field(default_factory=Market)
    planner: Planner = Field(default_factory=Planner)
    invention: Invention = Field(default_factory=Invention)
    ui: Ui = Field(default_factory=Ui)


def load(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    """Загрузить конфиг из TOML-файла."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            tr("Конфиг не найден: {path}. Скопируй forge.example.toml в {path} и заполни.", path=p)
        )
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return Config.model_validate(data)


def loads(text: str) -> Config:
    """Загрузить конфиг из строки TOML (удобно для тестов)."""
    return Config.model_validate(tomllib.loads(text))


def save(cfg: Config, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """Записать конфиг в TOML (для сохранения настроек из пульта).

    Внимание: комментарии не сохраняются (forge.example.toml остаётся справочником).
    """
    import tomli_w  # из extra [web]

    data = cfg.model_dump(by_alias=True)
    with Path(path).open("wb") as fh:
        tomli_w.dump(data, fh)


def resolve_locations(cfg: Config, conn: sqlite3.Connection) -> Config:
    """Дозаполнить отсутствующие system_id/region_id из SDE по имени локации.

    Возвращает тот же объект конфига (мутирует locations). Требует наполненной SDE;
    если SDE пуста, неизвестные id остаются нулевыми.
    """
    for loc in cfg.locations.values():
        if loc.system_id == 0 and loc.name:
            sid = repo.resolve_system_id(conn, loc.name)
            if sid is not None:
                loc.system_id = sid
        if loc.region_id == 0 and loc.name:
            rid = repo.resolve_region_id_by_system(conn, loc.name)
            if rid is not None:
                loc.region_id = rid
    return cfg
