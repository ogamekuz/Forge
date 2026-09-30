"""Английские переводы: вкладки «Склад» и «Что строить» (desktop/pages/stock.py, recommend.py).

Ключ — русская строка ровно как в коде (с подстановками {name}), значение — английская."""

EN: dict[str, str] = {
    # ================================================================ «Склад» (pages/stock.py)
    # --- роль системы в дереве, флаги ассетов (location_flag ESI)
    "место стройки": "build location",
    "рынок / хаб": "market / hub",
    "не склад · фильтр": "not stock · filter",
    "Трюм кораблей": "Cargo hold",
    "Дронбей": "Drone bay",
    "Флотский ангар": "Fleet hangar",
    "Топливный отсек": "Fuel bay",
    "Ангар истребителей": "Fighter bay",
    "Пусковые истребителей": "Fighter launch tubes",
    "Корабельный ангар (капиталы)": "Ship maintenance bay (capitals)",
    "Отсек боеприпасов": "Ammo hold",
    "Рудный трюм": "Ore hold",
    "Экспедиционный трюм": "Expedition hold",
    "Доставки": "Deliveries",
    "Содержимое контейнеров": "Container contents",
    "Запертое в контейнерах": "Locked in containers",
    "AutoFit (структуры)": "AutoFit (structures)",
    "Ангар станции/структуры": "Station/structure hangar",
    "Скрытые модификаторы": "Hidden modifiers",
    "Отсек трупов": "Corpse bay",
    # --- дерево «Где что лежит»
    "Где что лежит": "Where things are",
    "Авто": "Auto",
    "Выбрать вручную": "Pick manually",
    "Фильтр по системе/станции…": "Filter by system/station…",
    "Взять выбор «Авто» за основу": "Start from the “Auto” selection",
    "Отметить то же, что считает режим «Авто» (структуры стройки + локации чертежей), и дальше "
    "править вручную":
        "Tick the same locations as “Auto” mode (build structures + blueprint locations), then edit "
        "by hand",
    "Развернуть/свернуть всё": "Expand/collapse all",
    "ГДЕ": "WHERE",
    "ПРЕДМЕТОВ": "ITEMS",
    "ОЦЕНКА (JITA)": "VALUE (JITA)",
    "РОЛЬ": "ROLE",
    "Структуры без известной системы — отмечай поштучно (или синкни персонажей)":
        "Structures with an unknown system — tick them one by one (or sync characters)",
    "Галка на системе — весь склад в ней (включая новые структуры)":
        "Ticking a system takes all stock in it (including new structures)",
    "корабль · id {id}": "ship · id {id}",
    "контейнер · id {id}": "container · id {id}",
    "Не склад: всё здесь отсекают фильтры справа — {why}. Галка места тут ни при чём: фильтры "
    "действуют на предметы в выбранных местах.":
        "Not stock: the filters on the right cut everything here — {why}. The location tick has "
        "nothing to do with it: filters apply to items in the selected locations.",
    "Проходят фильтры: {passing} из {items} (отсечено: {why})":
        "Passing filters: {passing} of {items} (cut: {why})",
    "Режим «Авто» — места выбираются сами (структуры стройки + локации чертежей). Переключи на "
    "«Выбрать вручную», чтобы отмечать системы и локации.":
        "“Auto” mode — locations are picked automatically (build structures + blueprint locations). "
        "Switch to “Pick manually” to tick systems and locations.",
    "Склад = структуры стройки из «Настроек» + все локации «Где искать чертежи» (и всё внутри, "
    "кроме отсечённого фильтрами).":
        "Stock = build structures from “Settings” + all “Where to look for blueprints” locations "
        "(and everything inside, except what the filters cut).",
    "системы: {names}": "systems: {names}",
    "отдельных локаций: {n}": "individual locations: {n}",
    "исключений: {n}": "exclusions: {n}",
    "Выбрано — {parts}": "Selected — {parts}",
    "Ничего не выбрано — склад пуст (отметь системы или локации).":
        "Nothing selected — stock is empty (tick systems or locations).",
    "Отмечай системы целиком (включая будущие структуры в них) или отдельные станции/структуры/"
    "контейнеры. Снятая галка внутри выбранной системы — исключение.":
        "Tick whole systems (including future structures in them) or individual stations/structures/"
        "containers. An unticked box inside a selected system is an exclusion.",
    "Авто: структуры стройки + локации «Где искать чертежи».":
        "Auto: build structures + “Where to look for blueprints” locations.",
    "Галка — место входит в склад; фильтры справа действуют на предметы в этих местах: что они "
    "отсекают целиком — серым «не склад». Дерево — сразу по галкам, до сохранения.":
        "A tick means the location counts as stock; the filters on the right apply to items in those "
        "locations: whatever they cut entirely is greyed out as “not stock”. The tree follows the "
        "ticks right away, before saving.",
    "Отмечены локации режима «Авто» — поправь и сохрани.": "“Auto” mode locations ticked — adjust and save.",
    "{n} структур(ы) пока без имени и системы — их не получится выбрать по системе. «Обзор → "
    "Персонажи + рынки структур»: при синке Forge спросит ESI (нужен доступ к докингу у кого-то из "
    "персонажей). Пока их можно отметить поштучно.":
        "{n} structure(s) have no name or system yet — they can't be selected by system. “Overview → "
        "Characters + structure markets”: on sync Forge will ask ESI (one of your characters needs "
        "docking access). Until then you can tick them one by one.",
    "Скрыто кораблей вне ассетов: {ships} ({items} предм.) — ESI отдаёт только их модули и риги, "
    "самих кораблей в ассетах нет (напр. корабль выставлен в контракт). Системы у них нет, в склад "
    "они не входят.":
        "Ships outside assets hidden: {ships} ({items} items) — ESI returns only their modules and "
        "rigs, the ships themselves aren't in assets (e.g. a ship put up in a contract). They have no "
        "system and don't count as stock.",
    # --- фильтры
    "Фильтры": "Filters",
    "Чьи ассеты считать": "Whose assets to count",
    "Ни одна галка не снята = все персонажи.": "Nothing unticked = all characters.",
    "Не считать складом": "Don't count as stock",
    "Модули в слотах фита кораблей и структур": "Modules fitted to ships and structures",
    "Снятие с фита не бесплатно — зафитованное не остаток":
        "Unfitting isn't free — fitted modules aren't leftovers",
    "Собранные корабли (внутри что-то есть)": "Assembled ships (with something inside)",
    "Корабль с фитом/грузом — это чей-то корабль, а не корпус на складе. Сам груз при этом остаётся "
    "складом (если трюм не исключён ниже).":
        "A ship with a fit/cargo is someone's ship, not a hull in stock. Its cargo still counts as "
        "stock (unless the cargo hold is excluded below).",
    "Осторожно: это основной ангар станций/структур — почти весь склад":
        "Careful: this is the main station/structure hangar — almost all of your stock",
    # --- запреты и запас
    "Запреты и запас": "Exclusions and reserve",
    "Никогда не брать со склада": "Never take from stock",
    "предметы не выбраны": "no items selected",
    "Предмет, который не трогать вообще…": "Item to never touch…",
    "группы не выбраны": "no groups selected",
    "EVE-группа, которую не трогать (напр. Fuel Block)…": "EVE group to never touch (e.g. Fuel Block)…",
    "Не трогать N штук (неприкосновенный запас)": "Keep N units untouched (reserve)",
    "Добавить предмет в запас…": "Add an item to the reserve…",
    "— запас не задан": "— no reserve set",
    "Убрать из запаса": "Remove from reserve",
    "Сохранить склад": "Save stock",
    "Сбросить": "Reset",
    "В режиме «Выбрать» ничего не отмечено — склад будет пустым. Сохранено всё равно.":
        "Nothing is ticked in “Pick manually” mode — stock will be empty. Saved anyway.",
    "Склад сохранён — отчёты и «Из остатков» считают по нему.":
        "Stock saved — reports and “From stock leftovers” now use it.",
    # --- «Сейчас на складе»
    "Сейчас на складе": "Currently in stock",
    "Найти на складе…": "Search stock…",
    "Считаю склад…": "Calculating stock…",
    "Предмет": "Item",
    "Можно взять": "Available",
    "Не трогать": "Kept",
    "Оценка (Jita)": "Value (Jita)",
    "Где лежит": "Where it is",
    "ещё {n}": "{n} more",
    "{n} позиций · ~{isk} ISK": "types: {n} · ~{isk} ISK",
    "{n} позиций · ~{isk} ISK · показано {shown}": "types: {n} · ~{isk} ISK · shown: {shown}",
    "Список — ПО СОХРАНЁННОЙ настройке: выбранные локации + «Чьи ассеты считать», «Не считать "
    "складом», запреты и запас (после «Сохранить склад» обновится). Оценка — по sell Jita, только "
    "для ориентира.":
        "The list follows the SAVED settings: selected locations + “Whose assets to count”, “Don't "
        "count as stock”, exclusions and reserve (refreshes after “Save stock”). Value is by Jita "
        "sell, for reference only.",

    # ========================================================== «Что строить» (pages/recommend.py)
    "ТОП «что строить»": "Top “what to build”",
    "Дешевле купить": "Cheaper to buy",
    "Из остатков склада": "From stock leftovers",
    "Источник — «Настройки → Рекомендации».": "Source: “Settings → Recommendations”.",
    "В корзине: {n} — «Калькулятор» / «Расписание»": "In basket: {n} — “Calculator” / “Schedule”",
    "В корзину: {name} ×{runs}": "Added to basket: {name} ×{runs}",
    "{name} уже в корзине": "{name} is already in the basket",
    "В корзину (Калькулятор/Расписание)": "Add to basket (Calculator/Schedule)",
    "В корзину с подобранным числом прогонов": "Add to basket with the suggested number of runs",
    "в корзину": "to basket",
    "{n} поз.": "items: {n}",
    "Результат": "Result",
    # --- колонки
    "ISK/час": "ISK/hour",
    "Объём/сут": "Volume/day",
    "Вложение": "Investment",
    "Строить": "Build",
    "Купить": "Buy",
    "Хаб": "Hub",
    "Экономия": "Savings",
    "Прогонов": "Runs",
    "Реальная прибыль": "Real profit",
    "Реальный ROI": "Real ROI",
    "Со склада": "From stock",
    "% себест.": "% of cost",
    # --- фильтры
    "Только свои чертежи": "Own blueprints only",
    "Кандидаты — из чертежей персонажей (в локациях «Где искать чертежи»); иначе — все производимые":
        "Candidates come from your characters' blueprints (in “Where to look for blueprints” "
        "locations); otherwise — everything buildable",
    "нет": "none",
    "все": "all",
    "Топ": "Top",
    "Бюджет, ISK": "Budget, ISK",
    "Мин. объём/сут": "Min. volume/day",
    "Лимит кандидатов": "Candidate limit",
    # --- ТОП
    "Показать": "Show",
    "Группы ТОП, веса и «запреты» — «Настройки → Рекомендации». Двойной клик по строке или «+ в "
    "корзину» — добавить в корзину.":
        "Top groups, weights and “exclusions” — “Settings → Recommendations”. Double-click a row or "
        "“+ to basket” to add it to the basket.",
    "Считаю кандидатов (себестоимость каждого — полный расчёт)…":
        "Calculating candidates (full build cost for each)…",
    "Общий ТОП": "Overall top",
    "ТОП · {name}": "Top · {name}",
    "Нет кандидатов в этой группе (проверь свои чертежи / состав группы).":
        "No candidates in this group (check your blueprints / the group's contents).",
    "Нет подходящих позиций (проверь данные рынка/чертежей и фильтры).":
        "No matching items (check market/blueprint data and filters).",
    # --- дешевле купить
    "Дешевле купить, чем строить": "Cheaper to buy than build",
    "Предметы, которые рынок продаёт дешевле твоей себестоимости постройки (landed до места "
    "стройки). Без групп — весь торгуемый рынок (~10 с); выбери группы, чтобы сузить.":
        "Items the market sells for less than your build cost (landed at the build location). "
        "Without groups — the whole traded market (~10 s); pick groups to narrow it down.",
    "Сканировать рынок": "Scan market",
    "Сузить по EVE-группе (напр. Hybrid Charge, Cruiser)…": "Narrow by EVE group (e.g. Hybrid Charge, Cruiser)…",
    "группы не выбраны — сканируется весь рынок": "no groups selected — scanning the whole market",
    "Считаю себестоимость по рынку…": "Calculating build costs across the market…",
    "Ничего не нашлось — всё выгоднее строить (или подними «мин. объём»).":
        "Nothing found — everything is cheaper to build (or raise “min. volume”).",
    # --- из остатков
    "Что построить из остатков": "What to build from leftovers",
    "Ищет среди своих чертежей постройки с максимальной реальной прибылью (себестоимость минус то, "
    "что уже лежит на складе) и использованием остатков. Размер партии подбирается сам — чтобы "
    "вычерпать самый дефицитный пересекающийся со складом материал. Что считать складом — вкладка "
    "«Склад».":
        "Searches your blueprints for builds with the highest real profit (build cost minus what's "
        "already in stock) and use of leftovers. The batch size is chosen automatically to use up the "
        "scarcest material shared with stock. What counts as stock — the “Stock” tab.",
    "без резервов строек": "excluding build reservations",
    "Не предлагать пускать в дело то, что уже зарезервировали незавершённые стройки":
        "Don't suggest using what unfinished builds have already reserved",
    "Проверить остатки": "Check leftovers",
    "Только эти EVE-группы продуктов (напр. Cruiser)…": "Only these EVE product groups (e.g. Cruiser)…",
    "группы не выбраны — все свои чертежи": "no groups selected — all your blueprints",
    "Сверяю склад с чертежами…": "Matching stock against blueprints…",
    "Ничего не нашлось — склад пуст (проверь вкладку «Склад») или ни один свой чертёж не "
    "пересекается с тем, что на нём лежит.":
        "Nothing found — stock is empty (check the “Stock” tab) or none of your blueprints use "
        "anything in it.",
}
