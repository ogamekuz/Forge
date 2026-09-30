"""Сборка и рендер отчёта по корзине (слой interface/web).

Объединяет себестоимость (core) и расписание (planner) в один payload и рендерит
**самодостаточный** HTML для ежедневного использования во время стройки:
1) список закупок (Jita / C-J6MT / остатки на GPLB-C / строить самому / проблемы),
2) инструкции по персонажам с чек-боксами и S-кривой план/факт,
3) контроль сроков и стоимости (план vs факт),
4) красивый тёмный вид в стиле дашборда.

Сеть не трогает — читает БД только через переданный ``conn`` (правило 1 docs/ARCHITECTURE.md). Иконки
EVE подгружает браузер с Image Server CCP, как и остальной UI. Остатки на складе GPLB-C
вычитаются ТОЛЬКО из списка закупок; себестоимость остаётся полным замещением (ядро не меняем).
"""

from __future__ import annotations

import html as html_mod
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta

from ..config import Config
from ..core import cost, prices, sourcing
from ..core.locations import LocationResolver
from ..core.stock import (
    HUB_BUILD,
    HUB_JITA,
    HUB_MARKET,
    HUB_OTHER,
    StockView,
    stock_view,
)
from ..core.stock import gplb_on_hand as gplb_on_hand  # для сверки и тестов
from ..i18n import N_, group, lang, short_dt, tr
from ..planner import deadline_substreams, format_duration, plan_basket, schedule_basket
from ..planner.instructions import build_character_instructions
from ..planner.schedule import job_resource_name
from . import stock_net

ICON = "https://images.evetech.net/types/{tid}/icon?size=32"


# --------------------------------------------------------------------------- payload


