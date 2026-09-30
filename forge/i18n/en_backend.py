"""Английские переводы: сервис (web/service.py) и ядро/планировщик/синк/ingest/config — сообщения, что доходят до пульта и отчётов.

Ключ — русская строка ровно как в коде (с подстановками {name}), значение — английская."""

EN: dict[str, str] = {
    # --- web/service.py: ошибки запросов
    "Не найден предмет: {product}": "Item not found: {product}",
    "Неизвестный источник: {source}": "Unknown source: {source}",
    "Синк уже идёт — дождись завершения.": "Sync already running — wait for it to finish.",
    "Вход уже выполняется.": "Login already in progress.",
    "Пустая корзина": "Empty basket",
    "Нечего сравнивать (всё в корзине помечено «купить»)":
        "Nothing to compare (everything in the basket is marked “buy”)",
    "Некорректный report_id": "Invalid report_id",
    "Отчёт не найден": "Report not found",
    "Неизвестная настройка: {key}": "Unknown setting: {key}",
    "Настройки не приняты: {e}": "Settings rejected: {e}",
    "Система не известна": "Unknown system",
    # --- web/service.py: «Сравнить варианты»
    "Без консолидации": "No consolidation",
    "Консолидация, 1 поток": "Consolidation, 1 stream",
    "Консолидация, авто-потоки": "Consolidation, auto streams",
    "Макс. {n} {unit}/поток": "Max {n} {unit}/stream",
    "Макс. {n} дня/поток": "Max {n} days/stream",
    "час": "hour",
    "часов": "hours",
    "день": "day",
    "дней": "days",
    # --- web/service.py: ликвидность («Объём/сут»)
    "за {days} календ. дн. до {anchor}, дни без сделок = 0":
        "over {days} calendar days up to {anchor}, days without trades = 0",
    "за {days} календ. дн., дни без сделок = 0": "over {days} calendar days, days without trades = 0",
    "Объём/сут — по записям: среднее по последним 30 записям истории рынка (дни без сделок "
    "не в счёт — неликвид завышен); без истории (для {cj} она не синкается) — "
    "выставленный на продажу объём, а не проданное.":
        "Volume/day — by records: average of the last 30 market history entries (days without trades "
        "are skipped, so illiquid items look better than they are); without history (it isn't synced "
        "for {cj}) — the volume listed for sale, not the volume sold.",
    "Объём/сут — реальный оборот региона {cj} по истории ESI (со сделками в "
    "структурах игроков) {window}.":
        "Volume/day — actual turnover of the {cj} region from ESI history (including trades in "
        "player structures) {window}.",
    "История региона {cj} ещё не скачана — «Обзор → Рынок Jita и индексы» "
    "(качается раз в сутки); до этого объём = 0.":
        "Market history of the {cj} region isn't downloaded yet — “Overview → Jita market & indices” "
        "(downloaded once a day); until then volume = 0.",
    "Объём/сут — оборот {jita} как прокси, по истории ESI {window}.":
        "Volume/day — {jita} turnover as a proxy, from ESI history {window}.",
    "Истории {jita} в БД нет — «Обзор → Рынок Jita и индексы».":
        "No {jita} history in the database — “Overview → Jita market & indices”.",
    "В «Дешевле купить» — тоже оборот {jita}.": "“Cheaper to buy” also uses {jita} turnover.",
    "В «Дешевле купить» рынок — хаб покупки предмета ({jita} или {cj}).":
        "In “Cheaper to buy” the market is the item's purchase hub ({jita} or {cj}).",
    # --- config/config.py
    "Конфиг не найден: {path}. Скопируй forge.example.toml в {path} и заполни.":
        "Config not found: {path}. Copy forge.example.toml to {path} and fill it in.",
    # --- core/blueprint.py: декрипторы
    "тип {type_id}": "type {type_id}",
    "нет цены на рынке": "no market price",
    "это не декриптор инвенты": "not an invention decryptor",
    "декриптор «{target}» задан вручную, но недоступен ({why}) — взят авто-выбор: «{auto}»":
        "decryptor “{target}” is set manually but unavailable ({why}) — auto choice used: “{auto}”",
    "декриптор «{target}» задан вручную, но недоступен ({why}) — взят авто-выбор: без декриптора":
        "decryptor “{target}” is set manually but unavailable ({why}) — auto choice used: no decryptor",
    # --- core/locations.py: подписи локаций
    "стройка": "build",
    "рынок": "market",
    "{name} · рынок (структура)": "{name} · market (structure)",
    "система {id}": "system {id}",
    "{name} (в космосе)": "{name} (in space)",
    "Корабль вне ассетов {id}": "Ship outside assets {id}",
    "Структура {id} (нет доступа)": "Structure {id} (no access)",
    "Структура {id}": "Structure {id}",
    # --- core/stock.py: почему предмет — не склад
    "персонаж не отмечен": "character not selected",
    "исключённая локация": "excluded location",
    "модули в слотах фита": "fitted modules",
    "собранный корабль": "assembled ship",
    "запрет: предмет": "excluded: item",
    "запрет: группа": "excluded: group",
    "флаг {flag}": "flag {flag}",
    # --- ingest/character/sso.py
    "войди этим персонажем заново: «Обзор → Добавить персонажа (EVE SSO)» (или `forge auth add`)":
        "log in with this character again: “Overview → Add character (EVE SSO)” (or `forge auth add`)",
    "EVE SSO не принял сохранённый вход ({detail}) — {hint}": "EVE SSO rejected the saved login ({detail}) — {hint}",
    "EVE SSO не знает приложение ({detail}) — проверь Client ID в «Настройки → Система»":
        "EVE SSO doesn't recognize the application ({detail}) — check the Client ID in “Settings → System”",
    "Некорректный JWT": "Invalid JWT",
    "Неожиданный issuer JWT: {iss}": "Unexpected JWT issuer: {iss}",
    "Неожиданный sub: {sub}": "Unexpected sub: {sub}",
    "Forge: авторизация получена.": "Forge: authorization received.",
    "Можно закрыть это окно и вернуться в терминал.": "You can close this window and return to the terminal.",
    "Forge ожидает ответа EVE SSO…": "Forge is waiting for the EVE SSO response…",
    "Не дождались ответа SSO на callback.": "Timed out waiting for the SSO callback.",
    "State не совпал — возможная CSRF, прерываю.": "State mismatch — possible CSRF, aborting.",
    # --- ingest/character/sync.py, structure_market.py, ingest/esi.py
    "Нет сохранённого входа (character_id={character_id}) — {hint}":
        "No saved login (character_id={character_id}) — {hint}",
    "Нет добавленных персонажей. Запусти `forge auth add`.": "No characters added. Run `forge auth add`.",
    "Не задан sso.client_id в конфиге.": "sso.client_id is not set in the config.",
    "Рынок-структура": "Structure market",
    "Рынок-структура «{name}»": "Structure market “{name}”",
    "нет персонажа со скоупом {scope}": "no character with scope {scope}",
    "сеть: {exc}": "network: {exc}",
    "нет доступа ни у одного персонажа ({codes})": "no character has access ({codes})",
    "ESI rate/error limit ({code}) на {path}": "ESI rate/error limit ({code}) on {path}",
    # --- sync/orchestrator.py, sync/runner.py, storage/sync_state.py: заметки синка (пишутся в БД)
    "кэш ещё свеж": "cache still fresh",
    "история региона сбыта {region}: {rows} строк": "sales region history {region}: {rows} rows",
    "история региона сбыта {region}: ОШИБКА ({error})": "sales region history {region}: ERROR ({error})",
    "нет type_id для history/snapshot (нужна SDE); залиты только adjusted prices":
        "no type_id for history/snapshot (SDE needed); only adjusted prices loaded",
    "прервано": "interrupted",
    "прервано: процесс не завершился": "interrupted: the process did not finish",
    "{who}: ОШИБКА ({error})": "{who}: ERROR ({error})",
    "{who}: ордеров {n}": "{who}: {n} orders",
    "все персонажи не синкнулись": "sync failed for all characters",
    "неизвестный источник: {source}": "unknown source: {source}",
    # --- planner/schedule.py: имена джобов и предупреждения
    "Копия: {name}": "Copy: {name}",
    "Инвента: {name}": "Invention: {name}",
    "Инвента (недостача): {name}": "Invention (shortfall): {name}",
    "Нет персонажей в БД — нечего планировать (forge sync character).":
        "No characters in the database — nothing to plan (forge sync character).",
    "Учтены запущенные джобы ({busy}): их слоты заняты до окончания — "
    "новые джобы встают после них.":
        "Running jobs accounted for ({busy}): their slots stay busy until they finish — "
        "new jobs are queued after them.",
    "Нет назначенных чаров производства со слотами для «{job}»{why}.":
        "No assigned manufacturing characters with slots for “{job}”{why}.",
    "Нет назначенных чаров реакций со слотами для «{job}»{why}.":
        "No assigned reaction characters with slots for “{job}”{why}.",
    "Нет назначенных чаров науки (инвента/копи) со слотами для «{job}»{why}.":
        "No assigned science (invention/copying) characters with slots for “{job}”{why}.",
    " — лимит слотов Forge 0 у: {names} (вкладка «Персонажи»)":
        " — Forge slot limit is 0 for: {names} (Characters tab)",
    "{head} и ещё {n}": "{head} and {n} more",
    "«Только владелец»: владельца чертежа нет среди назначенных в роли — поставлено любому "
    "(нужна передача): {jobs}. Назначь владельца в роль (вкладка «Персонажи»).":
        "“Owner only”: the blueprint owner isn't among the characters assigned to the role — given to "
        "any character (blueprint transfer needed): {jobs}. Assign the owner to the role (Characters tab).",
    "Нужна передача чертежей/формул между чарами: {jobs}.":
        "Blueprint/formula transfer between characters needed: {jobs}.",
    # --- planner/instructions.py: шаги по персонажам
    "Запусти реакцию": "Start reaction",
    "Запусти инвенту": "Start invention",
    "Запусти копирование": "Start copying",
    "Запусти производство": "Start manufacturing",
    "{name} — {copies} коп. × {runs} прог.": "{name} — {copies} × {runs}-run copies",
    "пр": "mfg",
    "рк": "rx",
    "нк": "sci",
    "{verb}: {what} (слот {slot}, TE {te}, {site})": "{verb}: {what} (slot {slot}, TE {te}, {site})",
}
