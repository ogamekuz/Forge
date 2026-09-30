"""Английские переводы: шапка, вкладки, общие виджеты пульта (main_window, widgets, app, donate,
server, state) и общие слова (единицы времени).

Ключ — русская строка ровно как в коде (с подстановками {name}), значение — английская."""

EN: dict[str, str] = {
    # --- единицы времени (fmt_duration и др.)
    "{n}д": "{n}d",
    "{n}ч": "{n}h",
    "{n}м": "{n}m",
    # --- общие виджеты
    "Удалить": "Delete",
    "Отмена": "Cancel",
    "Считаю…": "Calculating…",
    "Убрать": "Remove",
    "— ничего не выбрано": "— nothing selected",
    "Меньше": "Less",
    "Больше": "More",
    "Найти…": "Search…",
    "Название предмета…": "Item name…",
    "покупка": "buy only",
    "Найти EVE-группу (напр. Cruiser, Fuel Block)…": "Find an EVE group (e.g. Cruiser, Fuel Block)…",
    "Найти EVE-категорию (напр. Ship, Module)…": "Find an EVE category (e.g. Ship, Module)…",
    "Найти риг/сервис-модуль (напр. Reactor Efficiency)…":
        "Find a rig/service module (e.g. Reactor Efficiency)…",
    "Найти систему…": "Find a system…",
    # --- вкладки
    "Обзор": "Overview",
    "Что строить": "What to build",
    "Калькулятор": "Calculator",
    "Расписание": "Schedule",
    "Стройки": "Builds",
    "Склад": "Stock",
    "Персонажи": "Characters",
    "Настройки": "Settings",
    # --- шапка, статус
    "Forge 3.0 — индустрия EVE Online": "Forge 3.0 — EVE Online industry",
    "индустрия EVE Online": "EVE Online industry",
    "Поддержать": "Support",
    "Донат автору — ISK персонажу {name} в игре": "Donate to the author — ISK to {name} in game",
    "Синк: проверяю…": "Sync: checking…",
    "Синхронизация данных — вкладка «Обзор»": "Data sync — “Overview” tab",
    "Отчёты: запускаю…": "Reports: starting…",
    "Локальный сервер HTML-отчётов по стройкам (только 127.0.0.1)":
        "Local server for HTML build reports (127.0.0.1 only)",
    "Язык интерфейса и новых HTML-отчётов": "Language of the interface and of new HTML reports",
    "Язык не сохранён в настройках: {error}": "Language not saved to settings: {error}",
    "персонажи": "characters",
    "рынок и индексы": "market & indices",
    "ни разу": "never",
    "только что": "just now",
    "{n} мин назад": "{n} min ago",
    "{n} ч назад": "{n} h ago",
    "{n} дн назад": "{n} d ago",
    "Сервер отчётов не запущен: {error}": "Report server is not running: {error}",
    "ещё стартует": "still starting",
    "Синк: {source}…": "Sync: {source}…",
    "Синк: ошибка": "Sync: error",
    "Данные: {age}": "Data: {age}",
    "Рынок: {market} · персонажи: {chars}\nОбновить — вкладка «Обзор» (или включи автообновление)":
        "Market: {market} · characters: {chars}\nRefresh on the “Overview” tab (or turn on auto-update)",
    "Жду входа EVE SSO в браузере…": "Waiting for EVE SSO login in the browser…",
    "Отчёты: 127.0.0.1:{port}": "Reports: 127.0.0.1:{port}",
    "{config}  ·  отчёты http://127.0.0.1:{port}/reports/": "{config}  ·  reports http://127.0.0.1:{port}/reports/",
    "Отчёты: сервер не запущен": "Reports: server not running",
    "Отчёты: сервер выключен": "Reports: server off",
    # --- сервер отчётов, запуск
    "нет веб-зависимостей ({name}) — pip install -e .[desktop]":
        "web dependencies missing ({name}) — pip install -e .[desktop]",
    "порты 8000–8010 заняты": "ports 8000–8010 are busy",
    "сервер отчётов не создан: {error}": "report server not created: {error}",
    "Пульт не запустился:\n\n{error}\n\nЛог: {log}": "Forge failed to start:\n\n{error}\n\nLog: {log}",
    # --- «Поддержать»
    "Поддержать Forge": "Support Forge",
    "Forge бесплатный и работает только у тебя на компьютере. Если он экономит тебе время и ISK — переведи "
    "автору сколько не жалко. В EVE так и принято: донат — это ISK персонажу, прямо в игре.":
        "Forge is free and runs only on your computer. If it saves you time and ISK, send the author whatever "
        "you like. That's the EVE way: a donation is ISK to a character, right in the game.",
    "Кому": "Recipient",
    "Скопировать имя": "Copy name",
    "Скопировано": "Copied",
    "Как перевести": "How to send",
    "Закрыть": "Close",
    "В игре найди персонажа: поиск или «Люди и места» → «{name}».":
        "In game, find the character: search or “People & Places” → “{name}”.",
    "ПКМ по нему → Give Money (перевести ISK), сумма — сколько не жалко.":
        "Right-click them → Give Money, any amount you like.",
    "В назначении можно написать «{reason}» — так донат не потеряется среди других переводов.":
        "You can put “{reason}” in the reason field so the donation doesn't get lost among other transfers.",
}