def build_report_payload(
    conn: sqlite3.Connection,
    cfg: Config,
    products: list[tuple[int, int, int]],
    *,
    default_me: int = 0,
    default_te: int = 0,
    allow_build: bool = True,
    max_stream_days: float | None = None,
    consolidate: bool = True,
    auto_streams: bool = False,
    now: datetime | None = None,
    force_buy_ids: set[int] | None = None,
    force_buy_extra: frozenset[int] = frozenset(),
    external_reserved: dict[int, int] | None = None,
    me_overrides: dict[int, int] | None = None,
    te_overrides: dict[int, int] | None = None,
    use_stock: bool = True,
) -> dict:
    """Собрать JSON-payload отчёта по корзине ``products`` = [(type_id, runs, streams), …].

    ``use_stock`` — вычитать ли склад ([stock] конфига) из закупок вообще (False — отчёт «как
    будто склада нет», напр. чтобы прикинуть полную закупку под новую точку).

    ``force_buy_ids`` — type_id, которые игрок решил КУПИТЬ, а не строить: они НЕ идут в
    план/расписание, а попадают в список закупок как готовый предмет (landed до GPLB-C).
    ``auto_streams`` — см. ``sourcing.consolidate_shared_components``/``planner.plan_basket``:
    вернуть общей постройке параллелизм, потерянный при объединении общих компонентов.
    ``default_te`` — TE для срока постройки чертежей не во владении и не добываемых инвентой
    (аналог ``default_me`` для материалов, см. ``sourcing.NodeResult.resulting_te``).
    """
    now = now or datetime.now()
    force_buy = {int(t) for t in (force_buy_ids or set())}
    build_products = [(t, r, s) for (t, r, s) in products if t not in force_buy]
    buy_items = [(t, r) for (t, r, s) in products if t in force_buy]
    bp = plan_basket(
        conn, cfg, build_products, default_me=default_me, default_te=default_te, allow_build=allow_build,
        max_stream_days=max_stream_days, consolidate=consolidate, auto_streams=auto_streams,
        force_buy_extra=force_buy_extra, me_overrides=me_overrides, te_overrides=te_overrides,
    )
    estimates, schedule = bp.estimates, bp.schedule
    # ВАЖНО: те же (батчевые) params, что plan_basket уже использовал для расчёта деревьев —
    # НЕ пересобирать через build_params_from_config(cfg) заново: там фрахт по fixed_jump-
    # плечам ещё не пересчитан батчем на весь заказ (см. core.estimate_basket), и «Фрахт»/
    # _shrink_node ниже разъехались бы с тем, что реально зашито в material_cost деревьев.
    params = bp.params
    # Та же разбивка под-компонентов, что использовал plan_basket выше — нужна ниже, чтобы
    # ЧЕСТНО пересчитать под-дерево при частичном покрытии складом (не рассинхронизировать
    # потоки/ME-округление с остальным деревом).
    substream_fn = deadline_substreams(conn, cfg, max_stream_days)

    # Материалы, которые Проход 1 купил на рынке (и НИГДЕ в корзине не строил) — передаются
    # ниже как доп. force_buy_extra в любую пересборку узла (шринк верхнего продукта, LLC-
    # неттинг склада). Без этого пересборка на чуть другом количестве вольна заново решать
    # buy/build с нуля — для ПОЧТИ той же величины спроса (489 прогонов против 488) выбор
    # может молча перевернуться с «купить Unrefined Crystallite Alloy»
    # на «построить» — рождая ветку, которой в дереве Прохода 1 не было вообще: её собственный
    # склад GPLB-C потом никогда не проверяется (LLC-неттинг видит только ключи из графа
    # Прохода 1), а стоимость раздувается на сотни миллионов ISK. Материал, который где-либо
    # в той же корзине строился, сюда не попадает — не хотим силой перекрывать легитимное
    # решение «строить» в другой ветке.
    pass1_bought: set[int] = set()
    pass1_built: set[int] = set()
    _seen_pass1: set[int] = set()

    def _collect_pass1_sourcing(node: sourcing.NodeResult) -> None:
        if id(node) in _seen_pass1:
            return
        _seen_pass1.add(id(node))
        for ln in node.lines:
            if ln.child is not None:
                pass1_built.add(ln.type_id)
                _collect_pass1_sourcing(ln.child)
            elif ln.source == "buy":
                pass1_bought.add(ln.type_id)

    for est in estimates:
        if not sourcing.is_unbuildable_leaf(est.node):
            _collect_pass1_sourcing(est.node)
    pass1_market_only = frozenset(pass1_bought - pass1_built)

    # --- итоги по корзине (как /api/cost-basket) + строки по предметам -------------
    totals = {"material_cost": 0.0, "job_cost": 0.0, "blueprint_cost": 0.0,
              "total_cost": 0.0, "revenue": 0.0, "profit": 0.0, "freight_inbound": 0.0}
    items: list[dict] = []

    def _buy_total(tid: int, name: str, runs: int) -> None:
        """Покупаемый предмет (мета/дроп или по решению игрока): landed × кол-во → в себестоимость."""
        qty = max(1, runs)
        choice = cost.choose_hub(conn, tid, params, qty)
        unit = (choice.landed_gplb if choice else 0.0) or 0.0
        bt = unit * qty
        totals["material_cost"] += bt
        totals["total_cost"] += bt
        sell = prices.sell_min(conn, tid, params.cj6mt_region_id) or prices.sell_min(conn, tid, params.jita_region_id)
        items.append({"type_id": tid, "name": name, "runs": runs, "streams": 1, "produced": qty,
                      "total_cost": bt, "unit_cost": unit, "sell_unit_price": sell,
                      "revenue": None, "profit": None, "roi": None, "buy_only": True})

    for est in estimates:
        node, prof = est.node, est.profit
        if sourcing.is_unbuildable_leaf(node):
            _buy_total(node.product_type_id, node.name, node.runs)  # непостроиваемое (мета/дроп)
            continue
        m, j, b = sourcing.aggregate_costs(node)
        totals["material_cost"] += m
        totals["job_cost"] += j
        totals["blueprint_cost"] += b
        totals["total_cost"] += node.total_cost
        totals["revenue"] += prof.revenue or 0.0
        totals["profit"] += prof.profit or 0.0
        totals["freight_inbound"] += sourcing.inbound_freight(conn, node, params)
        items.append({
            "type_id": node.product_type_id, "name": node.name,
            "runs": node.runs, "streams": node.streams, "produced": node.produced,
            "total_cost": node.total_cost, "unit_cost": node.unit_cost,
            "sell_unit_price": prof.sell_unit_price,
            "revenue": prof.revenue, "profit": prof.profit, "roi": prof.roi,
        })
    for tid, runs in buy_items:
        _buy_total(tid, prices.type_name(conn, tid), runs)  # по решению игрока — купить
    # totals is float-valued except this one optional field:
    totals["roi"] = totals["profit"] / totals["total_cost"] if totals["total_cost"] else None  # type: ignore[assignment]

    # --- обход деревьев сверху вниз: вычет остатков GPLB-C, затем строить/покупать ----
    # Остатки нетим на КАЖДОМ потреблении (и для покупки, и для постройки): то, что уже
    # лежит на складе, не строим и не покупаем заново — и в под-дерево такого компонента
    # не спускаемся. Себестоимость (core) при этом не меняем — это отдельный «вид закупок».
    resolver = LocationResolver(conn, cfg)
    stock: StockView = stock_view(conn, cfg, resolver) if use_stock else StockView()
    ledger = dict(stock.totals)
    # Вычитаем со склада то, что зарезервировано другими отчётами (с учётом их прогресса).
    if external_reserved:
        for tid, q in external_reserved.items():
            if tid in ledger:
                ledger[tid] = max(0, ledger[tid] - q)
    reserved_external = bool(external_reserved)
    agg_buy: dict[int, dict] = {}
    own_build: dict[int, dict] = {}
    invention: dict[int, dict] = {}
    on_hand_used: dict[int, dict] = {}
    issues: list[dict] = []
    seen_issue: set[tuple] = set()
    # Плановая стоимость УСТАНОВКИ джоба на 1 run по каждому продукту — для таблицы «Сроки
    # по джобам» (там джобы разбиты на потоки, а node.job_cost — сумма за ВЕСЬ узел). EIV
    # линеен по runs (без округления на джоб, в отличие от материалов) — поэтому per-run
    # константа для продукта и её можно просто умножить на runs конкретного джоба в расписании.
    job_cost_per_run: dict[int, float] = {}
    # Стоимость 1 попытки инвенты (T1-копия + джоб-взнос — ровно то, что node.blueprint_cost
    # берёт на попытку, см. sourcing.build_node_cost) по продукту — для той же таблицы, но у
    # джоба инвенты (в расписании) нет "node" на попытку, только на весь узел, и раскладка
    # отличается от runs/streams обычного джоба (см. schedule.extract_jobs) — поэтому отдельный
    # лукап, НЕ переиспользующий job_cost_per_run (там — EIV-стоимость постройки продукта, а не
    # инвенты; смешивать нельзя, разные джобы с разным activity_id). Сумма plan_cost по ВСЕМ
    # джобам инвенты одного продукта (attempts_needed попыток суммарно, разбитых на потоки)
    # обязана дать РОВНО node.blueprint_cost — это то, что авто-суммирует статья «Чертежи/
    # инвента» в контроле стоимости (см. cost_control ниже и recompute() в JS).
    invention_fee_per_attempt: dict[int, float] = {}
    # Из неё — стоимость T1-копии на попытку (копи-джоб со своего BPO): если копи-джобы стоят в
    # расписании ([planner] schedule_copy_jobs), их строки несут t1_copy × копий, а строки инвенты —
    # только взнос за джоб инвенты: сумма по строкам та же, статья «Чертежи/инвента» не задваивается.
    t1_copy_per_attempt: dict[int, float] = {}
    # Реальные (после вычета склада) джобы/чертежи — для «Контроль стоимости», в отличие от
    # totals["job_cost"]/["blueprint_cost"] (те посчитаны ДО вычета склада, на полном дереве —
    # стабильная база для план/факт в totals/items). classify_built вызывается РОВНО один раз на
    # каждый реально строящийся (уже уменьшенный складом, если частично покрыт) узел — без
    # долевого масштабирования aggregate_costs (то предполагает, что ln.quantity — это доля
    # РЕБЁНКА, а после пересборки ln.quantity уже не совпадает с уменьшенным ln.child.produced).
    real_costs = {"job": 0.0, "blueprint": 0.0}

    def classify_built(node: sourcing.NodeResult) -> None:
        """Узел, который реально строим → группа «своё» / «инвента» / «нет чертежа»."""
        if node.runs:
            job_cost_per_run[node.product_type_id] = node.job_cost / node.runs
        real_costs["job"] += node.job_cost
        real_costs["blueprint"] += node.blueprint_cost
        if node.decryptor_fallback:
            # Заданный вручную декриптор ([invention]) недоступен — взят авто-выбор (см.
            # blueprint.choose_decryptor): игрок должен это увидеть до закупки декрипторов.
            key = ("decryptor_fallback", node.product_type_id)
            if key not in seen_issue:
                seen_issue.add(key)
                issues.append({"type_id": node.product_type_id, "name": node.name,
                               "kind": "decryptor_fallback", "note": node.decryptor_fallback})
        src = node.blueprint_source
        if src in ("owned_bpo", "owned_bpc", "owned_bpc_insufficient", "manual"):
            rec = own_build.setdefault(node.product_type_id,
                                       {"type_id": node.product_type_id, "name": node.name,
                                        "produced": 0, "source": src})
            rec["produced"] += node.produced
            if src == "owned_bpc_insufficient":
                key = ("bpc_runs", node.product_type_id)
                if key not in seen_issue:
                    seen_issue.add(key)
                    issues.append({"type_id": node.product_type_id, "name": node.name,
                                   "kind": "bpc_runs_insufficient",
                                   "owned": node.bpc_runs_owned, "needed": node.bpc_runs_needed})
                if node.bpc_shortfall_invention_attempts is not None:
                    # Недостачу ранов своей копии можно закрыть инвентой — конкретное число
                    # попыток показываем в том же списке «Заинвентить», а не только
                    # общую фразу «докупи/заинвентни ещё» в bpc_runs_insufficient выше.
                    shortfall = max(0, (node.bpc_runs_needed or 0) - (node.bpc_runs_owned or 0))
                    inv_rec = invention.setdefault(
                        node.product_type_id,
                        {"type_id": node.product_type_id, "name": node.name,
                         "produced": 0, "decryptor": node.bpc_shortfall_decryptor,
                         "source_name": node.invention_source_name,
                         "source_owned": node.invention_source_owned, "attempts": 0,
                         # итоговый шанс (со скиллами инвентора и декриптором) — по нему
                         # посчитаны попытки; показывается в «Подготовка / проблемы»
                         "probability": node.bpc_shortfall_probability,
                         "decryptor_manual": node.decryptor_manual},
                    )
                    inv_rec["produced"] += shortfall
                    inv_rec["attempts"] += node.bpc_shortfall_invention_attempts
                    if node.bpc_shortfall_invention_fee_per_attempt is not None:
                        # Джоб инвенты на недостачу (см. planner.schedule.extract_jobs_many)
                        # ищет план-стоимость по ЭТОМУ ЖЕ ключу — иначе строка в «Сроки по
                        # джобам» показала бы 0 вместо реальной цены попытки.
                        invention_fee_per_attempt[node.product_type_id] = (
                            node.bpc_shortfall_invention_fee_per_attempt
                        )
                        t1_copy_per_attempt[node.product_type_id] = node.invention_t1_copy
                    if not node.invention_source_owned:
                        src_key = ("invention_source", node.product_type_id)
                        if src_key not in seen_issue:
                            seen_issue.add(src_key)
                            issues.append({"type_id": node.invention_source_id or 0,
                                           "name": node.invention_source_name or "?",
                                           "kind": "bp_missing_invention_source",
                                           "for_product": node.name})
        elif src == "invention":
            rec = invention.setdefault(node.product_type_id,
                                       {"type_id": node.product_type_id, "name": node.name,
                                        "produced": 0, "decryptor": node.decryptor,
                                        "source_name": node.invention_source_name,
                                        "source_owned": node.invention_source_owned,
                                        "attempts": 0,
                                        "probability": (node.invention_breakdown or {}).get("probability"),
                                        "decryptor_manual": node.decryptor_manual})
            rec["produced"] += node.produced
            if node.invention_breakdown:
                invention_fee_per_attempt[node.product_type_id] = (
                    node.invention_breakdown["t1_copy"] + node.invention_breakdown["job_fee"]
                )
                t1_copy_per_attempt[node.product_type_id] = node.invention_breakdown["t1_copy"]
                # Попыток инвенты (= Т1-копий), план округлён вверх — для «Подготовка/проблемы»
                # (это число видно в самом отчёте, не только в Калькуляторе).
                rec["attempts"] += node.invention_breakdown["attempts"]
            if not node.invention_source_owned:
                key = ("invention_source", node.product_type_id)
                if key not in seen_issue:
                    seen_issue.add(key)
                    issues.append({"type_id": node.invention_source_id or 0,
                                   "name": node.invention_source_name or "?",
                                   "kind": "bp_missing_invention_source",
                                   "for_product": node.name})
        elif src == "missing":
            key = ("missing", node.product_type_id)
            if key not in seen_issue:
                seen_issue.add(key)
                issues.append({"type_id": node.product_type_id, "name": node.name,
                               "kind": "bp_missing"})
        elif src == "missing_reaction":
            key = ("missing_reaction", node.product_type_id)
            if key not in seen_issue:
                seen_issue.add(key)
                issues.append({"type_id": node.product_type_id, "name": node.name,
                               "kind": "bp_missing_reaction",
                               "owned": node.reaction_bp_owned, "needed": node.reaction_bp_needed})

    def take_from_stock(type_id: int, name: str, quantity: int, unit_cost) -> int:
        """Списать покрытие материала со склада GPLB-C; вернуть остаток к постройке/покупке.

        Материал может встречаться НЕСКОЛЬКИМИ независимыми строками в дереве (напр. Photonic
        Metamaterials нужен и Photon Microprocessor, и Oscillator Capacitor Unit — каждый со
        своим потреблением его же Thulium Hafnite) — склад делится МЕЖДУ ними по порядку обхода
        (кто раньше, тот и берёт). Материал трекается целиком, если он ХОТЬ КОГДА-ТО был на
        складе (``type_id in ledger``) — остаток («сколько взяли») копится только пока есть
        склад, а «нужно» — по КАЖДОЙ строке, даже если к моменту очередной строки остаток УЖЕ
        обнулился (``covered == 0``): иначе «нужно» показывало бы только сумму строк, которым
        ХОТЬ ЧТО-ТО досталось от склада, и «Уже на складе» занижало бы реальный спрос.
        """
        have = ledger.get(type_id, 0)
        covered = min(have, quantity) if have > 0 else 0
        if type_id in ledger:
            rec = on_hand_used.setdefault(type_id,
                                          {"type_id": type_id, "name": name, "covered": 0,
                                           "required": 0, "unit_cost": unit_cost, "subtotal": 0.0})
            rec["required"] += quantity
            if covered > 0:
                rec["covered"] += covered
                if unit_cost is not None:
                    rec["subtotal"] += unit_cost * covered
        if covered > 0:
            ledger[type_id] = have - covered
        return quantity - covered

    def add_buy(type_id: int, name: str, quantity: int, unit, buy_hub, shortage: bool) -> None:
        rec = agg_buy.setdefault(type_id,
                                 {"type_id": type_id, "name": name, "qty": 0, "subtotal": 0.0,
                                  "hub": {"jita": 0, "cj": 0}, "shortage": False})
        rec["qty"] += quantity
        rec["subtotal"] += (unit or 0.0) * quantity
        rec["hub"][buy_hub or "cj"] += quantity
        rec["shortage"] = rec["shortage"] or bool(shortage)

    def note_no_price(type_id: int, name: str, quantity: int) -> None:
        key = ("noprice", type_id)
        if key not in seen_issue:
            seen_issue.add(key)
            issues.append({"type_id": type_id, "name": name, "kind": "no_price", "quantity": quantity})

    def _shrink_node(node: sourcing.NodeResult, remaining: int) -> sourcing.NodeResult:
        """Частичное покрытие складом (0 < remaining < node.produced): пересчитать (под-)дерево
        на РЕАЛЬНО нужное количество — иначе расписание/инструкции/«Своя постройка» планируют
        строить и то, что уже частично лежит на GPLB-C (пример: склад 61 шт., needed 350 → без
        пересчёта планировался бы джоб на все 350, а не на 289)."""
        qty_per_run = node.produced / node.runs if node.runs else 0
        if qty_per_run <= 0:
            return node
        new_runs = max(1, math.ceil(remaining / qty_per_run))
        return sourcing.build_node_cost(
            conn, node.product_type_id, new_runs, node.streams, params,
            default_me=default_me, default_te=default_te, allow_build=allow_build,
            substream_fn=substream_fn, force_buy_extra=force_buy_extra | pass1_market_only,
            me_overrides=me_overrides, te_overrides=te_overrides,
        )

    def _shop_buy(tid: int, name: str, runs: int) -> None:
        """Покупка готового предмета в список закупок (с вычетом остатков GPLB-C)."""
        qty = max(1, runs)
        choice = cost.choose_hub(conn, tid, params, qty)
        unit = choice.landed_gplb if choice else None
        remaining = take_from_stock(tid, name, qty, unit)
        if remaining > 0:
            if choice is not None:
                add_buy(tid, name, remaining, unit, choice.hub, not choice.enough)
            else:
                note_no_price(tid, name, remaining)

    # Остаток склада ПО САМОМУ верхнему продукту (не только по его материалам) — СНАЧАЛА, ДО
    # stock_net.net_and_finalize: если часть запрошенных 10 шт. уже лежит на GPLB-C готовыми,
    # строить нужно меньше — верхний узел ПЕРЕСОБИРАЕТСЯ ЗАНОВО (_shrink_node → свежий
    # build_node_cost, без всякого знания о консолидации, которую уже сделал plan_basket выше).
    # Если делать это ПОСЛЕ consolidate/net_and_finalize, пересобранное под-дерево содержит
    # СВЕЖИЕ, ещё не объединённые объекты — общий с соседним товаром корзины под-компонент
    # (напр. Hexite, общий у Sylramic Fibers и Ferrogel) перестаёт быть общим ТОЛЬКО для
    # пересобранной ветки, и строится ВТОРОЙ раз впустую (пример: Sylramic Fibers
    # частично на складе → её Hexite задвоился бы с тем, что уже верно объединил plan_basket
    # для Ferrogel).
    skip_est: set[int] = set()
    for est in estimates:
        node = est.node
        if sourcing.is_unbuildable_leaf(node):
            continue  # непостроиваемое — свой путь (_shop_buy) ниже, склад не общий с деревом
        remaining = take_from_stock(node.product_type_id, node.name, node.produced, node.unit_cost)
        if remaining <= 0:
            skip_est.add(id(est))
            continue  # весь верхний продукт уже на складе — строить не нужно вообще
        if remaining < node.produced:
            est.node = _shrink_node(node, remaining)

    # Пере-консолидировать ПОСЛЕ пересборки верхних продуктов (см. комментарий выше) — иначе
    # свежепересобранная ветка не участвует в объединении с остальными товарами корзины.
    # Для уже-не-пересобранных деревьев это идемпотентно (consolidate_shared_components сам
    # пропускает уже объединённые пары — задваивания на них не будет).
    if consolidate:
        buildable = [est.node for est in estimates
                     if not sourcing.is_unbuildable_leaf(est.node) and id(est) not in skip_est]
        if buildable:
            sourcing.consolidate_shared_components(
                conn, buildable, params,
                default_me=default_me, default_te=default_te, allow_build=allow_build,
                substream_fn=substream_fn, force_buy_extra=force_buy_extra,
                auto_streams=auto_streams, me_overrides=me_overrides, te_overrides=te_overrides,
            )

    # Списание склада + пересборка под-компонентов на любой глубине — единым LLC-упорядоченным
    # проходом (forge/web/stock_net.py, см. докстринг там), а НЕ рекурсивным walk с точечным
    # обнаружением общности: там узел, который сам частично на складе, при шринке пересобирается
    # с нуля и «забывает» про уже найденное слияние общего под-компонента глубже (пример: 5×
    # Ishtar — Vanadium Hafnite округлялся бы отдельно в двух ветках вместо одного раза на
    # объединённый спрос).
    roots = [est.node for est in estimates
             if not sourcing.is_unbuildable_leaf(est.node) and id(est) not in skip_est]
    stock_net.net_and_finalize(
        conn, roots, params, consolidate=consolidate,
        take_from_stock=take_from_stock, classify_built=classify_built,
        add_buy=add_buy, note_no_price=note_no_price,
        build_kwargs={
            "default_me": default_me, "default_te": default_te, "allow_build": allow_build,
            "substream_fn": substream_fn, "force_buy_extra": force_buy_extra | pass1_market_only,
            "me_overrides": me_overrides, "te_overrides": te_overrides,
        },
    )

    for est in estimates:
        node = est.node
        if sourcing.is_unbuildable_leaf(node):
            _shop_buy(node.product_type_id, node.name, node.runs)  # непостроиваемое (мета/дроп)
    for tid, runs in buy_items:
        _shop_buy(tid, prices.type_name(conn, tid), runs)  # по решению игрока — купить

    # Расписание пересчитываем по деревьям, из которых вычтены остатки склада (net_and_finalize
    # выше обрезал полностью покрытые под-деревья) — чтобы «Контроль сроков»/инструкции/S-кривая
    # не планировали строить то, что уже лежит на GPLB-C. Себестоимость (totals/items) при этом
    # не меняется.
    schedule = schedule_basket(conn, cfg, [est.node for est in estimates], consolidate=consolidate)

    # Реальная стоимость доставки ТОЙ партии, что действительно покупаем (после вычета
    # склада) — НЕ переиспользуем разбавленную ставку из batched_freight_params (та посчитана
    # на объём ВСЕГО дерева, до вычета склада — нужна core для стабильной «полной» себестоимости
    # в план/факт калибровке). Если после склада объём меньше, но всё равно укладывается в
    # 1 рейс — рейс всё равно стоит fixed_cost целиком, а не долю от него: route_freight_cost
    # честно считает по РЕАЛЬНОМУ объёму (params.jump_routes не тронут батчингом, только
    # params.freight — поэтому здесь получаем настоящий, не разбавленный, результат).
    vol_by_hub = {"jita": 0.0, "cj": 0.0}
    for rec in agg_buy.values():
        hub = "jita" if rec["hub"]["jita"] > rec["hub"]["cj"] else "cj"
        vol_by_hub[hub] += rec["qty"] * prices.volume(conn, rec["type_id"])

    # Откуда берём свои остатки ([stock] может включать не только место стройки): раскладываем
    # списанное по партиям склада (сначала местные, см. StockView.allocate) — для колонки «где
    # лежит» и для доставки: остатки в C-J6MT/Jita едут до стройки тем же фрахтом, что и
    # покупки (их объём добавляется в плечи), остатки в прочих системах — доставка не известна.
    place_names: dict[int, str] = {}
    stock_vol = {HUB_JITA: 0.0, HUB_MARKET: 0.0, HUB_OTHER: 0.0}
    stock_other: list[dict] = []
    for rec in on_hand_used.values():
        if rec["covered"] <= 0:
            rec["sources"] = []
            continue
        skip = (external_reserved or {}).get(rec["type_id"], 0)
        alloc = stock.allocate(rec["type_id"], skip + rec["covered"])
        sources: list[dict] = []
        for lot, qty in alloc:
            use = qty
            if skip > 0:  # чужой резерв занимает первые (самые удобные) партии
                cut = min(skip, use)
                skip -= cut
                use -= cut
            if use <= 0:
                continue
            if lot.root_id not in place_names:
                place_names[lot.root_id] = resolver.describe(lot.root_id).name
            sources.append({"root_id": lot.root_id, "label": place_names[lot.root_id],
                            "hub": lot.hub, "quantity": use})
            if lot.hub != HUB_BUILD:
                v = use * prices.volume(conn, rec["type_id"])
                stock_vol[lot.hub] = stock_vol.get(lot.hub, 0.0) + v
                if lot.hub == HUB_OTHER:
                    stock_other.append({"type_id": rec["type_id"], "name": rec["name"],
                                        "quantity": use, "label": place_names[lot.root_id]})
        rec["sources"] = sources

    total_vol_jc = vol_by_hub["jita"] + stock_vol[HUB_JITA]
    total_vol_cg = vol_by_hub["jita"] + vol_by_hub["cj"] + stock_vol[HUB_JITA] + stock_vol[HUB_MARKET]
    total_leg_jc = cost.route_freight_cost(params, "jita", "c_j6mt", total_vol_jc)
    total_leg_cg = cost.route_freight_cost(params, "c_j6mt", "gplb_c", total_vol_cg)
    # Доля доставки своих остатков из хабов в реальной стоимости плечей (по объёму).
    stock_haul = 0.0
    if total_vol_cg > 0:
        stock_haul += (stock_vol[HUB_JITA] + stock_vol[HUB_MARKET]) / total_vol_cg * total_leg_cg
    if total_vol_jc > 0:
        stock_haul += stock_vol[HUB_JITA] / total_vol_jc * total_leg_jc

    jita: list[dict] = []
    cj: list[dict] = []
    jita_raw = cj_raw = freight_total = 0.0   # для контроля стоимости: сырьё по хабам + фрахт (нетто)
    for rec in agg_buy.values():
        unit = rec["subtotal"] / rec["qty"] if rec["qty"] else 0.0
        hub = "jita" if rec["hub"]["jita"] > rec["hub"]["cj"] else "cj"
        vol = prices.volume(conn, rec["type_id"])
        rec_vol = rec["qty"] * vol
        # Доля этого материала в реальном (не разбавленном) фрахте партии — пропорционально
        # его вкладу в объём на каждом плече; сумма долей по всем материалам = total_leg_*.
        share_cg = (rec_vol / total_vol_cg * total_leg_cg) if total_vol_cg > 0 else 0.0
        share_jc = (rec_vol / total_vol_jc * total_leg_jc) if hub == "jita" and total_vol_jc > 0 else 0.0
        line_freight = share_cg + share_jc
        raw = rec["subtotal"] - line_freight     # рыночная цена без доставки
        line = {"type_id": rec["type_id"], "name": rec["name"], "quantity": rec["qty"],
                "unit_cost": unit, "subtotal": rec["subtotal"],
                "volume": vol, "shortage": rec["shortage"]}
        if hub == "jita":
            jita.append(line); jita_raw += raw
        else:
            cj.append(line); cj_raw += raw
        freight_total += line_freight
    freight_total += stock_haul

    on_hand_group = list(on_hand_used.values())
    by_sub = lambda x: -(x["subtotal"] or 0)
    jita.sort(key=by_sub)
    cj.sort(key=by_sub)
    on_hand_group.sort(key=by_sub)
    own = sorted(own_build.values(), key=lambda x: x["name"])
    inv = sorted(invention.values(), key=lambda x: x["name"])

    shopping = {
        "jita": jita, "cj": cj, "on_hand": on_hand_group,
        "own_build": own, "invention": inv, "issues": issues,
        "reserved_external": reserved_external,
        "stock_used": use_stock,
        "stock_other": stock_other,   # свои остатки вне хабов — доставку считай сам
        "totals": {
            "jita_isk": sum(l["subtotal"] for l in jita),
            "cj_isk": sum(l["subtotal"] for l in cj),
            "on_hand_isk": sum(l["subtotal"] for l in on_hand_group),
            "freight_inbound": totals["freight_inbound"],
            "stock_haul_isk": stock_haul,
        },
    }

    # --- контроль стоимости: план по статьям затрат (для ввода факта и калибровки) ----
    # Материалы по хабам — рыночная цена без доставки (нетто, с вычетом склада); фрахт отдельно
    # (включая довоз своих остатков из хабов); джобы/чертежи — из агрегатов. Так факт-расход
    # (из кошелька) сравнивается со статьями плана.
    places = place_labels(cfg)
    cost_buckets = [
        {"key": "mat_jita", "label": tr("Материалы — {place}", place=places["jita"]), "plan": jita_raw},
        {"key": "mat_cj", "label": tr("Материалы — {place}", place=places["cj"]), "plan": cj_raw},
        {"key": "freight", "label": tr("Фрахт (доставка до {place})", place=places["build"]),
         "plan": freight_total},
        {"key": "jobs", "label": tr("Взносы за джобы"), "plan": real_costs["job"]},
        {"key": "blueprints", "label": tr("Чертежи / инвента (датакоры, копии)"),
         "plan": real_costs["blueprint"]},
    ]
    plan_total = jita_raw + cj_raw + freight_total + real_costs["job"] + real_costs["blueprint"]
    cost_control = {"buckets": cost_buckets, "plan_total": plan_total}

    # --- инструкции по персонажам ---------------------------------------------------
    char_plans = build_character_instructions(schedule, now, site=places["build"])
    characters = [
        {"character_id": cp.character_id, "name": cp.name, "color": cp.color,
         "steps": [{"step_id": s.step_id, "order": s.order, "pool": s.pool,
                    "slot_label": s.slot_label, "text": s.text, "item_name": s.item_name,
                    "runs": s.runs, "te": s.te, "activity_id": s.activity_id,
                    "planned_start": s.planned_start, "planned_end": s.planned_end,
                    "duration_s": s.duration_s} for s in cp.steps]}
        for cp in char_plans
    ]

    # --- джобы для таблицы контроля сроков ------------------------------------------
    # Продукты, у которых копи-джобы T1 стоят в расписании: цена копии — в их строках, в строках
    # инвенты — только взнос за джоб инвенты (без задвоения, см. t1_copy_per_attempt).
    copied = {it.product_type_id for it in schedule.items if it.activity_id == cost.COPYING}

    def plan_cost(it) -> float:
        pid = it.product_type_id
        if it.activity_id == cost.INVENTION:
            fee = invention_fee_per_attempt.get(pid, 0.0)
            if pid in copied:
                fee -= t1_copy_per_attempt.get(pid, 0.0)
            return fee * it.runs
        if it.activity_id == cost.COPYING:  # копий × прогонов на копию = попыток инвенты
            return t1_copy_per_attempt.get(pid, 0.0) * it.runs * it.copy_runs
        return job_cost_per_run.get(pid, 0.0) * it.runs

    jobs = []
    for it in sorted(schedule.items, key=lambda x: (x.start, x.job_id)):
        start = now + timedelta(seconds=it.start)
        end = now + timedelta(seconds=it.end)
        jobs.append({
            "job_id": it.job_id, "name": it.name,
            "resource_name": job_resource_name(it.name),  # без «Инвента: »/«Копия: » (любой язык)
            "product_type_id": it.product_type_id, "runs": it.runs, "copy_runs": it.copy_runs,
            "character_name": it.character_name, "pool": it.pool, "slot": it.slot,
            "activity_id": it.activity_id, "start_s": it.start, "end_s": it.end,
            "duration_s": it.end - it.start, "duration_human": format_duration(it.end - it.start),
            "planned_start": start.isoformat(timespec="minutes"),
            "planned_end": end.isoformat(timespec="minutes"),
            "plan_cost": plan_cost(it),
        })

    makespan = schedule.makespan
    title = _build_title(items)
    report_id = f"build-{now:%Y%m%d-%H%M%S}-{_slug(items)}"
    return {
        "report_id": report_id,
        "title": title,
        "places": places,
        "generated_at": now.isoformat(timespec="minutes"),
        "generated_at_ms": int(now.timestamp() * 1000),
        "options": {"me": default_me, "te": default_te, "build": allow_build,
                    "max_stream_days": max_stream_days, "consolidate": consolidate},
        "summary": {
            "items": len(items), "jobs": len(schedule.items),
            "characters": len({c["character_id"] for c in characters}),
            "makespan_s": makespan, "makespan_human": format_duration(makespan),
            "eta": (now + timedelta(seconds=makespan)).isoformat(timespec="minutes"),
            "eta_ms": int((now + timedelta(seconds=makespan)).timestamp() * 1000),
        },
        "totals": totals,
        "items": items,
        "shopping": shopping,
        "cost_control": cost_control,
        "characters": characters,
        "prep": {"transfers": list(dict.fromkeys(schedule.warnings)), "invention": inv},
        "jobs": jobs,
    }


