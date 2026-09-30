"""Оркестрация ingest по источникам.

Разные таймеры/частоты: SDE — вручную/раз в патч; market history — раз в сутки;
snapshot и cost indices — чаще. Перед сетевым синком проверяем ESI-кэш через
``sync_state`` (правило 3: не синкать чаще, чем отдаёт кэш), результат пишем туда же.

Это единственный (вместе с ingest) слой, которому разрешена сеть.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ..config import Config
from ..i18n import tr
from ..ingest import character, industry, market, sde
from ..ingest.esi import EsiClient
from ..storage import sync_state


@dataclass
class SyncResult:
    source: str
    rows: int
    skipped: bool = False
    note: str | None = None


def market_type_ids(conn: sqlite3.Connection) -> list[int]:
    """Все type_id нужные для рыночного синка: продукты чертежей + их входные материалы,
    включая входы ИНВЕНТЫ (датакоры — материалы activity 8) и декрипторы (категория SDE
    «Decryptors»).

    Без материалов raw-ресурсы (Nocxium, Zydrine, Megacyte…) не попадут в снапшот Jita,
    т.к. они не производятся ни одним чертежом, но являются входом многих. Без датакоров и
    декрипторов у них нет цены Jita вообще — инвенту можно «купить» только в C-J6MT,
    а при их отсутствии там инвента-путь проваливается.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT product_type_id AS type_id FROM sde_blueprint_products WHERE activity_id IN (1, 11)
        UNION
        SELECT DISTINCT material_type_id  FROM sde_blueprint_materials WHERE activity_id IN (1, 8, 11)
        UNION
        SELECT t.type_id FROM sde_types t
          JOIN sde_groups g ON g.group_id = t.group_id
          JOIN sde_categories c ON c.category_id = g.category_id
         WHERE c.name = 'Decryptors' AND t.is_published = 1
        """
    ).fetchall()
    return [int(r["type_id"]) for r in rows]


def traded_type_ids(conn: sqlite3.Connection, region_id: int) -> set[int]:
    """type_id со снапшота, у которых есть реальный оборот (ненулевой buy/sell volume).

    ESI history по нетоcommon типам отдаёт 404 и сжигает error-limit; история нужна только
    для того, что реально торгуется. Источник истины — свежий market_snapshot (Fuzzwork).
    """
    rows = conn.execute(
        """
        SELECT type_id FROM market_snapshot
        WHERE region_id = ? AND (COALESCE(sell_volume, 0) > 0 OR COALESCE(buy_volume, 0) > 0)
        """,
        (region_id,),
    ).fetchall()
    return {int(r["type_id"]) for r in rows}


def sell_history_region(cfg: Config, hub_region_id: int) -> int | None:
    """Регион сбыта (C-J6MT), чью ESI-историю надо синкать, — только если ликвидность считается
    по ней (``[recommend] liquidity_source = "sell_region_history"``) и он не совпадает с
    регионом хаба Jita (та история и так качается). Иначе None — лишних запросов к ESI нет.

    В региональную историю ESI попадают и сделки в структурах игроков: проверено вручную
    (2026-09-29) — в Insmother нет ни одной NPC-станции, а ``/markets/10000009/history/`` отдаёт
    ежедневные объёмы (Tritanium — 1–4 млрд шт./сут). Это оборот ВСЕГО региона (все его рынки-
    структуры), не только Keepstar 1st Taj Mahgoon — для Insmother он и есть основной рынок."""
    if cfg.recommend.liquidity_source != "sell_region_history":
        return None
    cj = cfg.locations.get("c_j6mt")
    region = cj.region_id if cj else 0
    return region if region and region != hub_region_id else None


def has_history(conn: sqlite3.Connection, region_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM market_history WHERE region_id = ? LIMIT 1", (region_id,)
    ).fetchone() is not None


def sync_industry(
    conn: sqlite3.Connection, esi: EsiClient | None = None, force: bool = False
) -> SyncResult:
    source = "industry"
    if not force and sync_state.is_cache_fresh(conn, source):
        return SyncResult(source, 0, skipped=True, note=tr("кэш ещё свеж"))
    own = esi is None
    esi = esi or EsiClient()
    sync_state.mark_running(conn, source)
    try:
        rows, expires = industry.sync(conn, esi)
        sync_state.mark_success(conn, source, rows, expires)
        return SyncResult(source, rows)
    except Exception as exc:
        sync_state.mark_error(conn, source, str(exc))
        raise
    finally:
        if own:
            esi.close()


def sync_market(
    conn: sqlite3.Connection,
    cfg: Config,
    esi: EsiClient | None = None,
    type_ids: Iterable[int] | None = None,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> SyncResult:
    source = "market"
    # NB: market намеренно НЕ гейтим суточным кэшем целиком — снапшот цен дешёвый (~3с).
    # Тяжёлая ESI-история (суточные свечи, ~12 мин на ~5к типов) гейтится отдельным
    # 'market_history' и не перекачивается на каждый синк — иначе обновление цен «висит» 12 мин.
    jita = cfg.locations.get("jita")
    region_id = jita.region_id if jita else 10000002  # The Forge
    ids = list(type_ids) if type_ids is not None else market_type_ids(conn)

    own = esi is None
    esi = esi or EsiClient()
    sync_state.mark_running(conn, source)
    try:
        total = 0
        notes: list[str] = []
        if ids:
            # Снапшот цен (Fuzzwork, один батч) — быстро, каждый раз.
            s_rows = market.sync_snapshot(conn, region_id, ids)
            total += s_rows
            # История ESI — суточная и медленная: качаем только если её кэш истёк (или force).
            history_due = force or not sync_state.is_cache_fresh(conn, "market_history")
            if history_due:
                traded = traded_type_ids(conn, region_id)
                hist_ids = [t for t in ids if t in traded]
                h_rows, h_expires = market.sync_history(conn, esi, region_id, hist_ids, progress=progress)
                sync_state.mark_success(conn, "market_history", h_rows, h_expires)
                total += h_rows
            sell_region = sell_history_region(cfg, region_id)
            # История региона сбыта — тот же суточный гейт 'market_history'; плюс сразу, если её
            # ещё нет совсем (только что включили источник — кэшировать нечего, ждать сутки незачем).
            if sell_region and (history_due or not has_history(conn, sell_region)):
                try:
                    r_rows, _ = market.sync_history(
                        conn, esi, sell_region, sorted(traded_type_ids(conn, sell_region)), progress=progress)
                    total += r_rows
                    notes.append(tr("история региона сбыта {region}: {rows} строк", region=sell_region,
                                    rows=r_rows))
                except Exception as exc:  # опция не роняет синк цен Jita — ошибка видна в «Обзоре»
                    notes.append(tr("история региона сбыта {region}: ОШИБКА ({error})", region=sell_region,
                                    error=exc))
        a_rows, _ = market.sync_adjusted_prices(conn, esi)
        total += a_rows
        if not ids:
            notes.append(tr("нет type_id для history/snapshot (нужна SDE); залиты только adjusted prices"))
        note = "; ".join(notes) or None
        # expires=None: цены (снапшот) можно обновлять когда угодно, не ждём суток.
        sync_state.mark_success(conn, source, total, note=note)
        return SyncResult(source, total, note=note)
    except Exception as exc:
        sync_state.mark_error(conn, source, str(exc))
        raise
    except BaseException:  # Ctrl-C / SystemExit — не оставляем зависший 'running'
        sync_state.mark_error(conn, source, tr("прервано"))
        raise
    finally:
        if own:
            esi.close()


def sync_sde(
    conn: sqlite3.Connection, workdir: str = ".", force: bool = False
) -> SyncResult:
    source = "sde"
    sync_state.mark_running(conn, source)
    try:
        counts = sde.sync(conn, workdir=workdir)
        total = sum(counts.values())
        note = ", ".join(f"{k}={v}" for k, v in counts.items()) or None
        sync_state.mark_success(conn, source, total, note=note)
        return SyncResult(source, total, note=note)
    except Exception as exc:
        sync_state.mark_error(conn, source, str(exc))
        raise


def character_note(results: list) -> str | None:
    """Итог синка персонажей одной строкой. Одинаковые ошибки — одной записью со списком имён
    и первыми: 7 персонажей, чей вход EVE отозвала, — «A, B, …: ОШИБКА (… войди заново …)»,
    а не семь одинаковых простыней вперемешку с успешными."""
    groups: dict[str, list[str]] = {}
    for r in results:
        if r.error:
            groups.setdefault(r.error, []).append(r.name)
    parts = [tr("{who}: ОШИБКА ({error})", who=", ".join(names), error=err) for err, names in groups.items()]
    parts += [f"{r.name}: " + ", ".join(f"{k}={v}" for k, v in r.counts.items())
              for r in results if not r.error]
    return "; ".join(parts) or None


def sync_character(conn: sqlite3.Connection, cfg: Config) -> SyncResult:
    source = "character"
    sync_state.mark_running(conn, source)
    try:
        results = character.sync_all(conn, cfg)
        total = sum(sum(r.counts.values()) for r in results)
        note = character_note(results)
        failed = [r for r in results if r.error]
        _mark_structure_markets(conn, results)
        # Один упавший персонаж (напр. устойчивый 500 у ESI на его /blueprints/) не должен
        # красить ВЕСЬ синк как "error" — остальные реально засинкались, данные свежие. Только
        # если упали АБСОЛЮТНО все — это уже настоящий сбой синка, а не проблема одного чара.
        if results and len(failed) == len(results):
            sync_state.mark_error(conn, source, note or tr("все персонажи не синкнулись"))
            return SyncResult(source, total, note=note)
        sync_state.mark_success(conn, source, total, note=note)
        return SyncResult(source, total, note=note)
    except (LookupError, ValueError) as exc:
        # Нет добавленных чаров / не задан client_id — это настроечная проблема, не сбой.
        sync_state.mark_error(conn, source, str(exc))
        return SyncResult(source, 0, skipped=True, note=str(exc))
    except Exception as exc:
        sync_state.mark_error(conn, source, str(exc))
        raise


def _mark_structure_markets(conn: sqlite3.Connection, results: list) -> None:
    """Итоги рынков-структур (строки ``structure_market.RESULT_PREFIX`` синка персонажей) —
    отдельной строкой «structure_market» журнала синков: на вкладке «Обзор» видно, какая
    структура сколько ордеров дала и какая нет (ошибка одной не красит ВЕСЬ синк персонажей)."""
    sm = [r for r in results if character.structure_market.is_result(r.name)]
    if not sm:
        return
    note = "; ".join(tr("{who}: ОШИБКА ({error})", who=r.name, error=r.error) if r.error
                     else tr("{who}: ордеров {n}", who=r.name, n=r.counts.get("orders", 0)) for r in sm)
    if all(r.error for r in sm):
        sync_state.mark_error(conn, "structure_market", note)
    else:
        sync_state.mark_success(conn, "structure_market", sum(r.counts.get("orders", 0) for r in sm),
                                note=note)


def sync_all(
    conn: sqlite3.Connection,
    cfg: Config,
    workdir: str = ".",
    force: bool = False,
    include_sde: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> list[SyncResult]:
    """Полный синк public-источников. SDE опционально (тяжёлый дамп)."""
    results: list[SyncResult] = []
    if include_sde:
        results.append(sync_sde(conn, workdir=workdir, force=force))
    results.append(sync_industry(conn, force=force))
    results.append(sync_market(conn, cfg, force=force, progress=progress))
    return results
