# Changelog

## 3.2.1 — 2026-11-15

- **macOS.** Forge for Macs with Apple Silicon (M1 or newer, macOS 14+): `Forge_3.2.1_macOS.zip`
  with `Forge.app` and its own Python. Data lives in `~/Library/Application Support/Forge`, character
  tokens in the macOS Keychain; *Show* buttons open Finder. The app is built, tested and test-launched
  on a real Mac by GitHub Actions.
- Without the theme's fonts (on a Mac) the interface uses the system font and the system monospace
  font.

- **macOS.** Forge для Маков на Apple Silicon (M1 и новее, macOS 14+): `Forge_3.2.1_macOS.zip` с
  `Forge.app` и своим Python. Данные — в `~/Library/Application Support/Forge`, токены персонажей — в
  связке ключей macOS; кнопки «Показать» открывают Finder. Приложение собирается, проходит тесты и
  пробный запуск на настоящем Маке в GitHub Actions.
- Без шрифтов темы (на Маке) интерфейс берёт системный шрифт и системный моноширинный.

## 3.2.0 — 2026-10-24

- **What to build: pick what you build like in the EVE market.** On the left — the market tree
  with ticks (Ships › Frigates › Standard / Faction / Advanced, Ammunition › Hybrid Charges ›
  Standard / Advanced, Rigs, Reaction Materials…) with *own / total* counts. Unticked sections are not
  suggested in Top, Cheaper to buy or From stock leftovers; ticks are saved at once.
- **Breakdown** of the top by market section (section / subsection / group) and a *Section* column
  in all three tables.
- **Setup video** — the step-by-step guide as a 16-minute video with subtitles (EN/RU), linked from
  README and the setup guide.
- After updating with your own `forge.db`, press *Overview → Download SDE* once — the market tree is
  part of the SDE data.

- **Что строить: выбор, что строишь, как на маркете EVE.** Слева — дерево маркета с галками
  (Ships › Frigates › Standard / Faction / Advanced, Ammunition › Hybrid Charges › Standard /
  Advanced, Rigs, Reaction Materials…) со счётчиками «своих / всего». Снятые разделы не предлагаются
  ни в ТОП, ни в «Дешевле купить», ни в «Из остатков»; галки сохраняются сразу.
- **«Разбивка»** ТОП по разделам маркета (раздел / подраздел / группа) и колонка «Раздел» во всех
  трёх таблицах.
- **Видеоинструкция** — пошаговая настройка в видео на 16 минут с субтитрами (RU/EN), ссылки в
  README и в инструкции по настройке.
- После обновления со своей `forge.db` один раз нажмите «Обзор → Скачать SDE» — дерево маркета
  входит в данные SDE.

## 3.1.0 — 2026-10-12

- **Themes.** Ten interface themes with a live preview (palette button in the header): Forge
  (default), Graphite, Studio, Fjord, Solarized, Amethyst, Ember, Contrast and the light Daylight and
  Sandstone. Applied instantly, stored in `[ui] theme`.
- **From stock leftovers** can ignore blueprint ownership: uncheck *Own blueprints only* and
  everything buildable is scanned; the new *Blueprint* column shows own / invention / manual / none.

- **Темы оформления.** Десять тем с живым предпросмотром (кнопка-палитра в шапке): Forge (по
  умолчанию), Graphite, Studio, Fjord, Solarized, Amethyst, Ember, Contrast и светлые Daylight и
  Sandstone. Применяются сразу, выбор — в `[ui] theme`.
- **«Из остатков склада»** умеет не смотреть на владение чертежами: без галки «Только свои
  чертежи» перебирается всё производимое; новая колонка «Чертёж» — свой / инвента / вручную / нет.

## 3.0.0 — 2026-09-30

First public release. What Forge does and every option are described in [README.md](README.md).

Первый публичный релиз. Возможности и все настройки — в [README.ru.md](README.ru.md).