# --------------------------------------------------------------------------- helpers

def place_labels(cfg: Config) -> dict[str, str]:
    """Имена мест из конфига ([locations]): хаб Jita, рынок C-J6MT, место стройки GPLB-C —
    чтобы отчёт не был прибит к конкретным названиям систем."""
    def name(key: str, default: str) -> str:
        loc = cfg.locations.get(key)
        return (loc.name if loc and loc.name else default).strip() or default
    return {"jita": name("jita", "Jita"), "cj": name("c_j6mt", "C-J6MT"),
            "build": name("gplb_c", "GPLB-C")}


def _places(p: dict) -> dict[str, str]:
    return p.get("places") or {"jita": "Jita", "cj": "C-J6MT", "build": "GPLB-C"}


def _esc(s) -> str:
    return html_mod.escape(str(s))


def _int(n) -> str:
    return group(f"{round(n):,}")


def _isk(v) -> str:
    if v is None:
        return "—"
    return _int(v) + " ISK"


def _isk_short(v) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return tr("{v} млрд", v=f"{v / 1e9:.2f}")
    if a >= 1e6:
        return tr("{v} млн", v=f"{v / 1e6:.1f}")
    if a >= 1e3:
        return tr("{v} тыс", v=f"{v / 1e3:.0f}")
    return _int(v)


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _short_dt(iso: str) -> str:
    try:
        return short_dt(datetime.fromisoformat(iso))
    except (ValueError, TypeError):
        return iso or "—"


