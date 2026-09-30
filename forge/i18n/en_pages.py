"""Английские переводы: вкладки «Обзор», «Расписание», «Стройки», «Персонажи» (dashboard, planner, builds, characters).

Ключ — русская строка ровно как в коде (с подстановками {name}), значение — английская."""

EN: dict[str, str] = {
    # ------------------------------------------------------------------ общие для вкладок
    "Персонажи": "Characters",
    "Склад": "Stock",
    "Стройки": "Builds",
    "Расписание": "Schedule",
    "Отмена": "Cancel",
    "Сохранить": "Save",
    "Обновить": "Refresh",
    "Источник": "Source",
    "Себестоимость": "Build cost",
    "{n} поз.": "items: {n}",
    "все": "all",
    "{n} ч назад": "{n} h ago",
    "{n} дн назад": "{n} d ago",
    "персонаж": "character",
    "персонажей": "characters",

    # ------------------------------------------------------------------ «Обзор» (dashboard)
    "Типы (SDE)": "Types (SDE)",
    "Чертежи (SDE)": "Blueprints (SDE)",
    "Рынок (снапшот)": "Market (snapshot)",
    "Cost-индексы": "Cost indices",
    "Чертежи чаров": "Character blueprints",
    "Рынок Jita": "Jita market",
    "История рынка": "Market history",
    "Индексы стоимости": "Cost indices",
    "Рынки-структуры": "Structure markets",
    "Данные": "Data",
    "Персонажи и их роли в стройке": "Characters and their build roles",
    "Что считается складом и где лежит": "What counts as stock and where it is",
    "Свежесть данных — ниже": "Data freshness — see below",
    "Мои стройки (отчёты)": "My builds (reports)",
    "Источники данных": "Data sources",
    "Статус": "Status",
    "готово": "done",
    "идёт…": "running…",
    "ошибка": "error",
    "Когда": "When",
    "Строк": "Rows",
    "Наполнение БД": "Database contents",
    "Таблица": "Table",
    "Синхронизация": "Sync",
    "Сеть — только здесь: ESI (персонажи, рынок структуры, индексы) и Fuzzwork (SDE, снапшот Jita). "
    "Расчёты работают по локальной БД. Синк уважает ESI-кэш — повторный запуск может ничего не докачать.":
        "Network access happens only here: ESI (characters, structure market, indices) and Fuzzwork (SDE, "
        "Jita snapshot). Calculations run on the local database. Sync respects the ESI cache — running it "
        "again may fetch nothing new.",
    "Персонажи + рынки структур": "Characters + structure markets",
    "Чертежи, скиллы, ассеты, джобы, кошельки всех чаров; рынки структур из настроек; имена и системы "
    "структур (для склада)":
        "Blueprints, skills, assets, jobs and wallets of all characters; structure markets from Settings; "
        "structure names and systems (for stock)",
    "Рынок Jita и индексы": "Jita market & indices",
    "Снапшот цен Jita (Fuzzwork), история (раз в сутки), adjusted prices и cost-индексы систем":
        "Jita price snapshot (Fuzzwork), history (once a day), adjusted prices and system cost indices",
    "Скачать SDE": "Download SDE",
    "Дамп SDE с Fuzzwork — сотни МБ, несколько минут": "SDE dump from Fuzzwork — hundreds of MB, several minutes",
    "Добавить персонажа (EVE SSO)": "Add character (EVE SSO)",
    "Запущено: {src}…": "Started: {src}…",
    "Скачать дамп SDE с Fuzzwork? Это сотни МБ и несколько минут.":
        "Download the SDE dump from Fuzzwork? It is hundreds of MB and takes several minutes.",
    "Скачать": "Download",
    "Открываю браузер EVE SSO — войди персонажем и разреши доступ…":
        "Opening EVE SSO in the browser — log in with the character and grant access…",
    "Автообновление": "Auto-update",
    "Обновлять в фоне, пока открыт пульт": "Update in the background while Forge is open",
    "Рынок Jita и индексы — раз в N минут": "Jita market & indices — every N minutes",
    "Персонажи (+ рынки структур) — раз в N минут": "Characters (+ structure markets) — every N minutes",
    "Срабатывает, когда с последнего успешного синка прошло больше интервала. ESI-кэш всё равно уважается.":
        "Runs when more than the interval has passed since the last successful sync. The ESI cache is "
        "still respected.",
    "Сохранено — планировщик подхватит за минуту.": "Saved — the scheduler will pick it up within a minute.",
    "Идёт синк: {src}…": "Syncing: {src}…",
    "Последняя ошибка: {error}": "Last error: {error}",
    "Синк завершён.": "Sync complete.",
    "Жду входа в браузере (EVE SSO)…": "Waiting for login in the browser (EVE SSO)…",
    "Добавлен: {name}. Запусти «Персонажи» — подтянуть его данные.":
        "Added: {name}. Run “Characters” to fetch their data.",
    "Вход не удался: {error}": "Login failed: {error}",
    "Нужно войти заново ({n}): {names}. EVE SSO не принимает их сохранённый вход — нажми «Добавить "
    "персонажа (EVE SSO)» и войди каждым из них, затем «Персонажи + рынки структур».":
        "Login needed again ({n}): {names}. EVE SSO no longer accepts their saved login — click “Add "
        "character (EVE SSO)” and log in with each of them, then run “Characters + structure markets”.",
    "рынок {age}": "market {age}",
    "персонажи {age}": "characters {age}",
    "кошельки {wallet} ISK · идёт джобов: {active}": "wallets {wallet} ISK · jobs running: {active}",
    "{n} позиций · {mode}": "types: {n} · {mode}",
    "выбран вручную": "chosen manually",
    "авто": "auto",
    "{n} в работе": "{n} in progress",
    "всего {total} · средний прогресс {pct}%": "{total} total · average progress {pct}%",
    "всего отчётов: {n}": "total reports: {n}",

    # ------------------------------------------------------------------ «Расписание» (planner)
    "реак": "react",
    "наука": "science",
    "пр": "mfg",
    "чертёж/формула — свои (владелец)": "own blueprint/formula (owner)",
    "владельца": "the owner",
    "нужна передача чертежа от: {who}": "blueprint transfer needed from: {who}",
    "старт": "start",
    "Построить расписание": "Build schedule",
    "Сформировать отчёт по стройке": "Generate build report",
    "учитывать склад": "use stock",
    "резервы строек": "build reservations",
    "Сравнить варианты: срок vs себестоимость": "Compare options: duration vs build cost",
    "Прогнать корзину при нескольких готовых настройках объединения/срока (набор сроков — «Настройки → "
    "Планировщик»)":
        "Run the basket with several preset consolidation/duration settings (the set of durations is in "
        "“Settings → Planner”)",
    "Предметов": "Items",
    "Джобов": "Jobs",
    "Срок": "Duration",
    "Готово к": "Ready by",
    "Передач чертежей": "Blueprint transfers",
    "Джобов, назначенных не владельцу чертежа/формулы — нужна передача. Политика — «Настройки → "
    "Планировщик».":
        "Jobs assigned to someone other than the blueprint/formula owner — a transfer is needed. "
        "Policy: “Settings → Planner”.",
    "Добавь предметы в корзину и нажми «Построить расписание» — общий Gantt по слотам персонажей (роли — "
    "вкладка «Персонажи»; уже запущенные джобы занимают слоты до своего окончания).":
        "Add items to the basket and click “Build schedule” — a combined Gantt across character slots (roles "
        "are on the “Characters” tab; jobs already running occupy slots until they finish).",
    "Раскладываю джобы по слотам…": "Assigning jobs to slots…",
    "Формирую отчёт по стройке…": "Generating build report…",
    "Отчёт сохранён: reports/{file} — открыт в браузере": "Report saved: reports/{file} — opened in the browser",
    "Считаю варианты (каждый — полный расчёт + расписание)…":
        "Calculating options (each one is a full calculation + schedule)…",
    "Сравнение вариантов: срок vs себестоимость": "Option comparison: duration vs build cost",
    "Вариант": "Option",
    "Cyan — самый быстрый срок, зелёным — самая низкая себестоимость (обычно разные строки). Двойной клик — "
    "применить вариант к корзине.":
        "Cyan — the fastest duration, green — the lowest build cost (usually different rows). Double-click "
        "to apply an option to the basket.",
    "Применено к корзине: {label}": "Applied to basket: {label}",
    "{n} джоб(ов)": "{n} job(s)",
    "рамка золотом — реакция": "gold border — reaction",
    "рамка пурпуром — наука (инвента, копии)": "purple border — science (invention, copies)",
    "Нет джобов (всё покупается или на складе).": "No jobs (everything is bought or in stock).",

    # ------------------------------------------------------------------ «Стройки» (builds)
    "Мои стройки": "My builds",
    "Список в браузере": "List in browser",
    "Отчёт формируется из корзины — кнопка «Сформировать отчёт» в Калькуляторе или Расписании. Отметки "
    "«запущено» и факт-стоимость вносятся в самом отчёте и сохраняются здесь, в reports/.":
        "A report is created from the basket — the “Generate build report” button in the Calculator or Schedule. "
        "“Started” marks and actual costs are entered in the report itself and saved here, in reports/.",
    "{n} отч.": "reports: {n}",
    "Пока нет отчётов.": "No reports yet.",
    "Открыть отчёт в браузере": "Open report in browser",
    "Открыть в браузере": "Open in browser",
    "Удалить стройку (отчёт, прогресс и факт-данные)": "Delete build (report, progress and actual data)",
    "создан {created} · ETA {eta} · {done}/{jobs} джобов · {cost} ISK":
        "created {created} · ETA {eta} · {done}/{jobs} jobs · {cost} ISK",
    "Удалить стройку": "Delete build",
    "Удалить стройку «{title}»?\nПрогресс и факт-данные удалятся безвозвратно, резерв склада освободится.":
        "Delete build “{title}”?\nProgress and actual data will be deleted permanently; the stock "
        "reservation will be released.",
    "Не удалось удалить: {error}": "Could not delete: {error}",

    # ------------------------------------------------------------------ «Персонажи» (characters)
    "Произв.": "Mfg.",
    "Реакции": "Reactions",
    "Наука": "Science",
    "EVE SSO не принял сохранённый вход этого персонажа (смена пароля или отзыв доступа приложения на сайте "
    "EVE, перенос персонажа…) — его данные не обновляются. «Обзор → Добавить персонажа (EVE SSO)», войти "
    "им, затем «Персонажи + рынки структур».":
        "EVE SSO rejected this character’s saved login (password change, app access revoked on the EVE "
        "website, character transfer…) — its data is not updating. Use “Overview → Add character (EVE SSO)”, "
        "log in with it, then run “Characters + structure markets”.",
    "Сколько слотов Forge может занять в расписании (пр / рк / нк). Пусто — все по скиллам; 0 — персонаж в "
    "этом пуле не участвует. Уже идущие джобы (любые) занимают слоты по скиллам до окончания: Forge получает "
    "min(лимит, свободно) — напр. свободно 3 из 11 при лимите 5 → 3 сразу и до 5 по мере окончания идущих. "
    "Остальное — под ресёрч и свои джобы.":
        "How many slots Forge may take in the schedule (mfg / react / sci). Empty — all, by skills; 0 — the "
        "character takes no part in this pool. Jobs already running (any) occupy slots by skills until they "
        "finish: Forge gets min(limit, free) — e.g. 3 of 11 free with a limit of 5 → 3 at once and up to 5 "
        "as the running ones finish. The rest is left for research and your own jobs.",
    "Персонаж": "Character",
    "Кошелёк": "Wallet",
    "Слоты пр/рк/нк": "Slots mfg/react/sci",
    "Лимит Forge пр/рк/нк": "Forge limit mfg/react/sci",
    "Чертежи": "Blueprints",
    "Джобы (идут)": "Jobs (running)",
    "Ассеты": "Assets",
    "Обновлён": "Updated",
    "Отметь роли: «Произв.» — производство, «Реакции» — реакции, «Наука» — инвента/копирование (слоты "
    "Laboratory Operation). Можно несколько ролей или ни одной — тогда персонаж в стройке не участвует. "
    "Пустой выбор у всех = участвуют все. «Наука» пустая — наукой занимаются те, кто отмечен на производство. "
    "«Лимит Forge» — сколько слотов расписание может занять (пусто — все; 0 — не участвует в "
    "пуле), чтобы оставить часть под ресёрч и свои джобы.":
        "Tick the roles: “Mfg.” — manufacturing, “Reactions” — reactions, “Science” — invention/copying "
        "(Laboratory Operation slots). A character may have several roles or none — with none, they take no "
        "part in builds. Nothing ticked for anyone = everyone takes part. “Science” empty — science is done "
        "by those ticked for manufacturing. “Forge limit” — how many slots the schedule may take "
        "(empty — all; 0 — not in the pool), to leave some for research and your own jobs.",
    "Сохранить роли": "Save roles",
    "Сбросить изменения": "Discard changes",
    "нужен вход · {age}": "login needed · {age}",
    "Суммарно: {isk} ISK ({short})": "Total: {isk} ISK ({short})",
    "{n} перс.": "characters: {n}",
    "нужен вход: {n}": "login needed: {n}",
    "Сохранено — роли и лимиты слотов учитываются в Расписании и отчётах.":
        "Saved — roles and slot limits are used in the Schedule and reports.",
}
