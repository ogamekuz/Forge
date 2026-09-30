"""Английские переводы: HTML-отчёт по стройке и список отчётов (web/report.py, web/app.py).

Ключ — русская строка ровно как в коде (с подстановками {name}), значение — английская."""

EN: dict[str, str] = {
    # --- числа и общие слова
    "{v} млрд": "{v}B",
    "{v} млн": "{v}M",
    "{v} тыс": "{v}k",
    "{v} м³": "{v} m³",
    "вкл": "on",
    "выкл": "off",
    "Некорректный report_id": "Invalid report_id",
    # --- payload: заголовок стройки и статьи контроля стоимости
    "Пустая корзина": "Empty basket",
    "{head} (+{n} поз.)": "{head} (+{n} more)",
    "Материалы — {place}": "Materials — {place}",
    "Фрахт (доставка до {place})": "Freight (delivery to {place})",
    "Взносы за джобы": "Job fees",
    "Чертежи / инвента (датакоры, копии)": "Blueprints / invention (datacores, copies)",
    # --- шапка отчёта
    "← Все стройки": "← All builds",
    "отчёт по стройке": "build report",
    "ME {me} · оптимизация {build} · объединение {consolidate}":
        "ME {me} · optimization {build} · consolidation {consolidate}",
    " · макс. {days} дн/поток": " · max {days} d/stream",
    # --- сводка
    "Сводка по стройке": "Build summary",
    "Себестоимость": "Build cost",
    "Выручка": "Revenue",
    "Прибыль": "Profit",
    "Срок (ETA)": "ETA",
    "Предметов": "Items",
    "Джобов": "Jobs",
    "Персонажей": "Characters",
    "Готовность ориентировочно к {eta} при старте всех джобов сейчас.":
        "Estimated completion by {eta} if all jobs start now.",
    # --- 1 · список закупок
    "1 · Список закупок": "1 · Shopping list",
    "Купить в {place}": "Buy in {place}",
    "Скопировать список для мультипокупки EVE": "Copy the list for EVE multibuy",
    "📋 копировать": "📋 copy",
    "— пусто": "— empty",
    "материал": "material",
    "кол-во": "qty",
    "цена/шт": "price/unit",
    "сумма": "total",
    "объём": "volume",
    "не хватает объёма на хабе": "not enough volume at the hub",
    "Уже на складе — остатки": "Already in stock — leftovers",
    "— на складе ничего из нужного не нашлось": "— nothing needed was found in stock",
    "есть/нужно": "have/need",
    "экономия": "savings",
    "где лежит": "location",
    "Лежит не на месте стройки — довезти (доставка учтена во фрахте)":
        "Not at the build site — haul it over (delivery included in freight)",
    "Лежит не на месте стройки — довезти (доставка не посчитана)":
        "Not at the build site — haul it over (delivery not counted)",
    "Эти материалы не покупаем (остатки прошлых строек). Себестоимость в сводке показана полным "
    "замещением — остатки уменьшают только закупку.":
        "We don't buy these materials (leftovers from past builds). The build cost in the summary is at "
        "full replacement value — leftovers only reduce the purchase.",
    "Довоз своих остатков из хабов до {place}: {isk} (входит в статью «Фрахт» контроля стоимости).":
        "Delivery of your leftovers from hubs to {place}: {isk} (included in the “Freight” cost item of "
        "cost control).",
    "⚠ Учтён резерв под другие отчёты": "⚠ Reservations for other reports applied",
    "{warn} — доступный склад уменьшен на их незавершённую часть.":
        "{warn} — available stock is reduced by their unfinished part.",
    "⚠ Склад в этом отчёте не учитывался": "⚠ Stock was not used in this report",
    "{warn} — закупка полная.": "{warn} — full purchase.",
    "Строить самому (чертёж есть)": "Build yourself (blueprint available)",
    "→ {n} шт. ({source})": "→ ×{n} ({source})",
    "свой BPO": "own BPO",
    "своя BPC": "own BPC",
    "своя BPC (⚠ не хватает ранов)": "own BPC (⚠ not enough runs)",
    "ручная цена": "manual price",
    "— нет": "— none",
    "Подготовка / проблемы": "Preparation / issues",
    "Заинвентить: {name} → {n} шт. ({attempts} попыт. Т1-копий{chance}, план округлён вверх){decryptor}":
        "Invent: {name} → ×{n} ({attempts} attempts on T1 copies{chance}, plan rounded up){decryptor}",
    ", шанс {pct}": ", chance {pct}",
    ", декриптор «{name}»": ", decryptor “{name}”",
    ", без декриптора": ", no decryptor",
    " (задано вручную)": " (set manually)",
    "Нет цены: {name} (×{qty}) — задай вручную.": "No price: {name} (×{qty}) — set it manually.",
    "Нет чертежа: {name} — задай стоимость в «Настройки → Стоимость чертежей».":
        "No blueprint: {name} — set its cost in “Settings → Blueprint costs”.",
    "Нет T1-чертежа для инвенты: {name} — нужен, чтобы заинвентить {product}. Купи BPO/BPC или впиши "
    "цену вручную в «Настройки → Стоимость чертежей».":
        "No T1 blueprint for invention: {name} — needed to invent {product}. Buy a BPO/BPC or enter a "
        "price manually in “Settings → Blueprint costs”.",
    "Не хватает копий формулы реакции: {name} — нужно {needed}, есть {owned}. Докупи копии или уменьши "
    "число потоков.":
        "Not enough reaction formula copies: {name} — need {needed}, have {owned}. Buy more copies or "
        "reduce the number of streams.",
    "Не хватает ранов у своей копии чертежа: {name} — нужно {needed} прогонов, на копии осталось "
    "{owned}. Докупи копию/инвентни ещё или запусти партиями.":
        "Not enough runs on your blueprint copy: {name} — need {needed} runs, {owned} left on the copy. "
        "Buy a copy / invent more, or run it in batches.",
    "Инвента {name}: {note}. Проверь цену декриптора или «Настройки → Производство → Инвента».":
        "Invention {name}: {note}. Check the decryptor price or “Settings → Manufacturing → Invention”.",
    "Довезти свой остаток: {name} ×{qty} из «{place}» — доставка из этой системы в фрахте не посчитана.":
        "Haul your leftover: {name} ×{qty} from “{place}” — delivery from this system is not included "
        "in freight.",
    "— ничего, можно строить": "— nothing, ready to build",
    "Входящий фрахт (в себестоимости): {isk}": "Inbound freight (in build cost): {isk}",
    # --- 2 · инструкции по персонажам
    "2 · Инструкции по персонажам": "2 · Instructions per character",
    "{n} шаг(ов)": "{n} step(s)",
    "Нет джобов для распределения.": "No jobs to assign.",
    "Общий прогресс": "Overall progress",
    "{done} / {total} джобов": "{done} / {total} jobs",
    "S-кривая: план vs факт": "S-curve: plan vs actual",
    "План — накопленная доля джобов к их плановому старту (запуску); факт — по отметкам «запущено» "
    "в чек-листе персонажей (совпадает с галочками там и в «Сроки по джобам»).":
        "Plan — cumulative share of jobs by their planned start (launch); actual — by the “launched” "
        "marks in the character checklists (the same checkboxes as there and in “Job timeline”).",
    "— план": "— plan",
    "— факт": "— actual",
    "| сейчас": "| now",
    # --- 3 · контроль сроков и стоимости
    "3 · Контроль сроков и стоимости": "3 · Schedule and cost control",
    "Заполняй факт по мере прохождения циклов — будет видно, где план разошёлся с "
    "реальностью. Всё сохраняется автоматически.":
        "Fill in actuals as the cycles complete to see where the plan and reality diverged. "
        "Everything is saved automatically.",
    "Сроки по джобам": "Job timeline",
    "реакция": "reaction",
    "инвента/копи": "invention/copying",
    "джоб": "job",
    "персонаж": "character",
    "план старт": "plan start",
    "план финиш": "plan finish",
    "план длит.": "plan duration",
    "план ISK (взнос)": "plan ISK (fee)",
    "факт ISK (взнос)": "actual ISK (fee)",
    "готово": "done",
    "заметка": "note",
    "Скопировать название для поиска чертежа в EVE": "Copy the name to search for the blueprint in EVE",
    "рк": "rx",
    "нк": "sci",
    "пр": "mfg",
    "взнос ISK": "fee ISK",
    "Синхронизировано с чек-листом «Инструкции по персонажам»":
        "Synced with the “Instructions per character” checklist",
    "нет джобов": "no jobs",
    "Стоимость — план vs факт (по статьям)": "Cost — plan vs actual (by cost item)",
    "Факт бери из кошелька/журнала за эту стройку: сколько реально потратил на материалы ({jita} / "
    "{cj}), доставку и взносы за джобы. По предметам факт не разносим — материалы общие, а считаем по "
    "статьям.":
        "Take actuals from your wallet/journal for this build: what you actually spent on materials "
        "({jita} / {cj}), delivery and job fees. Actuals aren't split per item — materials are shared, "
        "so we track by cost item.",
    "статья": "cost item",
    "план": "plan",
    "факт ISK": "actual ISK",
    "Δ (факт − план)": "Δ (actual − plan)",
    "ISK факт": "actual ISK",
    " (из таблицы сроков)": " (from the job timeline)",
    "Сумма из колонки «факт ISK» в таблице «Сроки по джобам»":
        "Sum of the “actual ISK” column in the “Job timeline” table",
    "План (ожид. расход): {isk}": "Plan (expected spend): {isk}",
    "Факт итого: {isk}": "Actual total: {isk}",
    # --- JS отчёта (window.__FORGE_I18N__)
    "сохранено ✓": "saved ✓",
    "сохранено локально (сервер недоступен)": "saved locally (server unavailable)",
    "скопировано ✓ ({n})": "copied ✓ ({n})",
    "список пуст": "list is empty",
    "не удалось": "failed",
    # --- страница-список отчётов (reports/index.html)
    "Мои стройки": "My builds",
    "Загрузка…": "Loading…",
    "Пока нет отчётов. Сформируй отчёт из корзины («📋 Сформировать отчёт»).":
        "No reports yet. Create one from the basket (“📋 Generate build report”).",
    "Удалить стройку": "Delete build",
    "создан {created} · ETA {eta} · {done}/{jobs} джобов · {cost}":
        "created {created} · ETA {eta} · {done}/{jobs} jobs · {cost}",
    "Удалить стройку «{title}»? Прогресс и факт-данные удалятся безвозвратно.":
        "Delete build “{title}”? Progress and actuals will be permanently deleted.",
    "Не удалось удалить отчёт.": "Couldn't delete the report.",
    "Не удалось удалить (запущен ли forge web?).": "Couldn't delete (is forge web running?).",
    "Не удалось загрузить список (запущен ли forge web?).": "Couldn't load the list (is forge web running?).",
}