def _icon(tid: int) -> str:
    return ICON.format(tid=tid)


def _build_title(items: list[dict]) -> str:
    """Человекочитаемый заголовок стройки по составу корзины: «30× Ishtar (+2 поз.)»."""
    if not items:
        return tr("Пустая корзина")
    first = items[0]
    head = f"{_int(first['produced'])}× {first['name']}"
    return head if len(items) == 1 else tr("{head} (+{n} поз.)", head=head, n=len(items) - 1)


def _slug(items: list[dict]) -> str:
    """Короткий безопасный для имени файла/URL слаг по составу корзины."""
    if not items:
        return "build"
    base = f"{items[0]['name']}-x{int(items[0]['produced'])}"
    if len(items) > 1:
        base += f"-plus{len(items) - 1}"
    s = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-")
    return s[:48] or "build"


# --------------------------------------------------------------------------- sections

def _stat(label: str, value: str, accent: str = "") -> str:
    cls = {"good": "v-good", "bad": "v-bad", "sky": "v-sky"}.get(accent, "")
    return (f'<div class="stat"><div class="stat-l">{_esc(label)}</div>'
            f'<div class="stat-v {cls}">{_esc(value)}</div></div>')


def _summary_html(p: dict) -> str:
    s, t = p["summary"], p["totals"]
    cards = "".join([
        _stat(tr("Себестоимость"), _isk_short(t["total_cost"])),
        _stat(tr("Выручка"), _isk_short(t["revenue"])),
        _stat(tr("Прибыль"), _isk_short(t["profit"]), "good" if t["profit"] >= 0 else "bad"),
        _stat("ROI", _pct(t["roi"]), "good" if (t["roi"] or 0) >= 0 else "bad"),
        _stat(tr("Срок (ETA)"), s["makespan_human"], "sky"),
        _stat(tr("Предметов"), str(s["items"])),
        _stat(tr("Джобов"), str(s["jobs"])),
        _stat(tr("Персонажей"), str(s["characters"])),
    ])
    eta = _short_dt(s["eta"])
    return (f'<section class="card"><h2>{_esc(tr("Сводка по стройке"))}</h2>'
            f'<div class="grid">{cards}</div>'
            '<div class="muted sm" style="margin-top:10px">'
            + tr("Готовность ориентировочно к {eta} при старте всех джобов сейчас.",
                 eta=f"<b>{_esc(eta)}</b>")
            + '</div></section>')


def _buy_table(rows: list[dict], with_check: bool) -> str:
    if not rows:
        return f'<div class="muted sm">{_esc(tr("— пусто"))}</div>'
    out = ['<table class="t"><thead><tr>',
           '<th></th>' if with_check else '', f'<th></th><th>{_esc(tr("материал"))}</th>',
           f'<th class="r">{_esc(tr("кол-во"))}</th><th class="r">{_esc(tr("цена/шт"))}</th>'
           f'<th class="r">{_esc(tr("сумма"))}</th>',
           f'<th class="r">{_esc(tr("объём"))}</th></tr></thead><tbody>']
    for r in rows:
        chk = (f'<td><input type="checkbox" data-shop="buy-{r["type_id"]}"></td>'
               if with_check else "")
        warn = (f' <span class="warn" title="{_esc(tr("не хватает объёма на хабе"))}">⚠</span>'
                if r.get("shortage") else "")
        vol = (tr("{v} м³", v=group(f'{r["volume"] * r["quantity"]:,.0f}'))
               if r.get("volume") else "—")
        out.append(
            f'<tr>{chk}<td><img class="ic" loading="lazy" src="{_icon(r["type_id"])}"></td>'
            f'<td>{_esc(r["name"])}{warn}</td>'
            f'<td class="r num">{_int(r["quantity"])}</td>'
            f'<td class="r num">{_isk_short(r["unit_cost"])}</td>'
            f'<td class="r num">{_isk(r["subtotal"])}</td>'
            f'<td class="r num muted">{vol}</td></tr>')
    out.append("</tbody></table>")
    return "".join(out)


def _sources_html(r: dict, build: str) -> str:
    """«GPLB-C ×120 · 1st Taj Mahgoon ×40 🚚» — откуда брать остаток (🚚 — надо довезти)."""
    parts = []
    for s in r.get("sources") or []:
        if s.get("hub") == "gplb_c":
            haul = ""
        else:
            hint = (tr("Лежит не на месте стройки — довезти (доставка учтена во фрахте)")
                    if s.get("hub") in ("c_j6mt", "jita")
                    else tr("Лежит не на месте стройки — довезти (доставка не посчитана)"))
            haul = f' <span class="warn" title="{_esc(hint)}">🚚</span>'
        parts.append(f'{_esc(s["label"])} ×{_int(s["quantity"])}{haul}')
    return " · ".join(parts) or f'<span class="dim">{_esc(build)}</span>'


def _onhand_table(rows: list[dict], build: str = "GPLB-C") -> str:
    if not rows:
        return f'<div class="muted sm">{_esc(tr("— на складе ничего из нужного не нашлось"))}</div>'
    out = [f'<table class="t"><thead><tr><th></th><th>{_esc(tr("материал"))}</th>'
           f'<th class="r">{_esc(tr("есть/нужно"))}</th><th class="r">{_esc(tr("экономия"))}</th>'
           f'<th>{_esc(tr("где лежит"))}</th></tr></thead><tbody>']
    for r in rows:
        out.append(
            f'<tr><td><img class="ic" loading="lazy" src="{_icon(r["type_id"])}"></td>'
            f'<td>{_esc(r["name"])}</td>'
            f'<td class="r num">{_int(r["covered"])} / {_int(r["required"])}</td>'
            f'<td class="r num good">−{_isk_short(r["subtotal"])}</td>'
            f'<td class="sm muted">{_sources_html(r, build)}</td></tr>')
    out.append("</tbody></table>")
    return "".join(out)


# Откуда чертёж у позиции «Строить самому» (source узла → подпись; прочее — ручная цена).
_OWN_LABELS = {
    "owned_bpo": N_("свой BPO"), "owned_bpc": N_("своя BPC"),
    "owned_bpc_insufficient": N_("своя BPC (⚠ не хватает ранов)"),
}


def _prep_issue(r: dict) -> str:
    """Строка «Подготовка / проблемы» по проблеме ``shopping.issues`` (пусто — неизвестный вид)."""
    kind, name = r["kind"], _esc(r["name"])
    if kind == "no_price":
        text = tr("Нет цены: {name} (×{qty}) — задай вручную.", name=name, qty=_int(r.get("quantity", 0)))
    elif kind == "bp_missing":
        text = tr("Нет чертежа: {name} — задай стоимость в «Настройки → Стоимость чертежей».", name=name)
    elif kind == "bp_missing_invention_source":
        text = tr("Нет T1-чертежа для инвенты: {name} — нужен, чтобы заинвентить {product}. Купи BPO/BPC "
                  "или впиши цену вручную в «Настройки → Стоимость чертежей».",
                  name=name, product=_esc(r.get("for_product", "?")))
    elif kind == "bp_missing_reaction":
        text = tr("Не хватает копий формулы реакции: {name} — нужно {needed}, есть {owned}. Докупи копии "
                  "или уменьши число потоков.",
                  name=name, needed=_int(r.get("needed", 0)), owned=_int(r.get("owned", 0)))
    elif kind == "bpc_runs_insufficient":
        text = tr("Не хватает ранов у своей копии чертежа: {name} — нужно {needed} прогонов, на копии "
                  "осталось {owned}. Докупи копию/инвентни ещё или запусти партиями.",
                  name=name, needed=_int(r.get("needed", 0)), owned=_int(r.get("owned", 0)))
    elif kind == "decryptor_fallback":
        text = tr("Инвента {name}: {note}. Проверь цену декриптора или «Настройки → Производство → Инвента».",
                  name=name, note=_esc(r.get("note", "")))
        return f'<li class="warn">{text}</li>'
    else:
        return ""
    return f'<li class="bad">{text}</li>'


