"""Английские переводы: вкладка «Настройки» (desktop/pages/settings.py).

Ключ — русская строка ровно как в коде (с подстановками {name}), значение — английская."""

EN: dict[str, str] = {
    # --- разделы и кнопки
    "Производство": "Manufacturing",
    "Станции": "Stations",
    "Логистика и рынок": "Logistics & market",
    "Рекомендации": "Recommendations",
    "Чертежи": "Blueprints",
    "Планировщик": "Planner",
    "Система": "System",
    "Сохранить": "Save",
    "Сбросить изменения": "Discard changes",
    "Сохранено в forge.toml — расчёты используют новые значения.":
        "Saved to forge.toml — calculations now use the new values.",
    "Убрать": "Remove",
    "Добавить": "Add",
    "Предмет…": "Item…",
    "Группа…": "Group…",
    "Категория…": "Category…",
    "предметы не выбраны": "no items selected",
    "группы не выбраны": "no groups selected",
    "категории не выбраны": "no categories selected",
    "Предметы": "Items",
    "Группы": "Groups",
    "Категории": "Categories",
    "По группам": "By group",
    "По предметам": "By item",
    # --- роли станций
    "реакции": "reactions",
    "инвента": "invention",
    "копирование": "copying",
    "T2-компоненты (по группам)": "T2 components (by group)",
    "производство (остальное)": "manufacturing (everything else)",
    # --- откуда известна система станции
    "система структуры «Где стоит»": "system of the “Located at” structure",
    "NPC-станция «Где стоит»": "“Located at” NPC station",
    "указана системой": "set as a system",
    "система стройки — структура ещё не известна (синк персонажей)":
        "build system — structure not known yet (character sync)",
    # --- Производство: числовые поля
    "Множитель материалов (фолбэк, если нет станции роли)":
        "Material multiplier (fallback if the role has no station)",
    "1.0 = без бонуса": "1.0 = no bonus",
    "Множитель стоимости джоба (фолбэк)": "Job cost multiplier (fallback)",
    "Множитель времени (фолбэк)": "Time multiplier (fallback)",
    "Налог структуры, доля (фолбэк)": "Structure tax, fraction (fallback)",
    "SCC surcharge, доля": "SCC surcharge, fraction",
    "Налог SCC на джобы (сейчас 4% = 0.04)": "SCC tax on jobs (currently 4% = 0.04)",
    "Брокер при продаже, доля": "Broker fee on sale, fraction",
    "Налог с продажи, доля": "Sales tax, fraction",
    "Эффективность переработки, доля 0–1 (0 — не рассматривать)":
        "Reprocessing efficiency, fraction 0–1 (0 — ignore)",
    "Реальная эффективность со станции/скиллов — переработка прекурсора как альтернатива постройке":
        "Actual efficiency from your station/skills — reprocessing a precursor as an alternative to building it",
    "Производство и налоги": "Manufacturing & taxes",
    "Станции с ригами (вкладка «Станции») важнее этих фолбэков: множители выше "
    "действуют, только если под роль джоба нет станции.":
        "Rigged stations (“Stations” tab) take priority over these fallbacks: the multipliers above "
        "apply only if there is no station for the job's role.",
    # --- Производство: инвента и декрипторы
    "Инвента": "Invention",
    "Шанс инвенты — со скиллами инвентора": "Invention chance — with the inventor's skills",
    "EVE: шанс = база × (1 + Encryption/40 + (наука1 + наука2)/30) × декриптор, не больше 100%. "
    "Выкл — «голый» шанс из SDE, без скиллов (попыток и себестоимости T2 больше).":
        "EVE: chance = base × (1 + Encryption/40 + (science1 + science2)/30) × decryptor, capped at 100%. "
        "Off — the “bare” chance from the SDE, without skills (more attempts and a higher T2 build cost).",
    "Скиллы — те, что требует инвента конкретного T1-чертежа (Encryption Methods расы + две науки). "
    "Инвентор — лучший по этим скиллам среди назначенных на «Науку» (вкладка «Персонажи»; пусто — те, "
    "кто на производстве). Разбивка шанса видна в Калькуляторе, блок «Чертёж — инвента».":
        "Skills are the ones the specific T1 blueprint's invention requires (the race's Encryption Methods "
        "+ two sciences). The inventor is the best at these skills among those assigned to “Science” "
        "(“Characters” tab; if none — those on manufacturing). The chance breakdown is shown in the "
        "Calculator, “Blueprint — invention” block.",
    "Декриптор": "Decryptor",
    "Авто — по стоимости": "Auto — by cost",
    "Без декриптора": "No decryptor",
    "Один для всех": "One for all",
    "без декриптора": "no decryptor",
    " · нет цены": " · no price",
    "{name} (шанс ×{prob}, ME {me}, TE {te}, прогонов {runs}){tail}":
        "{name} (chance ×{prob}, ME {me}, TE {te}, runs {runs}){tail}",
    "Декриптор режима «Один для всех»": "Decryptor for “One for all” mode",
    "Разрешены для авто-выбора": "Allowed for auto selection",
    "Авто перебирает «без декриптора» и отмеченные (все отмечены — все 8). Декриптор без цены на рынке "
    "недоступен: заданный вручную — тогда берётся авто-выбор с пометкой в «Подготовка / проблемы».":
        "Auto tries “no decryptor” and the checked ones (all checked — all 8). A decryptor with no market "
        "price is unavailable: if it was set manually, the auto choice is used instead, with a note in "
        "“Preparation / issues”.",
    "Для конкретных T2-товаров (сильнее режима)": "For specific T2 products (overrides the mode)",
    "Добавить T2-товар (напр. Nomad)…": "Add a T2 product (e.g. Nomad)…",
    "тип {id} (не декриптор)": "type {id} (not a decryptor)",
    "Инвента: для авто-выбора не отмечен ни один декриптор — отметь хотя бы один "
    "или выбери режим «Без декриптора».":
        "Invention: no decryptor is checked for auto selection — check at least one "
        "or choose “No decryptor” mode.",
    # --- Производство: всегда покупать / строить
    "Всегда покупать (не строить)": "Always buy (never build)",
    "Эти предметы/группы Forge никогда не строит как компонент — берёт с рынка (напр. все Fuel Block).":
        "Forge never builds these items/groups as components — it buys them on the market "
        "(e.g. all Fuel Blocks).",
    "Группа (напр. Fuel Block)…": "Group (e.g. Fuel Block)…",
    "Всегда строить (не покупать)": "Always build (never buy)",
    "Строить, даже если рынок дешевле (если чертёж есть). «Всегда покупать» сильнее, если предмет в "
    "обоих списках.":
        "Build even if the market is cheaper (if you have the blueprint). “Always buy” wins if an item is "
        "in both lists.",
    # --- Станции
    "Станции (структуры) — параметры джобов": "Stations (structures) — job parameters",
    "ME/время — из реально фитованных ригов и типа структуры (считаем сами из SDE, со стэкинг-пенальти и "
    "бонусом low/null-sec по системе станции). Стоимость джоба и налог — вручную. Группы/категории "
    "ограничивают, к каким продуктам применяется станция — можно завести несколько станций одной роли под "
    "разные группы. Система станции (авто — из структуры) задаёт индекс стоимости её джобов; перевозка "
    "материалов между системами не считается — они считаются доставленными в систему стройки.":
        "ME/time come from the rigs actually fitted and the structure type (computed by Forge from the SDE, "
        "with stacking penalties and the low/null-sec bonus for the station's system). Job cost and tax are "
        "set manually. Groups/categories limit which products the station applies to — you can add several "
        "stations of the same role for different groups. The station's system (auto — from the structure) "
        "sets the cost index of its jobs; moving materials between systems is not counted — they are "
        "treated as delivered to the build system.",
    "Добавить станцию": "Add station",
    "Новая станция": "New station",
    "Станция": "Station",
    "Удалить станцию": "Delete station",
    "Где стоит:": "Located at:",
    "— не указано —": "— not set —",
    "Структура в EVE (для подписей склада/чертежей). Можно вписать id вручную.":
        "The structure in EVE (for stock/blueprint labels). You can type an id manually.",
    "Система:": "System:",
    "По этой системе — индекс стоимости джобов станции и security-модификатор бонусов ригов "
    "(в low/null-sec риги дают больше). Логистика между системами НЕ считается: материалы — "
    "доставленными в систему стройки.":
        "This system sets the station's job cost index and the security modifier of rig bonuses "
        "(rigs give more in low/null-sec). Logistics between systems is NOT counted: materials are "
        "treated as delivered to the build system.",
    "Авто — система из структуры «Где стоит»": "Auto — system of the “Located at” structure",
    "Выбрать систему станции вручную (если не в системе стройки)…":
        "Pick the station's system manually (if not in the build system)…",
    "выбрана вручную: {name}{sec}": "set manually: {name}{sec}",
    "авто: {name}{sec} ({how})": "auto: {name}{sec} ({how})",
    "авто: {name}{sec}": "auto: {name}{sec}",
    "авто — по структуре «Где стоит» (определится после сохранения)":
        "auto — from the “Located at” structure (resolved after saving)",
    "Тип структуры:": "Structure type:",
    "не выбран — встроенный бонус (напр. Tatara −25% времени реакций) не учитывается":
        "not selected — the built-in bonus (e.g. Tatara −25% reaction time) is not applied",
    "не выбран — встроенный бонус структуры не учитывается":
        "not selected — the structure's built-in bonus is not applied",
    "Сбросить тип структуры": "Reset structure type",
    "Найти тип структуры (Raitaru, Azbel, Sotiyo, Athanor, Tatara)…":
        "Find a structure type (Raitaru, Azbel, Sotiyo, Athanor, Tatara)…",
    "Риги и сервис-модули (бонусы считаются из SDE)": "Rigs and service modules (bonuses computed from the SDE)",
    "Найти риг (напр. Standup XL-Set Ship Manufacturing Efficiency)…":
        "Find a rig (e.g. Standup XL-Set Ship Manufacturing Efficiency)…",
    "риги не выбраны — используются ручные % ниже": "no rigs selected — the manual % below are used",
    "материалы": "materials",
    "время": "time",
    "стоим. джоба": "job cost",
    "Расчётный бонус (по сохранённому фиту): {bonuses}": "Computed bonus (from the saved fit): {bonuses}",
    "Только без ригов и типа структуры": "Only when no rigs and no structure type are set",
    "ME эконом., %": "ME savings, %",
    "Время эконом., %": "Time savings, %",
    "Стоим. джоба эконом., %": "Job cost savings, %",
    "Налог станции, %": "Station tax, %",
    "Налог владельца структуры": "Tax set by the structure owner",
    "Какие продукты сюда (пусто — все в этой роли)": "Which products go here (empty — all in this role)",
    "EVE-группа продукта…": "Product EVE group…",
    "EVE-категория продукта (для XL/L ригов EC)…": "Product EVE category (for XL/L EC rigs)…",
    # --- Логистика и рынок: места и структуры
    "Места": "Locations",
    "Три роли логистики Forge: хаб закупки (Jita), рынок сбыта и второй хаб (C-J6MT), место стройки "
    "(GPLB-C). Имя системы резолвится из SDE; регион рынка — для цен. Названия идут в отчёты и подписи.":
        "Forge's three logistics roles: purchasing hub (Jita), sales market and second hub (C-J6MT), build "
        "location (GPLB-C). The system name is resolved from the SDE; the market region is used for prices. "
        "The names appear in reports and labels.",
    "Хаб закупки": "Purchasing hub",
    "Рынок сбыта / 2-й хаб": "Sales market / 2nd hub",
    "Место стройки": "Build location",
    "система {name} · id {id} · регион {region}": "system {name} · id {id} · region {region}",
    "сменить систему…": "change system…",
    "Структуры": "Structures",
    "Рынок-структура по умолчанию (если список рынков ниже пуст)":
        "Default market structure (if the market list below is empty)",
    "Engineering Complex места стройки (склад «Авто»)": "Build location Engineering Complex (“Auto” stock)",
    "Refinery места стройки (склад «Авто»)": "Build location Refinery (“Auto” stock)",
    # --- Логистика и рынок: рынок, фрахт, самоходные
    "Рынок: где покупать и как считать продажу": "Market: where to buy and how to price sales",
    "Покупать в {hub} (+ доставка)": "Buy in {hub} (+ delivery)",
    "Покупать в {hub}": "Buy in {hub}",
    "Оба выключены = оба разрешены. Выбор хаба — самый дешёвый landed с учётом наличия объёма.":
        "Both off = both allowed. The hub chosen is the one with the cheapest landed cost, given the "
        "available volume.",
    "Продажа: выставить sell-ордер": "Sale: place a sell order",
    "Продажа: сразу в buy-ордер": "Sale: straight into buy orders",
    "Фрахт (плечи логистики)": "Freight (logistics legs)",
    "per_m3 — линейно ISK/м³ (+ минимум за заказ); fixed_jump — рейсами: 1-я партия = один прыжок, "
    "каждая следующая = ещё два (туда-обратно).":
        "per_m3 — linear ISK/m³ (+ a minimum per order); fixed_jump — by trips: the 1st load = one jump, "
        "each additional load = two more (there and back).",
    "рейсами": "by trips",
    "ISK/м³": "ISK/m³",
    "мин. за заказ": "min. per order",
    "ISK за прыжок": "ISK per jump",
    "вместимость, м³": "capacity, m³",
    "загрузка 0–1": "load factor 0–1",
    "Самоходные при вывозе": "Ships that fly out themselves",
    "Корабли этих групп летят на рынок сами — вывоз считается как топливо за прыжок (ISK за прыжок плеча "
    "«стройка → рынок») за штуку, а не объём × ставка.":
        "Ships of these groups fly to the market on their own — haul-out is counted as fuel per jump "
        "(“ISK per jump” of the “build → market” leg) per unit, not volume × rate.",
    "Группа (напр. Dreadnought, Carrier)…": "Group (e.g. Dreadnought, Carrier)…",
    "не выбрано — вывоз всех кораблей по объёму": "none selected — all ships are hauled out by volume",
    # --- Логистика и рынок: рынки-структуры
    "Рынки-структуры (ордера — при синке персонажей)": "Market structures (orders fetched in character sync)",
    "Ордера всех перечисленных структур сводятся в ОДИН срез рынка {market}: минимальный sell, "
    "максимальный buy, суммарные объёмы. Каждую тянет персонаж со скоупом рынков структур — "
    "сначала те, у кого там лежат ассеты; нет доступа — пробуется следующий, ошибка одной "
    "структуры не мешает остальным (итог по каждой — «Обзор → Источники данных»).":
        "Orders from all listed structures are merged into ONE {market} market snapshot: lowest sell, "
        "highest buy, total volumes. Each one is fetched by a character with the structure markets scope — "
        "those with assets there go first; if access is denied, the next one is tried, and an error in one "
        "structure doesn't affect the others (the result for each — “Overview → Data sources”).",
    "— известная структура —": "— known structure —",
    "система ?": "system ?",
    "{name} · {where} (не в системе хаба)": "{name} · {where} (not in the hub system)",
    "id структуры": "structure id",
    "По id": "By id",
    "Список пуст — синкается «Рынок-структура по умолчанию» (id {id}).":
        "The list is empty — the “Default market structure” (id {id}) is synced.",
    "Список пуст и рынок-структура по умолчанию не задана — ордера структур не синкаются.":
        "The list is empty and no default market structure is set — structure orders are not synced.",
    # запасное имя системы хаба (её нет в SDE) в «не в системе хаба {hub}»
    "хаба": "?",
    "Структура {id}": "Structure {id}",
    "⚠ {system}: не в системе хаба {hub} — ордера всё равно попадут в срез рынка сбыта":
        "⚠ {system}: not in hub system {hub} — orders still go into the sales market snapshot",
    "система не известна — структура ещё не резолвлена (синк персонажей)":
        "system unknown — structure not resolved yet (character sync)",
    # --- Рекомендации
    "Вес ROI": "ROI weight",
    "Вес ISK/час": "ISK/hour weight",
    "Вес ликвидности": "Liquidity weight",
    "Мин. суточный объём (0 — без фильтра)": "Min. daily volume (0 — no filter)",
    "Потолок вложения на партию, ISK (0 — нет)": "Max investment per batch, ISK (0 — no limit)",
    "Прогонов по умолчанию": "Default runs",
    "Размер ТОП на группу": "Top size per group",
    "Фильтр битых чертежей: себестоимость / цена ≥": "Broken blueprint filter: build cost / price ≥",
    "Отсекает артефакты SDE вроде «1 Tritanium»": "Filters out SDE artifacts like “1 Tritanium”",
    "Винзоризация выбросов, доля": "Outlier winsorization, fraction",
    "«Дешевле купить»: макс. экономия, доля 0–1": "“Cheaper to buy”: max. savings, fraction 0–1",
    "Выше — мусор SDE (компрессия и т.п.)": "Anything above is SDE junk (compression etc.)",
    "Веса и фильтры «Что строить»": "“What to build” weights and filters",
    "Ликвидность — откуда «Объём/сут»": "Liquidity — where “Volume/day” comes from",
    "По записям: история {market}, иначе выставленное на продажу":
        "By records: {market} history, otherwise the volume listed for sale",
    "Реальный оборот {market} (история ESI региона, со структурами)":
        "Actual {market} turnover (regional ESI history, incl. structures)",
    "Оборот {hub} как прокси": "{hub} turnover as a proxy",
    "Источник": "Source",
    "Окно, календарных дней (дни без сделок = 0)": "Window, calendar days (days without trades = 0)",
    "Для «реального оборота» и «Jita»; «по записям» окно не использует":
        "For “actual turnover” and “Jita”; “by records” doesn't use the window",
    "Влияет на колонку «Объём/сут», фильтр «Мин. объём/сут» и вес ликвидности в ТОП, «Из остатков» и "
    "«Дешевле купить» (там рынок — хаб покупки). История {market} качается с «Рынком Jita и индексами» "
    "раз в сутки и только при источнике «Реальный оборот»: в неё попадают и сделки в структурах игроков "
    "(в Insmother нет NPC-станций, а история ESI есть).":
        "Affects the “Volume/day” column, the “Min. volume/day” filter and the liquidity weight in the Top, "
        "“From stock leftovers” and “Cheaper to buy” (there the market is the purchasing hub). {market} "
        "history is downloaded with “Jita market & indices” once a day, and only when the source is "
        "“Actual turnover”: it includes trades in player structures too (Insmother has no NPC stations, "
        "but ESI history is available).",
    "Запреты — никогда не рекомендовать": "Exclusions — never recommend",
    "Не попадут ни в ТОП, ни в «Из остатков», ни в «Дешевле купить» — напр. то, что не хочешь или не "
    "можешь продавать.":
        "These never appear in the Top, “From stock leftovers” or “Cheaper to buy” — e.g. things you don't "
        "want to or can't sell.",
    "«Дешевле купить»: исключить категории": "“Cheaper to buy”: exclude categories",
    "Категория (25 = Asteroid — руда/лёд)…": "Category (25 = Asteroid — ore/ice)…",
    "не исключено ничего": "nothing excluded",
    "Группы ТОП": "Top groups",
    "Для каждой группы — свой ТОП на вкладке «Что строить». Ограничения по числу групп нет.":
        "Each group gets its own Top on the “What to build” tab. There is no limit on the number of groups.",
    "Добавить группу": "Add group",
    "Новая группа": "New group",
    "Группа": "Group",
    "Удалить группу": "Delete group",
    "EVE-группа (напр. Dreadnought)…": "EVE group (e.g. Dreadnought)…",
    "EVE-группы не выбраны": "no EVE groups selected",
    "Значения по умолчанию на вкладке «Что строить»": "Defaults on the “What to build” tab",
    "ТОП: сколько позиций": "Top: number of entries",
    "«Дешевле купить»: топ": "“Cheaper to buy”: top",
    "«Дешевле купить»: мин. объём/сут": "“Cheaper to buy”: min. volume/day",
    "«Из остатков»: топ": "“From stock leftovers”: top",
    # --- Чертежи
    "Стоимость чертежей вручную (ISK на 1 прогон)": "Manual blueprint cost (ISK per run)",
    "Для чертежей, которыми не владеешь и где недоступна инвента. Для T1-чертежа инвенты (напр. Fenrir "
    "Blueprint) — цена одной T1-копии на попытку.":
        "For blueprints you don't own and can't invent. For a T1 invention blueprint (e.g. Fenrir "
        "Blueprint) — the price of one T1 copy per attempt.",
    "Добавить предмет…": "Add an item…",
    "Где искать чертежи": "Where to look for blueprints",
    "Ничего не отмечено — «свои» все чертежи во всех локациях. Отмечено — только лежащие там (включая "
    "контейнеры внутри): влияет на стоимость чертежа, ME/TE, ТОП «свои чертежи» и назначение в расписании. "
    "Склад настраивается отдельно — вкладка «Склад».":
        "Nothing checked — all blueprints in all locations count as “yours”. Checked — only the ones stored "
        "there (including containers inside): affects blueprint cost, ME/TE, the “own blueprints” Top and "
        "job assignment in the schedule. Stock is configured separately — the “Stock” tab.",
    "Нет данных о чертежах — синкни персонажей («Обзор»).": "No blueprint data — sync your characters (“Overview”).",
    "id {id} · система {system}": "id {id} · system {system}",
    "{n} черт.": "{n} BP",
    "id {id} (сейчас там чертежей нет)": "id {id} (no blueprints there now)",
    # --- Планировщик
    "Расписание": "Schedule",
    "Учитывать уже запущенные джобы (их слоты заняты до окончания)":
        "Account for jobs already running (their slots stay busy until they finish)",
    "Ставить копирование T1-чертежа для инвенты в расписание (копи-джобы со своего BPO)":
        "Schedule T1 blueprint copying for invention (copy jobs from your own BPO)",
    "Перед инвентой — копи-джобы в пуле «наука» (по копии на каждый джоб инвенты, минимальные раны), "
    "инвента ждёт свои копии; один BPO — одновременно в одном джобе. Время — из SDE без TE, со скиллами "
    "Science (−5%/ур.) и Advanced Industry (−3%/ур.) и бонусом станции «копирование». Выкл — "
    "стоимость копий учтена, их время в срок не входит.":
        "Before invention — copy jobs in the “science” pool (one copy per invention job, minimum runs); "
        "invention waits for its copies; one BPO can only be in one job at a time. Time — from the SDE "
        "without TE, with the Science (−5%/level) and Advanced Industry (−3%/level) skills and the "
        "“copying” station bonus. Off — copy cost is included, but their time doesn't count "
        "toward the lead time.",
    "Потолок потоков одного компонента при разбивке под срок":
        "Max streams per component when splitting to meet a deadline",
    "«Сравнить варианты»: сроки «Макс. N дней/поток» (через запятую; 0.5 = 12 ч)":
        "“Compare options”: “Max N days/stream” limits (comma-separated; 0.5 = 12 h)",
    "Сроки сравнения: «{value}» — не число": "Comparison limits: “{value}” is not a number",
    "Кому ставить джоб — владелец чертежа": "Who gets the job — blueprint owner",
    "Любому — кто раньше закончит": "Anyone — whoever finishes first",
    "Предпочитать владельца": "Prefer the owner",
    "Только владельцу": "Owner only",
    "«Предпочитать владельца»: допуск, часов — владелец берётся, если закончит не позже самого раннего "
    "на столько":
        "“Prefer the owner”: tolerance, hours — the owner gets the job if they finish no later than the "
        "earliest one plus this much",
    "Владелец — у кого чертёж (для реакций — формула, для инвенты — T1-чертёж) лежит в «Где искать "
    "чертежи». «Любому» — самый быстрый срок, но чертежи придётся передавать между чарами (список — в "
    "предупреждениях расписания и отчёта). «Только владельцу» — меньше передач, срок обычно дольше; если "
    "владельца нет среди назначенных в роли — джоб встанет любому, с предупреждением.":
        "The owner is whoever has the blueprint (for reactions — the formula, for invention — the T1 "
        "blueprint) in “Where to look for blueprints”. “Anyone” gives the fastest lead time, but blueprints "
        "will have to be passed between characters (the list is in the schedule and report warnings). "
        "“Owner only” means fewer transfers and usually a longer lead time; if the owner isn't among those "
        "assigned to the role, the job goes to anyone, with a warning.",
    "Корзина по умолчанию (новый запуск пульта)": "Default basket (when Forge starts)",
    "Объединять общие компоненты": "Consolidate shared components",
    "Авто-потоки для объединённых": "Auto streams for consolidated",
    "ME, если нет своего чертежа": "ME if no own blueprint",
    "TE, если нет своего чертежа": "TE if no own blueprint",
    # --- Система
    "Client ID приложения (developers.eveonline.com)": "Application Client ID (developers.eveonline.com)",
    "Порт callback (http://localhost:PORT/callback)": "Callback port (http://localhost:PORT/callback)",
    "Скоупы": "Scopes",
    "Refresh-токены — в Диспетчере учётных данных Windows (keyring), отдельно для каждого Client ID; не в "
    "БД и не в git. Токен EVE привязан к приложению: сменишь Client ID — персонажей нужно будет добавить "
    "заново («Обзор → Добавить персонажа (EVE SSO)»).":
        "Refresh tokens are kept in Windows Credential Manager (keyring), separately for each Client ID; "
        "not in the DB and not in git. An EVE token is bound to the application: change the Client ID and "
        "you'll have to add your characters again (“Overview → Add character (EVE SSO)”).",
    "Данные": "Data",
    "Настройки": "Settings",
    "База": "Database",
    "Отчёты": "Reports",
    "Лог пульта": "Forge log",
    "Состояние пульта": "Forge state",
    "Показать": "Show",
    "Открыть в Проводнике": "Open in Explorer",
    "forge.toml переписывается при сохранении (комментарии не сохраняются) — справочник с пояснениями: "
    "forge.example.toml.":
        "forge.toml is rewritten on save (comments are not kept) — the annotated reference is "
        "forge.example.toml.",
}
