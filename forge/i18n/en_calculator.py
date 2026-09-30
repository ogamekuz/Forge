"""Английские переводы: вкладка «Калькулятор» (desktop/pages/calculator.py, basket_panel.py).

Ключ — русская строка ровно как в коде (с подстановками {name}), значение — английская."""

EN: dict[str, str] = {
    # ================================================================ calculator.py
    # --- кнопки и опции (левая панель)
    "Посчитать корзину": "Calculate basket",
    "Сформировать отчёт по стройке": "Generate build report",
    "HTML-отчёт: закупки (с вычетом склада), инструкции по чарам, контроль сроков и стоимости. "
    "Откроется в браузере.":
        "HTML report: purchases (minus stock), per-character instructions, deadline and cost tracking. "
        "Opens in the browser.",
    "учитывать склад": "use stock",
    "Вычитать из закупок то, что уже лежит на складе ([stock])":
        "Subtract what's already in stock ([stock]) from purchases",
    "резервы строек": "build reservations",
    "Уменьшать доступный склад на то, что зарезервировали незавершённые стройки":
        "Reduce available stock by what unfinished builds have reserved",
    "Сравнить ME главного чертежа": "Compare main blueprint ME",
    "Себестоимость этого предмета при ME его чертежа 0–10 — остальное дерево не меняется":
        "Build cost of this item at its blueprint ME 0–10 — the rest of the tree stays the same",
    "Работает с одним предметом в корзине": "Works with a single item in the basket",
    "Добавь предметы в корзину и нажми «Посчитать корзину». Корзина общая с «Расписанием»; "
    "из «Что строить» предметы добавляются кнопкой «+ в корзину».":
        "Add items to the basket and press “Calculate basket”. The basket is shared with “Schedule”; "
        "on “What to build”, items are added with the “+ to basket” button.",
    # --- ход расчёта
    "Считаю себестоимость корзины…": "Calculating basket build cost…",
    "Формирую отчёт по стройке (расписание, склад, закупки)…":
        "Generating build report (schedule, stock, purchases)…",
    "Отчёт сохранён: reports/{file} — открыт в браузере": "Report saved: reports/{file} — opened in the browser",
    "Считаю себестоимость при ME 0…10…": "Calculating build cost at ME 0…10…",
    # --- сравнение по ME
    "Сравнение по ME главного чертежа": "Main blueprint ME comparison",
    "У этого чертежа ME задаёт декриптор инвенты (авто-оптимум по стоимости или выбранный в "
    "«Настройки → Производство → Инвента») — свободного параметра 0–10 нет, сравнение недоступно.":
        "This blueprint's ME is set by the invention decryptor (cost-optimal auto pick or the one chosen in "
        "“Settings → Manufacturing → Invention”) — there's no free 0–10 parameter, so comparison is unavailable.",
    "{me} (тек.)": "{me} (cur.)",
    "Зелёным — самая низкая себестоимость, cyan — наибольшая прибыль. «тек.» — текущее поле "
    "«ME, если нет своего».":
        "Green — lowest build cost, cyan — highest profit. “cur.” — the current “ME if no own blueprint” "
        "value.",
    # --- итоги и колонки
    "Итого по корзине": "Basket totals",
    "Себестоимость": "Build cost",
    "Выручка": "Revenue",
    "Прибыль": "Profit",
    "Материалы": "Materials",
    "Установка джобов": "Job installation",
    "Чертежи / инвента": "Blueprints / invention",
    "Позиций": "Items",
    "За штуку": "Per unit",
    "Джобы": "Jobs",
    "По предметам": "By item",
    # --- дерево материалов
    "Материал": "Material",
    "Кол-во": "Qty",
    "Источник": "Source",
    "Сумма": "Total",
    "{name}   ♻ переработка": "{name}   ♻ reprocessing",
    "Дешевле переработать «{src}», чем строить/купить напрямую (побочка сверху: {credit})":
        "Cheaper to reprocess “{src}” than to build/buy directly (by-products on top: {credit})",
    "✋ купить": "✋ buy",
    "Принудительно купить (клик — вернуть авто-решение)": "Forced buy (click to restore the auto decision)",
    "строить": "build",
    "Клик — купить вместо постройки": "Click to buy instead of building",
    "нет цены": "no price",
    "Нет цены — переключить нельзя": "No price — can't switch",
    "⚠ купить": "⚠ buy",
    "купить": "buy",
    "Клик — зафиксировать «купить» (не строить)": "Click to lock “buy” (don't build)",
    "доступно {n}": "{n} available",
    "доступно {n} — не хватает на нужное количество": "{n} available — not enough for the required quantity",
    "Материалы — клик по «источнику» переключает строить ↔ купить":
        "Materials — click the “source” to switch build ↔ buy",
    # --- подготовка / проблемы
    "Подготовка / проблемы": "Preparation / issues",
    " · декриптор «{name}»": " · decryptor “{name}”",
    " · без декриптора": " · no decryptor",
    " (задано вручную)": " (set manually)",
    "недостача ранов у своей копии": "own copy is short of runs",
    "нужен T1-чертёж + датакоры": "needs a T1 blueprint + datacores",
    ", шанс {pct}": ", chance {pct}",
    " → {n} шт.": " → {n} units",
    "Заинвентить: {name}{produced}{dec} ({attempts} попыт. Т1-копий{chance}, план округлён вверх; {what}).":
        "Invent: {name}{produced}{dec} ({attempts} attempts on T1 copies{chance}, plan rounded up; {what}).",
    "⚠ Инвента {name}: {note}. Проверь цену декриптора или «Настройки → Производство → Инвента».":
        "⚠ Invention {name}: {note}. Check the decryptor price or “Settings → Manufacturing → Invention”.",
    "⚠ Нет T1-чертежа для инвенты: {src} — нужен, чтобы заинвентить {prod}. Купи BPO/BPC или впиши цену "
    "в «Настройки → Чертежи».":
        "⚠ No T1 blueprint for invention: {src} — needed to invent {prod}. Buy a BPO/BPC or enter a price "
        "in “Settings → Blueprints”.",
    "⚠ Нет чертежа: {name} — себестоимость занижена; задай цену в «Настройки → Чертежи».":
        "⚠ No blueprint: {name} — build cost is understated; set a price in “Settings → Blueprints”.",
    "⚠ Не хватает копий формулы реакции: {name} — нужно {needed}, есть {owned}. Докупи копии или "
    "уменьши число потоков.":
        "⚠ Not enough reaction formula copies: {name} — need {needed}, have {owned}. Buy more copies or "
        "reduce the number of streams.",
    "⚠ Не хватает ранов у своей копии: {name} — нужно {needed}, осталось {owned}.":
        "⚠ Own copy is short of runs: {name} — need {needed}, {owned} left.",
    "Нет рыночной цены: {name} — не учтено в стоимости.": "No market price: {name} — not included in the cost.",
    "Закрой эти пробелы — и отчёт сформируется без «дыр» в себестоимости. Передачи чертежей между "
    "чарами видны в самом отчёте.":
        "Close these gaps and the report will have no “holes” in the build cost. Blueprint transfers "
        "between characters are shown in the report itself.",
    # --- остатки переработки
    "Останется от переработки": "Reprocessing leftovers",
    "Побочные продукты переработки — физически осядут на складе {build}. В себестоимость не входят — "
    "бонус сверху, если продать (цена — с налогом/брокером и вывозом в {cj}).":
        "Reprocessing by-products — they will physically end up in stock at {build}. Not included in the "
        "build cost — a bonus on top if sold (price net of sales tax/broker fee and haul-out to {cj}).",
    "Если продать": "If sold",
    "нет цены в {place}": "no price at {place}",
    "Итого по цене продажи: {isk} ISK": "Total at sell price: {isk} ISK",
    # --- карточка предмета
    "→ {n} шт.": "→ {n} units",
    "Цена продажи": "Sell price",
    "Материалы (всего)": "Materials (total)",
    "Джобы (всего)": "Jobs (total)",
    "Чертежи (всего)": "Blueprints (total)",
    "Логистика: входящий фрахт {inbound} ISK (зашит в материалы) · вывоз {export} ISK ({mode}, "
    "вычтен из выручки)":
        "Logistics: inbound freight {inbound} ISK (included in materials) · haul-out {export} ISK ({mode}, "
        "deducted from revenue)",
    "топливо за прыжок": "fuel per jump",
    "по объёму": "by volume",
    "Установка джоба (основной чертёж) — разбивка": "Job installation (main blueprint) — breakdown",
    "Индекс — системы станции этого джоба («Настройки → Станции»: своя или система стройки). Логистика "
    "между системами не считается: материалы — доставленными в {build}.":
        "The index is that of this job's station system (“Settings → Stations”: its own or the build "
        "system). Logistics between systems isn't counted: materials are taken as delivered to {build}.",
    "EIV (Σ adjusted_price × база × runs)": "EIV (Σ adjusted_price × base × runs)",
    "× индекс системы{sys}": "× system cost index{sys}",
    "× множитель станции": "× station multiplier",
    "+ налог + SCC (от EIV)": "+ facility tax + SCC (of EIV)",
    "= Установка джоба": "= Job installation",
    # --- инвента
    "Датакоры": "Datacores",
    "T1-копия (источник)": "T1 copy (source)",
    " (индекс {pct}, {name})": " (cost index {pct}, {name})",
    " (индекс {pct})": " (cost index {pct})",
    "Декриптор": "Decryptor",
    "Джоб-взнос инвенты": "Invention job fee",
    "= за попытку": "= per attempt",
    "Базовый шанс (SDE)": "Base chance (SDE)",
    "× скиллы — не учитываются (Настройки → Производство)": "× skills — not applied (Settings → Manufacturing)",
    "× скиллы, лучший инвентор: {name}": "× skills, best inventor: {name}",
    "× скиллы — нет назначенных на «Науку» (вкладка «Персонажи»)":
        "× skills — nobody assigned to “Science” (Characters tab)",
    "× декриптор": "× decryptor",
    "= Вероятность успеха": "= Success chance",
    "Прогонов на копию": "Runs per copy",
    "Попыток (Т1-копий), округлено вверх": "Attempts (T1 copies), rounded up",
    "Чертёж — инвента{dec}": "Blueprint — invention{dec}",
    "{isk}/прогон": "{isk}/run",
    "= вся инвента (справочно)": "= all invention (for reference)",
    # --- заметки на карточке предмета
    "⚠ Нет T1-чертежа для инвенты: «{src}». Нужен BPO (для копий) или покупка BPC — иначе инвенту "
    "не запустить.":
        "⚠ No T1 blueprint for invention: “{src}”. You need a BPO (for copies) or a purchased BPC — "
        "otherwise invention can't be started.",
    "⚠ Нет цены чертежа для «{name}» (не во владении, инвента недоступна). Задай вручную: "
    "«Настройки → Чертежи → Стоимость чертежей».":
        "⚠ No blueprint price for “{name}” (not owned, invention unavailable). Set it manually: "
        "“Settings → Blueprints → Manual blueprint cost”.",
    "⚠ Не хватает копий формулы реакции «{name}»: нужно {needed} (под {streams} потоков), есть {owned}.":
        "⚠ Not enough reaction formula copies for “{name}”: need {needed} (for {streams} streams), "
        "have {owned}.",
    " Заинвентить недостачу: {dec}{manual}, {attempts} попыт. Т1-копий{chance}.":
        " Invent the shortfall: {dec}{manual}, {attempts} attempts on T1 copies{chance}.",
    "декриптор «{name}»": "decryptor “{name}”",
    "без декриптора": "no decryptor",
    " (шанс {pct})": " (chance {pct})",
    "⚠ Не хватает ранов у своей копии «{name}»: нужно {needed} прогонов, осталось {owned}.{extra}":
        "⚠ Own copy of “{name}” is short of runs: need {needed} runs, {owned} left.{extra}",
    "⚠ Инвента: {note}.": "⚠ Invention: {note}.",
    "Нет чертежа — этот предмет нельзя построить (мета/дроп). Только покупка: {isk} ISK.":
        "No blueprint — this item can't be built (meta/drop). Buy only: {isk} ISK.",
    "Нет чертежа — этот предмет нельзя построить (мета/дроп). Только покупка.":
        "No blueprint — this item can't be built (meta/drop). Buy only.",
    "Выгоднее строить: {cost} против {buy} за готовый.": "Building is cheaper: {cost} vs {buy} ready-made.",
    "Выгоднее купить готовым: {buy} против {cost}.": "Buying ready-made is cheaper: {buy} vs {cost}.",
    "⚠ Нет цен для {n} материал(ов).": "⚠ No prices for {n} material(s).",
    # --- «только покупка»
    "{n} шт. · только покупка": "{n} units · buy only",
    "За штуку (до {place})": "Per unit (to {place})",
    "{hub} + доставка": "{hub} + delivery",
    "Считаем как материал: дешевле купить в {hub}.": "Treated as a material: cheaper to buy at {hub}.",
    " ⚠ На хабе не хватает объёма под количество.": " ⚠ The hub doesn't have enough volume for this quantity.",
    " Входящий фрахт {isk} ISK.": " Inbound freight {isk} ISK.",

    # ================================================================ basket_panel.py
    # --- вставка фита
    "Вставить фит из буфера EVE": "Paste fit from EVE clipboard",
    "Скопируй фит (Ctrl+C в окне фита) или список из груза/ангара (Ctrl+C по выделенным строкам) и "
    "вставь сюда. Имена сопоставляются точно — как их отдаёт клиент EVE.":
        "Copy a fit (Ctrl+C in the fitting window) or a list from cargo/hangar (Ctrl+C on selected rows) "
        "and paste it here. Names are matched exactly — as the EVE client gives them.",
    "[Ishtar, мой фит]\nDrone Damage Amplifier II\n…\nHammerhead II x5":
        "[Ishtar, my fit]\nDrone Damage Amplifier II\n…\nHammerhead II x5",
    "Добавить в корзину": "Add to basket",
    "Не удалось распознать текст фита.": "Couldn't recognize the fit text.",
    "Добавлено позиций: {n} · не найдено: {names}": "Items added: {n} · not found: {names}",
    "Готово — добавить найденное": "Done — add what was found",
    # --- строка корзины
    "Убрать из корзины": "Remove from basket",
    "точный ME": "exact ME",
    "Нет чертежа — только покупка (считается как материал: где дешевле)":
        "No blueprint — buy only (treated as a material: wherever it's cheaper)",
    "Решение: КУПИТЬ готовым (в отчёте уйдёт в закупку). Клик — строить.":
        "Decision: BUY ready-made (goes to purchasing in the report). Click to build.",
    "Решение: СТРОИТЬ. Клик — купить готовым.": "Decision: BUILD. Click to buy ready-made.",
    "Количество к покупке": "Quantity to buy",
    "Прогонов на этот предмет": "Runs for this item",
    "Покупка — потоки не применяются": "Buying — streams don't apply",
    "Параллельных потоков (джобов) для этого предмета": "Parallel streams (jobs) for this item",
    "Точный ME/TE включён — клик вернёт авто (свой чертёж/дефолт)":
        "Exact ME/TE is on — click to return to auto (own blueprint/default)",
    "Задать точный ME/TE этого товара (вместо своего чертежа/дефолта). Не действует на Т2 из инвенты — "
    "там ME/TE задаёт декриптор.":
        "Set an exact ME/TE for this item (instead of own blueprint/default). Doesn't apply to T2 from "
        "invention — there the decryptor sets ME/TE.",
    # --- панель корзины
    "Корзина": "Basket",
    "Очистить корзину": "Clear basket",
    "Добавить предмет в корзину…": "Add an item to the basket…",
    "Вставить фит / список из буфера EVE": "Paste fit / list from EVE clipboard",
    "ПРЕДМЕТ": "ITEM",
    "ПРОГ.": "RUNS",
    "ПОТ.": "STRM",
    "Корзина пуста — найди корабль/модуль выше или вставь фит.":
        "The basket is empty — find a ship/module above or paste a fit.",
    "Настройки расчёта": "Calculation settings",
    "нет": "none",
    "Макс. дней на поток (компоненты)": "Max days per stream (components)",
    "Дробит ПОД-компоненты (реакции, детали) так, чтобы каждый их джоб был ≤ N дней — волнами. Меняет "
    "расход материалов (ceil на каждый джоб). Верхний продукт дробится полем «потоки». Пусто — "
    "под-компоненты в 1 поток.":
        "Splits SUB-components (reactions, parts) so that each of their jobs is ≤ N days — in waves. Changes "
        "material usage (ceil per job). The top product is split by the “streams” field. Empty — "
        "sub-components in 1 stream.",
    "ME, если нет своего чертежа": "ME if no own blueprint",
    "TE, если нет своего чертежа": "TE if no own blueprint",
    "Срок для чертежей, которых нет ни у одного персонажа И которые не добываются инвентой. Свой "
    "чертёж — TE берётся с копии; инвента — TE итоговой BPC от декриптора.":
        "Time for blueprints that no character has AND that aren't obtained by invention. Own blueprint — "
        "TE is taken from the copy; invention — TE of the resulting BPC comes from the decryptor.",
    "Оптимизация: сам решает строить/купить": "Optimization: decide build/buy automatically",
    "По каждому компоненту: строить или купить — что дешевле":
        "For each component: build or buy — whichever is cheaper",
    "Объединять общие компоненты": "Consolidate shared components",
    "Одинаковый под-компонент в разных ветках (в т.ч. в разных товарах корзины) строить ОДНОЙ общей "
    "постройкой — экономия слотов и материалов (округление считается один раз на суммарный спрос)":
        "Build the same sub-component in different branches (incl. different basket items) as ONE shared "
        "build — saves slots and materials (rounding is applied once to the total demand)",
    "Авто-потоки для объединённых": "Auto streams for consolidated",
    "Объединение теряет параллелизм: общая постройка идёт одним джобом. Эта галка даёт ей столько "
    "потоков, сколько веток в неё слито.":
        "Consolidation loses parallelism: the shared build runs as one job. This checkbox gives it as many "
        "streams as branches were merged into it.",
    "{n} поз.": "items: {n}",  # как в en_stock
    "Под-компонентов переключено «строить → купить»: {n} (клик по «источник» в дереве Калькулятора).":
        "Sub-components switched “build → buy”: {n} (click “source” in the Calculator tree).",
    "Добавлено из буфера: {n} поз.": "Added from clipboard — items: {n}",
}