def _shopping_html(p: dict) -> str:
    sh = p["shopping"]
    st = sh["totals"]
    own = "".join(
        f'<li><img class="ic" loading="lazy" src="{_icon(r["type_id"])}"> {_esc(r["name"])} '
        '<span class="muted">'
        + _esc(tr("→ {n} шт. ({source})", n=_int(r["produced"]),
                  source=tr(_OWN_LABELS.get(r["source"], N_("ручная цена")))))
        + '</span></li>'
        for r in sh["own_build"]) or f'<li class="muted sm">{_esc(tr("— нет"))}</li>'

    prep = []
    for r in sh["invention"]:
        dec = tr(", декриптор «{name}»", name=_esc(r["decryptor"])) if r.get("decryptor") else ""
        if r.get("decryptor_manual"):
            dec = (dec or tr(", без декриптора")) + tr(" (задано вручную)")
        chance = tr(", шанс {pct}", pct=_pct(r["probability"])) if r.get("probability") else ""
        prep.append("<li>" + tr("Заинвентить: {name} → {n} шт. ({attempts} попыт. Т1-копий{chance}, "
                                "план округлён вверх){decryptor}",
                                name=f"<b>{_esc(r['name'])}</b>", n=_int(r["produced"]),
                                attempts=_int(r.get("attempts", 0)), chance=chance, decryptor=dec)
                    + "</li>")
    for w in p["prep"]["transfers"]:
        prep.append(f'<li>{_esc(w)}</li>')
    for r in sh["issues"]:
        prep.append(_prep_issue(r))
    for r in sh.get("stock_other") or []:
        prep.append('<li class="warn">'
                    + tr("Довезти свой остаток: {name} ×{qty} из «{place}» — доставка из этой системы "
                         "в фрахте не посчитана.",
                         name=_esc(r["name"]), qty=_int(r["quantity"]), place=_esc(r["label"]))
                    + '</li>')
    prep_html = "".join(prep) or f'<li class="muted sm">{_esc(tr("— ничего, можно строить"))}</li>'
    pl = _places(p)
    haul = st.get("stock_haul_isk") or 0.0
    stock_note = ('' if sh.get("stock_used", True) else
                  '<br>' + tr("{warn} — закупка полная.",
                              warn=f'<b class="warn">{_esc(tr("⚠ Склад в этом отчёте не учитывался"))}</b>'))
    copy_btn = ('<button class="copybtn" data-copy="{hub}" title="'
                + _esc(tr("Скопировать список для мультипокупки EVE")) + '">'
                + _esc(tr("📋 копировать")) + '</button>')

    return (
        f'<section class="card"><h2>{_esc(tr("1 · Список закупок"))}</h2>'
        '<div class="two">'
        f'<div><h3>{_esc(tr("Купить в {place}", place=pl["jita"]))} '
        f'<span class="badge">{_isk_short(st["jita_isk"])}</span>'
        f'{copy_btn.replace("{hub}", "jita")}</h3>'
        f'{_buy_table(sh["jita"], True)}</div>'
        f'<div><h3>{_esc(tr("Купить в {place}", place=pl["cj"]))} '
        f'<span class="badge">{_isk_short(st["cj_isk"])}</span>'
        f'{copy_btn.replace("{hub}", "cj")}</h3>'
        f'{_buy_table(sh["cj"], True)}</div>'
        '</div>'
        '<div class="two" style="margin-top:14px">'
        f'<div><h3>{_esc(tr("Уже на складе — остатки"))} '
        f'<span class="badge good">−{_isk_short(st["on_hand_isk"])}</span></h3>'
        f'{_onhand_table(sh["on_hand"], pl["build"])}'
        '<div class="muted sm" style="margin-top:6px">'
        + _esc(tr("Эти материалы не покупаем (остатки прошлых строек). Себестоимость в сводке показана "
                  "полным замещением — остатки уменьшают только закупку."))
        + ('<br>' + tr("Довоз своих остатков из хабов до {place}: {isk} (входит в статью «Фрахт» "
                       "контроля стоимости).", place=_esc(pl["build"]), isk=f"<b>{_isk(haul)}</b>")
           if haul > 0 else '')
        + ('<br>' + tr("{warn} — доступный склад уменьшен на их незавершённую часть.",
                       warn=f'<b class="warn">{_esc(tr("⚠ Учтён резерв под другие отчёты"))}</b>')
           if sh.get("reserved_external") else '')
        + stock_note
        + '</div></div>'
        f'<div><h3>{_esc(tr("Строить самому (чертёж есть)"))}</h3><ul class="list">{own}</ul></div>'
        '</div>'
        f'<div style="margin-top:14px"><h3>{_esc(tr("Подготовка / проблемы"))}</h3>'
        f'<ul class="list">{prep_html}</ul></div>'
        '<div class="muted sm" style="margin-top:8px">'
        + tr("Входящий фрахт (в себестоимости): {isk}", isk=f'<b>{_isk(st["freight_inbound"])}</b>')
        + '</div></section>')


def _characters_html(p: dict) -> str:
    blocks = []
    for c in p["characters"]:
        steps = "".join(
            f'<li class="step"><label><input type="checkbox" data-step="{s["step_id"]}">'
            f'<span class="ord">{s["order"]}</span>'
            f'<span class="stext">{_esc(s["text"])}</span></label>'
            f'<span class="when">{_short_dt(s["planned_start"])} → {_short_dt(s["planned_end"])}</span></li>'
            for s in c["steps"])
        blocks.append(
            f'<div class="charcard" style="--c:{c["color"]}">'
            f'<div class="charhead"><span class="dot"></span><b>{_esc(c["name"])}</b>'
            f'<span class="muted sm">{_esc(tr("{n} шаг(ов)", n=len(c["steps"])))}</span>'
            f'<span class="cprog" data-cprog="{c["character_id"]}">0%</span></div>'
            f'<div class="bar"><i data-cbar="{c["character_id"]}"></i></div>'
            f'<ul class="steps">{steps}</ul></div>')
    grid = "".join(blocks) or f'<div class="muted">{_esc(tr("Нет джобов для распределения."))}</div>'
    return (
        f'<section class="card"><h2>{_esc(tr("2 · Инструкции по персонажам"))}</h2>'
        '<div class="overall"><div class="ring"><svg viewBox="0 0 120 120"><circle class="ring-bg" cx="60" cy="60" r="52"></circle>'
        '<circle id="ring-fg" cx="60" cy="60" r="52" transform="rotate(-90 60 60)"></circle>'
        '<text id="ring-t" x="60" y="68">0%</text></svg></div>'
        f'<div class="overall-meta"><div class="muted sm">{_esc(tr("Общий прогресс"))}</div>'
        f'<div id="prog-line" class="big">{_esc(tr(_JS_REPORT_T["jobs_progress"], done=0, total=0))}</div>'
        '</div></div>'
        f'<div class="chars">{grid}</div>'
        f'<h3 style="margin-top:16px">{_esc(tr("S-кривая: план vs факт"))}</h3>'
        '<div class="muted sm">'
        + _esc(tr("План — накопленная доля джобов к их плановому старту (запуску); факт — по отметкам "
                  "«запущено» в чек-листе персонажей (совпадает с галочками там и в «Сроки по джобам»)."))
        + '</div>'
        '<svg id="scurve" viewBox="0 0 720 260" preserveAspectRatio="none"></svg>'
        f'<div class="legend"><span class="lg lg-plan">{_esc(tr("— план"))}</span> '
        f'<span class="lg lg-fact">{_esc(tr("— факт"))}</span> '
        f'<span class="lg lg-now">{_esc(tr("| сейчас"))}</span></div>'
        '</section>')


def _job_label(j: dict) -> str:
    """«Имя ×прогонов»; копи-джоб — «Копия: T1 — N коп. × M прог.»."""
    if j.get("activity_id") == cost.COPYING:
        return tr("{name} — {copies} коп. × {runs} прог.", name=_esc(j["name"]),
                  copies=_int(j["runs"]), runs=_int(j.get("copy_runs") or 1))
    return f'{_esc(j["name"])} ×{_int(j["runs"])}'


# Пул джоба в «Сроки по джобам» (персонаж · пул#слот): реакции / наука / производство.
_POOL_SHORT = {"reaction": N_("рк"), "science": N_("нк")}
_POOL_SHORT_DEFAULT = N_("пр")


def _tracking_html(p: dict) -> str:
    t_copy_name = _esc(tr("Скопировать название для поиска чертежа в EVE"))
    t_fee = _esc(tr("взнос ISK"))
    t_synced = _esc(tr("Синхронизировано с чек-листом «Инструкции по персонажам»"))
    t_note = _esc(tr("заметка"))
    jrows = "".join(
        f'<tr class="pool-{_esc(j["pool"])}"><td class="num muted">{j["job_id"]}</td>'
        f'<td>{_job_label(j)} '
        f'<button class="copybtn copybtn-sm" data-copy-name="{_esc(j["resource_name"])}" '
        f'title="{t_copy_name}">📋</button></td>'
        f'<td>{_esc(j["character_name"])} · '
        f'{_esc(tr(_POOL_SHORT.get(j["pool"], _POOL_SHORT_DEFAULT)))}#{j["slot"]+1}</td>'
        f'<td class="num">{_short_dt(j["planned_start"])}</td>'
        f'<td class="num">{_short_dt(j["planned_end"])}</td>'
        f'<td class="num muted">{_esc(j["duration_human"])}</td>'
        f'<td class="num muted">{_isk(j["plan_cost"])}</td>'
        f'<td><input type="number" class="spent" data-job="{j["job_id"]}" data-f="cost" placeholder="{t_fee}"></td>'
        f'<td class="ctr"><input type="checkbox" class="done-cb" data-step="{j["job_id"]}" title="{t_synced}"></td>'
        f'<td><input class="note" data-job="{j["job_id"]}" data-f="note" placeholder="{t_note}"></td></tr>'
        for j in p["jobs"]) or f'<tr><td colspan="10" class="muted">{_esc(tr("нет джобов"))}</td></tr>'

    # «Взносы за джобы» и «Чертежи/инвента» считаются автоматически из колонки факт-стоимости
    # в таблице сроков — первая суммирует ВСЕ джобы, КРОМЕ инвенты и копирования T1 (activity_id
    # 8 и 5), вторая — ТОЛЬКО их (см. recompute() в JS, split по job.activity_id): копии для
    # инвенты — часть статьи «Чертежи/инвента», как и в плане (node.blueprint_cost).
    _autocost_keys = {"jobs", "blueprints"}

    def _fact_cell(b: dict) -> str:
        if b["key"] in _autocost_keys:
            return (f'<td class="r num" data-autocost="{b["key"]}" '
                    f'title="{_esc(tr("Сумма из колонки «факт ISK» в таблице «Сроки по джобам»"))}">—</td>')
        return (f'<td><input type="number" class="spent" data-cost="{b["key"]}" '
                f'placeholder="{_esc(tr("ISK факт"))}"></td>')

    from_timeline = _esc(tr(" (из таблицы сроков)"))
    brows = "".join(
        f'<tr><td>{_esc(b["label"])}{from_timeline if b["key"] in _autocost_keys else ""}</td>'
        f'<td class="r num muted">{_isk(b["plan"])}</td>'
        f'{_fact_cell(b)}'
        f'<td class="r num" data-delta="{b["key"]}">—</td></tr>'
        for b in p["cost_control"]["buckets"])
    th = "".join(f'<th>{_esc(tr(h))}</th>' for h in (
        N_("джоб"), N_("персонаж"), N_("план старт"), N_("план финиш"), N_("план длит."),
        N_("план ISK (взнос)"), N_("факт ISK (взнос)"), N_("готово"), N_("заметка")))
    pl = _places(p)

    return (
        f'<section class="card"><h2>{_esc(tr("3 · Контроль сроков и стоимости"))}</h2>'
        '<div class="muted sm">'
        + _esc(tr("Заполняй факт по мере прохождения циклов — будет видно, где план разошёлся с "
                  "реальностью. Всё сохраняется автоматически."))
        + '</div>'
        f'<h3 style="margin-top:12px">{_esc(tr("Сроки по джобам"))}'
        f'<span class="legend-inline"><i class="lg-dot lg-reaction"></i>{_esc(tr("реакция"))}'
        f'<i class="lg-dot lg-science"></i>{_esc(tr("инвента/копи"))}</span></h3>'
        '<div class="scroll"><table class="t tracking"><thead><tr>'
        f'<th>#</th>{th}</tr></thead><tbody>'
        f'{jrows}</tbody></table></div>'
        f'<h3 style="margin-top:14px">{_esc(tr("Стоимость — план vs факт (по статьям)"))}</h3>'
        '<div class="muted sm">'
        + tr("Факт бери из кошелька/журнала за эту стройку: сколько реально потратил на материалы "
             "({jita} / {cj}), доставку и взносы за джобы. По предметам факт не разносим — материалы "
             "общие, а считаем по статьям.", jita=_esc(pl["jita"]), cj=_esc(pl["cj"]))
        + '</div>'
        f'<table class="t tracking"><thead><tr><th>{_esc(tr("статья"))}</th>'
        f'<th class="r">{_esc(tr("план"))}</th>'
        f'<th>{_esc(tr("факт ISK"))}</th><th class="r">{_esc(tr("Δ (факт − план)"))}</th></tr></thead><tbody>'
        f'{brows}</tbody></table>'
        '<div class="totrow"><div>'
        + tr("План (ожид. расход): {isk}", isk=f'<b>{_isk(p["cost_control"]["plan_total"])}</b>')
        + '</div><div>'
        + tr("Факт итого: {isk}", isk='<b id="fact-total">0 ISK</b>')
        + '</div>'
        '<div>Δ: <b id="fact-delta">—</b></div></div>'
        # Отметки сохраняются сами (scheduleSave в JS) — здесь только статус «сохранено ✓».
        '<div class="actions"><span id="save-msg" class="muted sm"></span></div>'
        '</section>')


# --------------------------------------------------------------------------- assemble

# Строки, которые показывают скрипты страниц (JS): ASCII-id → русский ключ словаря. В страницу
# уходят уже переведёнными (``window.__FORGE_I18N__.t``), JS берёт их через ``T(id, {vars})`` —
# подстановки ``{name}`` те же, что у tr(). Язык — тот, что был при создании страницы.
_JS_REPORT_T = {
    "jobs_progress": N_("{done} / {total} джобов"),
    "saved": N_("сохранено ✓"),
    "saved_local": N_("сохранено локально (сервер недоступен)"),
    "copied_n": N_("скопировано ✓ ({n})"),
    "list_empty": N_("список пуст"),
    "copy_failed": N_("не удалось"),
}
_JS_INDEX_T = {
    "isk_b": N_("{v} млрд"),
    "isk_m": N_("{v} млн"),
    "isk_k": N_("{v} тыс"),
    "empty": N_("Пока нет отчётов. Сформируй отчёт из корзины («📋 Сформировать отчёт»)."),
    "delete_title": N_("Удалить стройку"),
    "meta": N_("создан {created} · ETA {eta} · {done}/{jobs} джобов · {cost}"),
    "confirm_delete": N_("Удалить стройку «{title}»? Прогресс и факт-данные удалятся безвозвратно."),
    "delete_failed": N_("Не удалось удалить отчёт."),
    "delete_failed_net": N_("Не удалось удалить (запущен ли forge web?)."),
    "load_failed": N_("Не удалось загрузить список (запущен ли forge web?)."),
}


def _js_i18n(keys: dict[str, str]) -> str:
    """``<script>`` с языком и переведёнными строками для JS страницы (см. ``_JS_REPORT_T``)."""
    data = {"lang": lang(), "locale": "en-US" if lang() == "en" else "ru-RU",
            "t": {k: tr(v) for k, v in keys.items()}}
    return ("<script>window.__FORGE_I18N__ = "
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ";</script>")


def render_report_html(p: dict) -> str:
    payload_json = json.dumps(p, ensure_ascii=False).replace("</", "<\\/")
    gen = _short_dt(p["generated_at"])
    opt = p["options"]
    on, off = tr("вкл"), tr("выкл")
    optline = (tr("ME {me} · оптимизация {build} · объединение {consolidate}", me=opt["me"],
                  build=on if opt["build"] else off, consolidate=on if opt["consolidate"] else off)
               + (tr(" · макс. {days} дн/поток", days=opt["max_stream_days"])
                  if opt["max_stream_days"] else ""))
    head = (
        '<header class="top"><div>'
        f'<a class="back" href="/reports/">{_esc(tr("← Все стройки"))}</a>'
        '<div class="brand">Forge</div>'
        '<div class="muted sm">' + _esc(p["title"] if "title" in p else tr("отчёт по стройке")) + ' · ' + _esc(gen)
        + '</div></div>'
        '<div class="muted sm topopt">' + _esc(optline) + '</div></header>')
    body = head + _summary_html(p) + _shopping_html(p) + _characters_html(p) + _tracking_html(p)
    return (
        f'<!doctype html><html lang="{lang()}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Forge · {_esc(gen)}</title><style>' + _CSS + '</style></head>'
        '<body><div class="wrap">' + body + '</div>'
        '<script>window.__FORGE_REPORT__ = ' + payload_json + ';</script>'
        + _js_i18n(_JS_REPORT_T)
        + '<script>' + _JS + '</script></body></html>')


def report_meta(payload: dict) -> dict:
    """Лёгкая карточка отчёта для страницы-списка (без тяжёлых деталей)."""
    s = payload["summary"]
    return {
        "report_id": payload["report_id"],
        "title": payload.get("title", payload["report_id"]),
        "generated_at": payload["generated_at"],
        "eta": s["eta"],
        "jobs": s["jobs"],
        "items": [{"type_id": it["type_id"], "name": it["name"], "produced": it["produced"]}
                  for it in payload["items"]],
        "total_cost": payload["totals"]["total_cost"],
        "url": "/reports/" + payload["report_id"] + ".html",
        # Что этот отчёт забрал со склада GPLB-C — резерв для других отчётов (вычитается у них,
        # с поправкой на прогресс: см. external_reservations).
        "reserved": {str(r["type_id"]): r["covered"] for r in payload["shopping"]["on_hand"]},
    }


def external_reservations(reports_dir, exclude_id: str | None = None) -> dict[int, int]:
    """Сколько складских остатков GPLB-C зарезервировано ДРУГИМИ отчётами (для вычета у нового).

    Резерв отчёта = что он забрал со склада (meta ``reserved``), пока отчёт не завершён
    ПОЛНОСТЬЮ (все джобы отмечены done в tracking.json); тогда резерв снимается целиком.
    Удалённый отчёт (файлов нет) не резервирует ничего.

    Резерв НЕ «тает» линейно по ОБЩЕЙ доле готовых джобов (done/jobs): конкретный
    материал обычно потребляют лишь НЕСКОЛЬКО джобов из отчёта (напр. пара реакций жгут
    топливные блоки, а остальные 18 джобов производства — нет). Если пользователь сперва
    отметит готовыми именно ЭТИ «прочие» джобы, линейная доля покажет резерв топлива
    уже наполовину освобождённым, хотя реакции ещё не запускались и материал физически
    ещё лежит зарезервированным — другой отчёт занизит закупку на эту разницу (пример:
    недостача 2-5 шт. Fuel Block/Platinum Technite при нескольких активных отчётах).
    Держать резерв целиком до полного завершения — консервативно (может подсказать купить
    чуть больше, чем строго нужно), но никогда не создаёт недостачи.
    """
    from pathlib import Path
    rd = Path(reports_dir)
    total: dict[int, float] = {}
    for mf in rd.glob("*.meta.json"):
        try:
            meta = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rid = meta.get("report_id", "")
        reserved = meta.get("reserved") or {}
        if not reserved or (exclude_id and rid == exclude_id):
            continue
        jobs = meta.get("jobs") or 0
        done = 0
        tf = rd / (rid + ".tracking.json")
        if rid and tf.exists():
            try:
                data = json.loads(tf.read_text(encoding="utf-8"))
                steps = data.get("steps", {}) if isinstance(data, dict) else {}
                done = sum(1 for v in steps.values() if isinstance(v, dict) and v.get("done"))
            except (OSError, json.JSONDecodeError):
                pass
        remaining = 0.0 if jobs and done >= jobs else 1.0
        for tid, qty in reserved.items():
            total[int(tid)] = total.get(int(tid), 0.0) + qty * remaining
    return {t: round(q) for t, q in total.items() if q >= 1}


def write_report(reports_dir, payload: dict, html: str) -> dict:
    """Записать HTML отчёта + meta-карточку в ``reports_dir``; обновить страницу-список."""
    from pathlib import Path
    rd = Path(reports_dir)
    rd.mkdir(parents=True, exist_ok=True)
    rid = payload["report_id"]
    filename = rid + ".html"
    (rd / filename).write_text(html, encoding="utf-8")
    (rd / (rid + ".meta.json")).write_text(
        json.dumps(report_meta(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    write_index(rd)
    return {"report_id": rid, "filename": filename, "url": "/reports/" + filename}


def delete_report_files(reports_dir, report_id: str) -> list[str]:
    """Удалить файлы отчёта (html + meta + tracking). Вернуть список удалённых имён.

    ``report_id`` обязан быть безопасным (без разделителей пути и ``..``).
    """
    from pathlib import Path
    if not report_id or any(c in report_id for c in ("/", "\\")) or ".." in report_id:
        raise ValueError(tr("Некорректный report_id"))
    rd = Path(reports_dir)
    deleted = []
    for suffix in (".html", ".meta.json", ".tracking.json"):
        f = rd / (report_id + suffix)
        if f.exists():
            f.unlink()
            deleted.append(f.name)
    return deleted


def index_html() -> str:
    """Страница-список отчётов на ТЕКУЩЕМ языке: шаблон ``_INDEX_HTML`` + строки для его JS."""
    fill = {"lang": lang(), "title": _esc(tr("Мои стройки")), "loading": _esc(tr("Загрузка…")),
            "i18n": _js_i18n(_JS_INDEX_T)}
    return re.sub(r"@@(\w+)@@", lambda m: fill[m.group(1)], _INDEX_HTML)


def write_index(reports_dir) -> None:
    """Записать (обновить) страницу-список отчётов ``reports/index.html`` (статичная, тянет /api/reports)
    на текущем языке — пульт зовёт снова после переключения RUS/ENG."""
    from pathlib import Path
    Path(reports_dir).mkdir(parents=True, exist_ok=True)
    (Path(reports_dir) / "index.html").write_text(index_html(), encoding="utf-8")


# --------------------------------------------------------------------------- CSS / JS

_CSS = r"""
:root{
  --bg:#070a0e; --fg:#dde6ef; --card:rgba(14,22,32,.62); --bd:rgba(150,200,235,.14);
  --muted:#8c9cb0; --dim:#5c6a7b; --sky:#3fb8e0; --good:#4cc38a; --bad:#e5534b; --amber:#e8a93a;
  --violet:#a68bf0;
}
*{box-sizing:border-box}
body{margin:0;background:
  radial-gradient(1100px 520px at 8% -12%, rgba(63,184,224,.13), transparent 58%),
  radial-gradient(950px 480px at 96% -8%, rgba(232,150,58,.09), transparent 55%),
  var(--bg);
  color:var(--fg);font:14px/1.5 "Segoe UI",system-ui,Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:18px 18px 60px}
.num,.r.num,table .num{font-variant-numeric:tabular-nums}
.muted{color:var(--muted)} .dim{color:var(--dim)} .sm{font-size:12px}
.good{color:var(--good)} .bad{color:var(--bad)} .warn{color:var(--amber)}
h2{font-size:15px;letter-spacing:.08em;text-transform:uppercase;color:#8ad8f2;margin:0 0 12px;font-family:Bahnschrift,"Segoe UI",sans-serif}
h3{font-size:13px;color:#dde6ef;margin:0 0 8px;font-weight:600}
.top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;
  padding:18px 20px;margin-bottom:16px;border:1px solid var(--bd);border-radius:16px;
  background:linear-gradient(135deg, rgba(63,184,224,.14), rgba(232,169,58,.05) 70%, rgba(14,22,32,.5))}
.brand{font-size:28px;font-weight:700;letter-spacing:.08em;font-family:Bahnschrift,"Segoe UI",sans-serif;background:linear-gradient(90deg,#3fb8e0,#a9e4f7 40%,#e8a93a);-webkit-background-clip:text;background-clip:text;color:transparent}
.topopt{text-align:right}
.back{display:inline-block;color:var(--muted);font-size:12px;text-decoration:none;margin-bottom:6px}
.back:hover{color:var(--sky)}
.card{border:1px solid var(--bd);background:var(--card);border-radius:16px;padding:18px 20px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
@media(max-width:720px){.grid{grid-template-columns:repeat(2,1fr)}}
.stat{border:1px solid var(--bd);background:rgba(16,27,40,.4);border-radius:12px;padding:10px 12px}
.stat-l{font-size:11px;color:var(--muted)} .stat-v{font-size:20px;font-weight:600;margin-top:2px}
.v-good{color:var(--good)} .v-bad{color:var(--bad)} .v-sky{color:var(--sky)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:720px){.two{grid-template-columns:1fr}}
.badge{font-size:11px;color:var(--sky);border:1px solid var(--bd);border-radius:999px;padding:1px 8px;margin-left:6px}
.badge.good{color:var(--good)}
.copybtn{margin-left:8px;font-size:11px;color:var(--sky);border:1px solid var(--bd);background:rgba(10,17,26,.5);
  border-radius:6px;padding:1px 8px;cursor:pointer;vertical-align:middle}
.copybtn:hover{border-color:var(--sky)}
.copybtn-sm{margin-left:4px;padding:0px 5px;font-size:11px}
table.t{width:100%;border-collapse:collapse;font-size:13px}
table.t th{font-size:10px;text-transform:uppercase;color:var(--dim);text-align:left;font-weight:500;padding:4px 6px;border-bottom:1px solid var(--bd)}
table.t td{padding:4px 6px;border-bottom:1px solid rgba(150,200,235,0.062);vertical-align:middle}
table.t .r{text-align:right} th.r{text-align:right}
.ic{width:20px;height:20px;border-radius:4px;vertical-align:middle}
.list{list-style:none;margin:0;padding:0} .list li{padding:3px 0;border-bottom:1px solid rgba(150,200,235,0.050)}
.list li.bad{color:var(--bad)}
.overall{display:flex;align-items:center;gap:18px;margin-bottom:14px}
.ring{width:90px;height:90px;flex:0 0 auto}
.ring svg{width:90px;height:90px}
.ring-bg{fill:none;stroke:rgba(150,200,235,0.125);stroke-width:8}
#ring-fg{fill:none;stroke:var(--sky);stroke-width:8;stroke-linecap:round;
  stroke-dasharray:326.7;stroke-dashoffset:326.7;transition:stroke-dashoffset .5s}
#ring-t{fill:var(--fg);font-size:24px;font-weight:700;text-anchor:middle;font-variant-numeric:tabular-nums}
.overall-meta .big{font-size:20px;font-weight:600}
.chars{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:720px){.chars{grid-template-columns:1fr}}
.charcard{border:1px solid var(--bd);border-left:4px solid var(--c);border-radius:12px;
  padding:12px 14px;background:rgba(16,27,40,.35)}
.charhead{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.charhead .dot{width:10px;height:10px;border-radius:50%;background:var(--c)}
.charhead .cprog{margin-left:auto;font-variant-numeric:tabular-nums;color:var(--c);font-weight:600}
.bar{height:6px;border-radius:4px;background:rgba(150,200,235,0.125);overflow:hidden;margin-bottom:10px}
.bar i{display:block;height:100%;width:0;background:var(--c);transition:width .4s}
.steps{list-style:none;margin:0;padding:0}
.step{display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-bottom:1px solid rgba(150,200,235,0.050)}
.step label{display:flex;gap:8px;align-items:flex-start;cursor:pointer;flex:1}
.step .ord{color:var(--dim);font-size:11px;min-width:16px;text-align:right;padding-top:2px}
.step input{margin-top:3px}
.step.done .stext{text-decoration:line-through;color:var(--dim)}
.step .when{color:var(--dim);font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}
#scurve{width:100%;height:260px;margin-top:8px;border:1px solid var(--bd);border-radius:12px;background:rgba(10,17,26,.4)}
.legend{font-size:12px;color:var(--muted);margin-top:6px;display:flex;gap:16px}
.lg-plan{color:var(--sky)} .lg-fact{color:var(--good)} .lg-now{color:var(--amber)}
.scroll{overflow-x:auto}
table.tracking input{background:rgba(10,17,26,.7);border:1px solid var(--bd);color:var(--fg);
  border-radius:6px;padding:3px 6px;font:inherit;font-size:12px;width:150px}
table.tracking input.note{width:160px} table.tracking input.spent{width:120px;text-align:right}
table.tracking input.done-cb{width:16px;height:16px;padding:0;accent-color:var(--sky);cursor:pointer}
table.t td.ctr{text-align:center}
table.tracking tr.pool-reaction{background:rgba(251,191,36,.07)}
table.tracking tr.pool-science{background:rgba(167,139,250,.09)}
table.tracking tr.pool-reaction td:first-child{box-shadow:inset 3px 0 0 var(--amber)}
table.tracking tr.pool-science td:first-child{box-shadow:inset 3px 0 0 var(--violet)}
.legend-inline{margin-left:12px;font-size:11px;font-weight:400;text-transform:none;color:var(--muted)}
.legend-inline .lg-dot{display:inline-block;width:8px;height:8px;border-radius:2px;margin:0 4px 0 10px;vertical-align:middle}
.legend-inline .lg-dot:first-child{margin-left:0}
.legend-inline .lg-reaction{background:var(--amber)}
.legend-inline .lg-science{background:var(--violet)}
.totrow{display:flex;gap:24px;flex-wrap:wrap;margin-top:12px;padding:10px 12px;border:1px solid var(--bd);
  border-radius:10px;background:rgba(16,27,40,.4)}
.actions{display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap}
.btn{background:rgba(16,27,40,.7);border:1px solid var(--bd);color:var(--fg);border-radius:8px;
  padding:8px 14px;font:inherit;cursor:pointer}
.btn:hover{border-color:var(--sky)}
.btn.primary{background:linear-gradient(135deg,rgba(63,184,224,.35),rgba(111,149,232,.2));border-color:rgba(63,184,224,.6);color:#eafaff;font-weight:600}
.btn.primary:hover{border-color:#3fb8e0}
"""

_JS = r"""
(function(){
  var P = window.__FORGE_REPORT__;
  var I = window.__FORGE_I18N__ || {lang:"ru", locale:"ru-RU", t:{}};
  var KEY = "forge_report_" + P.report_id;
  var state = {steps:{}, shop:{}, jobs:{}, items:{}, cost:{}};
  var saveTimer = null;

  // UI text from the page's i18n block (translated at render time); {name} placeholders <- vars.
  function T(k, vars){
    var s = I.t[k]; if(s == null) s = k;
    return vars ? s.replace(/\{(\w+)\}/g, function(m, n){ return (n in vars) ? String(vars[n]) : m; }) : s;
  }

  function load(){
    try{ var s = JSON.parse(localStorage.getItem(KEY)||"null"); if(s) state = Object.assign(state, s); }catch(e){}
    // pull the copy saved on the server (when opened via the local server and a backup exists)
    fetch("/api/report-tracking/"+P.report_id).then(function(r){return r.ok?r.json():null;}).then(function(srv){
      if(srv && srv.data && (!localStorage.getItem(KEY))){ state = Object.assign(state, srv.data); apply(); recompute(); }
    }).catch(function(){});
  }

  function apply(){
    document.querySelectorAll('[data-step]').forEach(function(cb){
      var st = state.steps[cb.getAttribute('data-step')];
      cb.checked = !!(st && st.done); markStep(cb);
    });
    document.querySelectorAll('[data-shop]').forEach(function(cb){ cb.checked = !!state.shop[cb.getAttribute('data-shop')]; });
    document.querySelectorAll('[data-job]').forEach(function(el){
      var j = state.jobs[el.getAttribute('data-job')]; if(j){ var v=j[el.getAttribute('data-f')]; if(v!=null) el.value=v; }
    });
    document.querySelectorAll('[data-cost]').forEach(function(el){
      var c = state.cost[el.getAttribute('data-cost')]; if(c && c.v!=null) el.value=c.v;
    });
  }

  function markStep(cb){ var li=cb.closest('.step'); if(li) li.classList.toggle('done', cb.checked); }

  function scheduleSave(){ clearTimeout(saveTimer); saveTimer=setTimeout(save, 800); }

  function save(silent){
    try{ localStorage.setItem(KEY, JSON.stringify(state)); }catch(e){}
    fetch("/api/report-tracking", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({report_id:P.report_id, data:state})})
      .then(function(r){ msg(r.ok ? T("saved") : T("saved_local")); })
      .catch(function(){ msg(T("saved_local")); });
  }
  function msg(t){ var m=document.getElementById('save-msg'); if(m){ m.textContent=t; setTimeout(function(){m.textContent="";},2500);} }

  function recompute(){
    var total = P.jobs.length, done = 0;
    P.jobs.forEach(function(j){ if(state.steps[j.job_id] && state.steps[j.job_id].done) done++; });
    var pct = total? done/total : 0;
    var off = 326.7 * (1-pct);
    var fg=document.getElementById('ring-fg'); if(fg) fg.style.strokeDashoffset = off.toFixed(1);
    var rt=document.getElementById('ring-t'); if(rt) rt.textContent = Math.round(pct*100)+"%";
    var pl=document.getElementById('prog-line'); if(pl) pl.textContent = T("jobs_progress", {done:done, total:total});
    // per-character progress
    P.characters.forEach(function(c){
      var t=c.steps.length, d=0; c.steps.forEach(function(s){ if(state.steps[s.step_id] && state.steps[s.step_id].done) d++; });
      var p = t? d/t : 0;
      var bar=document.querySelector('[data-cbar="'+c.character_id+'"]'); if(bar) bar.style.width=(p*100)+"%";
      var lbl=document.querySelector('[data-cprog="'+c.character_id+'"]'); if(lbl) lbl.textContent=Math.round(p*100)+"%";
    });
    // actual job fees summed from the job timeline table: invention (activity_id=8) and the T1
    // copying for it (activity_id=5) go SEPARATELY to the "blueprints/invention" cost item, not
    // to "job fees" (the plan puts copies there too — node.blueprint_cost).
    var INVENTION_ACTIVITY=8, COPY_ACTIVITY=5;
    var activityOf={}; P.jobs.forEach(function(j){ activityOf[j.job_id]=j.activity_id; });
    var jobsFact=0, jobsAny=false, bpFact=0, bpAny=false;
    Object.keys(state.jobs).forEach(function(k){
      var v=parseFloat((state.jobs[k]||{}).cost); if(isNaN(v)) return;
      if(activityOf[k]===INVENTION_ACTIVITY || activityOf[k]===COPY_ACTIVITY){ bpFact+=v; bpAny=true; }
      else { jobsFact+=v; jobsAny=true; }
    });
    var ac=document.querySelector('[data-autocost="jobs"]'); if(ac) ac.textContent = jobsAny ? fmtIsk(jobsFact) : "—";
    var acb=document.querySelector('[data-autocost="blueprints"]'); if(acb) acb.textContent = bpAny ? fmtIsk(bpFact) : "—";
    // actual cost per cost item (plan vs actual), with a delta for each item
    var buckets=(P.cost_control&&P.cost_control.buckets)||[], factTotal=0, anyFact=false;
    buckets.forEach(function(b){
      var v, has;
      if(b.key==="jobs"){ v=jobsFact; has=jobsAny; }            // auto-sum from the job timeline
      else if(b.key==="blueprints"){ v=bpFact; has=bpAny; }     // auto-sum (invention/copying only)
      else { v=parseFloat((state.cost[b.key]||{}).v); has=!isNaN(v); }
      if(has){ factTotal+=v; anyFact=true; }
      var dc=document.querySelector('[data-delta="'+b.key+'"]');
      if(dc){ if(has){ var d=v-b.plan; dc.textContent=(d>=0?"+":"")+fmtIsk(d); dc.className="r num "+(d>0?"bad":"good"); }
              else { dc.textContent="—"; dc.className="r num muted"; } }
    });
    var ft=document.getElementById('fact-total'); if(ft) ft.textContent = fmtIsk(factTotal);
    var dl=document.getElementById('fact-delta');
    if(dl){ if(anyFact){ var d2=factTotal-(P.cost_control?P.cost_control.plan_total:0);
              dl.textContent=(d2>=0?"+":"")+fmtIsk(d2); dl.className=d2>0?"bad":"good"; } else dl.textContent="—"; }
    drawCurve();
  }

  function fmtIsk(v){ return Math.round(v).toLocaleString(I.locale)+" ISK"; }

  function fmtDt(ms){
    var d=new Date(ms);
    function p2(n){ return (n<10?'0':'')+n; }
    var hm=p2(d.getHours())+':'+p2(d.getMinutes());
    if(I.lang==="en") return p2(d.getDate())+' '+d.toLocaleString('en-US',{month:'short'})+' '+hm;
    return p2(d.getDate())+'.'+p2(d.getMonth()+1)+' '+hm;
  }

  function drawCurve(){
    var svg=document.getElementById('scurve'); if(!svg) return;
    var W=720,H=260,padL=40,padR=14,padT=14,padB=28;
    var t0=P.generated_at_ms, t1=Math.max(P.summary.eta_ms, Date.now()+1);
    var total=P.jobs.length||1;
    function X(ms){ return padL + (W-padL-padR) * Math.min(1,Math.max(0,(ms-t0)/(t1-t0))); }
    function Y(frac){ return padT + (H-padT-padB) * (1-frac); }
    var out="";
    // Y grid (0/25/50/75/100%)
    for(var g=0; g<=4; g++){ var y=Y(g/4); out+='<line x1="'+padL+'" y1="'+y+'" x2="'+(W-padR)+'" y2="'+y+'" stroke="rgba(150,200,235,0.100)"/>'+
      '<text x="6" y="'+(y+4)+'" fill="#64748b" font-size="10">'+(g*25)+'%</text>'; }
    // X axis: evenly spaced time ticks between the report start and the ETA (or "now")
    var ticks=5;
    for(var t=0; t<=ticks; t++){
      var ms=t0+(t1-t0)*(t/ticks), x=X(ms);
      var anchor = t===0 ? 'start' : (t===ticks ? 'end' : 'middle');
      out+='<line x1="'+x+'" y1="'+padT+'" x2="'+x+'" y2="'+(H-padB)+'" stroke="rgba(150,200,235,0.062)"/>'+
        '<text x="'+x+'" y="'+(H-8)+'" fill="#64748b" font-size="10" text-anchor="'+anchor+'">'+fmtDt(ms)+'</text>';
    }
    // plan: cumulative by the planned START (launch) of jobs — matches the checkboxes, which mark
    // the fact of LAUNCHING a job, not a finished product.
    var starts = P.jobs.map(function(j){return j.start_s;}).sort(function(a,b){return a-b;});
    var pts="M "+X(t0)+" "+Y(0);
    starts.forEach(function(ss,i){ var x=X(t0+ss*1000); pts+=" L "+x+" "+Y(i/total)+" L "+x+" "+Y((i+1)/total); });
    out+='<path d="'+pts+'" fill="none" stroke="#38bdf8" stroke-width="2" stroke-dasharray="5 4"/>';
    // actual: by the "launched" marks (character checklist / job timeline checkboxes, done_at)
    var dones=[]; Object.keys(state.steps).forEach(function(k){ var s=state.steps[k]; if(s.done && s.at) dones.push(s.at); });
    dones.sort(function(a,b){return a-b;});
    if(dones.length){ var fp="M "+X(t0)+" "+Y(0); dones.forEach(function(at,i){ var x=X(at); fp+=" L "+x+" "+Y(i/total)+" L "+x+" "+Y((i+1)/total); });
      out+='<path d="'+fp+'" fill="none" stroke="#34d399" stroke-width="2.5"/>'; }
    // "now" line
    var nx=X(Date.now()); out+='<line x1="'+nx+'" y1="'+padT+'" x2="'+nx+'" y2="'+(H-padB)+'" stroke="#fbbf24" stroke-dasharray="3 3"/>';
    svg.innerHTML=out;
  }

  function bind(){
    document.querySelectorAll('[data-step]').forEach(function(cb){
      cb.addEventListener('change', function(){
        var id=cb.getAttribute('data-step');
        var s=state.steps[id]||{}; s.done=cb.checked; if(cb.checked && !s.at) s.at=Date.now(); state.steps[id]=s;
        // the same job is checked both in the character instructions and in the job timeline —
        // sync ALL such checkboxes live, without waiting for a page reload.
        document.querySelectorAll('[data-step="'+id+'"]').forEach(function(other){
          other.checked = cb.checked; markStep(other);
        });
        recompute(); scheduleSave();
      });
    });
    document.querySelectorAll('[data-shop]').forEach(function(cb){
      cb.addEventListener('change', function(){ state.shop[cb.getAttribute('data-shop')]=cb.checked; scheduleSave(); });
    });
    document.querySelectorAll('[data-copy]').forEach(function(btn){
      btn.addEventListener('click', function(){
        var rows = P.shopping[btn.getAttribute('data-copy')] || [];
        // EVE multibuy format: "Name\tQty\t-\t-", with a Total line at the end.
        var lines = rows.map(function(r){ return r.name + '\t' + r.quantity + '\t-\t-'; });
        lines.push('Total:\t\t\t0');
        var orig = btn.textContent;
        navigator.clipboard.writeText(lines.join('\n')).then(function(){
          btn.textContent = rows.length ? T("copied_n", {n: rows.length}) : T("list_empty");
          setTimeout(function(){ btn.textContent = orig; }, 1800);
        }).catch(function(){ btn.textContent = T("copy_failed"); setTimeout(function(){ btn.textContent = orig; }, 1800); });
      });
    });
    document.querySelectorAll('[data-copy-name]').forEach(function(btn){
      btn.addEventListener('click', function(){
        var name = btn.getAttribute('data-copy-name');
        var orig = btn.textContent;
        navigator.clipboard.writeText(name).then(function(){
          btn.textContent = '✓'; setTimeout(function(){ btn.textContent = orig; }, 1200);
        }).catch(function(){ btn.textContent = '✗'; setTimeout(function(){ btn.textContent = orig; }, 1200); });
      });
    });
    document.querySelectorAll('[data-job]').forEach(function(el){
      el.addEventListener('input', function(){ var id=el.getAttribute('data-job'); var f=el.getAttribute('data-f');
        (state.jobs[id]=state.jobs[id]||{})[f]=el.value; if(f==='cost') recompute(); scheduleSave(); });
    });
    document.querySelectorAll('[data-cost]').forEach(function(el){
      el.addEventListener('input', function(){ var k=el.getAttribute('data-cost');
        (state.cost[k]=state.cost[k]||{}).v=el.value; recompute(); scheduleSave(); });
    });
  }

  load(); apply(); bind(); recompute();
  setInterval(drawCurve, 60000); // move the "now" line
})();
"""


# Страница-список всех строек: статична, тянет /api/reports (живой прогресс из трекинга).
# ``@@имя@@`` — подстановки ``index_html()`` (язык, переведённые заголовки, строки для JS).
_INDEX_HTML = r"""<!doctype html><html lang="@@lang@@"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Forge · @@title@@</title>
<style>
:root{--bg:#070a0e;--fg:#dde6ef;--card:rgba(14,22,32,.62);--bd:rgba(150,200,235,.14);--muted:#8c9cb0;--dim:#5c6a7b;--sky:#3fb8e0}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1100px 520px at 8% -12%,rgba(63,184,224,.13),transparent 58%),radial-gradient(950px 480px at 96% -8%,rgba(232,150,58,.09),transparent 55%),var(--bg);
  color:var(--fg);font:14px/1.5 "Segoe UI",system-ui,Roboto,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:18px 18px 60px}
.muted{color:var(--muted)} .dim{color:var(--dim)}
.top{display:flex;justify-content:space-between;align-items:flex-end;padding:18px 20px;margin-bottom:16px;
  border:1px solid var(--bd);border-radius:16px;background:linear-gradient(135deg,rgba(63,184,224,.14),rgba(232,169,58,.05) 70%,rgba(14,22,32,.5))}
.brand{font-size:28px;font-weight:700;letter-spacing:.08em;font-family:Bahnschrift,"Segoe UI",sans-serif;background:linear-gradient(90deg,#3fb8e0,#a9e4f7 40%,#e8a93a);-webkit-background-clip:text;background-clip:text;color:transparent}
.rcard{position:relative;border:1px solid var(--bd);background:var(--card);
  border-radius:14px;margin-bottom:12px;transition:border-color .2s}
.rcard:hover{border-color:var(--sky)}
.rlink{display:block;text-decoration:none;color:inherit;padding:14px 48px 14px 16px}
.del{position:absolute;top:10px;right:10px;width:28px;height:28px;border:1px solid var(--bd);
  background:rgba(10,17,26,.6);color:var(--dim);border-radius:8px;cursor:pointer;font-size:13px;line-height:1}
.del:hover{color:#fb7185;border-color:#fb7185}
.rhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.rhead b{font-size:16px} .pct{color:var(--sky);font-weight:600;font-variant-numeric:tabular-nums}
.bar{height:8px;border-radius:5px;background:rgba(150,200,235,0.125);overflow:hidden;margin-bottom:8px}
.bar i{display:block;height:100%;background:linear-gradient(90deg,#3fb8e0,#e8a93a);transition:width .4s}
.meta{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.empty{border:1px dashed var(--bd);border-radius:14px;padding:40px;text-align:center;color:var(--muted)}
</style></head><body><div class="wrap">
<header class="top"><div class="brand">Forge</div><div class="muted">@@title@@</div></header>
<div id="list" class="muted">@@loading@@</div></div>
@@i18n@@
<script>
var I=window.__FORGE_I18N__||{lang:'ru',locale:'ru-RU',t:{}};
function T(k,v){var s=I.t[k];if(s==null)s=k;return v?s.replace(/\{(\w+)\}/g,function(m,n){return (n in v)?String(v[n]):m;}):s;}
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
function p2(n){return (n<10?'0':'')+n;}
function fdate(iso){try{var d=new Date(iso);
  if(I.lang==='en'){if(isNaN(d.getTime()))return iso||'';return p2(d.getDate())+' '+d.toLocaleString('en-US',{month:'short'})+' '+p2(d.getHours())+':'+p2(d.getMinutes());}
  return d.toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});}catch(e){return iso||'';}}
function fisk(v){if(v==null)return '—';var a=Math.abs(v);if(a>=1e9)return T('isk_b',{v:(v/1e9).toFixed(2)});if(a>=1e6)return T('isk_m',{v:(v/1e6).toFixed(1)});if(a>=1e3)return T('isk_k',{v:(v/1e3).toFixed(0)});return Math.round(v).toLocaleString(I.locale);}
fetch('/api/reports').then(function(r){return r.json();}).then(function(reports){
  var el=document.getElementById('list');
  if(!reports || !reports.length){ el.className='empty'; el.textContent=T('empty'); return; }
  el.className=''; el.innerHTML='';
  reports.forEach(function(r){
    var pct = r.progress!=null ? Math.round(r.progress*100) : 0;
    var card=document.createElement('div'); card.className='rcard';
    card.innerHTML = '<button class="del" title="'+esc(T('delete_title'))+'">✕</button>'
      + '<a class="rlink" href="'+r.url+'">'
      + '<div class="rhead"><b>'+esc(r.title)+'</b><span class="pct">'+pct+'%</span></div>'
      + '<div class="bar"><i style="width:'+pct+'%"></i></div>'
      + '<div class="meta">'+esc(T('meta',{created:fdate(r.generated_at), eta:fdate(r.eta),
          done:(r.done||0), jobs:(r.jobs||0), cost:fisk(r.total_cost)}))+'</div></a>';
    card.querySelector('.del').addEventListener('click', function(){
      if(!confirm(T('confirm_delete',{title:r.title}))) return;
      fetch('/api/reports/'+encodeURIComponent(r.report_id), {method:'DELETE'}).then(function(resp){
        if(resp.ok){ card.remove(); if(!el.querySelector('.rcard')){ el.className='empty';
          el.textContent=T('empty'); } }
        else alert(T('delete_failed'));
      }).catch(function(){ alert(T('delete_failed_net')); });
    });
    el.appendChild(card);
  });
}).catch(function(){ document.getElementById('list').textContent=T('load_failed'); });
</script></body></html>
"""
