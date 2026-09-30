"""LLC-упорядоченный неттинг склада для дерева постройки корзины (вызывается из
``web/report.py``).

Живёт на уровне ``web`` — ``core`` остаётся полностью не знающим про склад (design-решение
проекта, см. docs/ARCHITECTURE.md и докстринг ``build_report_payload``: себестоимость = полное замещение,
стабильная база для план/факт калибровки; склад нетится только здесь).

Алгоритм — классический многоуровневый неттинг спецификации (в MRP/ERP-системах — "low-level
coding", LLC): каждый компонент (по ``(product_type_id, blueprint_type_id, activity_id)``)
обрабатывается РОВНО один раз, на САМОМ ГЛУБОКОМ уровне, на котором он вообще встречается в
дереве. Это гарантирует, что весь спрос от ВСЕХ родителей компонента уже известен и
финализирован (родители обработаны раньше — их LLC строго меньше) ДО того, как мы сами списываем
склад под этот компонент и, если нужно, пересобираем его. Никаких повторов и отката: retry-подход
(«после обхода дерева искать не слитые дубликаты и переобходить с чистого листа») не годится —
при пересечении нескольких общих компонентов сразу починка ОДНОГО дубликата в очередном раунде
может тихо ПОТЕРЯТЬ уже верно объединённую ветку спроса у ДРУГОГО компонента. LLC-порядок решает
это по построению — переобходов и мутации задним числом просто нет.

**Почему ``consolidate=False`` — ОТДЕЛЬНЫЙ, простой рекурсивный путь, а не тот же LLC-алгоритм.**
Ключ LLC-пути (``key_of``) при ``consolidate=True`` — ТИП компонента, а не identity объекта:
это критично, потому что пересборка узла (``sourcing.build_node_cost``) всегда создаёт СОВЕРШЕННО
НОВЫЕ дочерние объекты — тип у них тот же (та же строка в предпосчитанном графе), а identity
всегда новый. При ``consolidate=False`` ключ — ``id(node)`` (нарочно, чтобы НЕ сливать то, что
юзер попросил не сливать) — а значит, дочерние объекты СВЕЖЕЙ пересборки НИКОГДА не совпадут по
ключу ни с чем в заранее построенном графе (сплайс упал бы с ``KeyError`` на потомке, впервые
появившемся только после пересборки родителя). Без слияния между ветками LLC/граф вообще не
нужны — у каждого узла ровно один родитель, и обычная рекурсия сверху вниз
(``_net_independent_branches``) корректна по построению.

Ограничение пересборки (только для ``consolidate=True``): на изменившемся количестве она
вольна заново решать buy/build с нуля для КАЖДОГО материала — на ПОЧТИ той же величине спроса
(489 прогонов против 488) выбор может молча перевернуться с «купить» на «строить» на тип,
которого нет ВООБЩЕ нигде в дереве Прохода 1: новое под-дерево не попадёт в предпосчитанный
набор ключей, его собственный склад не учтётся, а стоимость раздуется на сотни миллионов ISK.
Защита — не здесь, а на уровне вызывающего (``web/report.py``): материалы, купленные Проходом 1
и НИГДЕ в корзине не построенные, передаются в ``build_kwargs["force_buy_extra"]`` каждой
пересборки — это не даёт ``sourcing._source_material`` вообще пробовать «строить» для них
заново, так что новых типов, которых не было бы в графе Прохода 1, эта пересборка не порождает."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable

from ..core import bom, cost, sourcing

Key = tuple[int, int, int] | int  # (product_type_id, blueprint_type_id, activity_id) | id(node)

TakeFromStock = Callable[[int, str, int, "float | None"], int]
ClassifyBuilt = Callable[[sourcing.NodeResult], None]
AddBuy = Callable[[int, str, int, "float | None", "str | None", bool], None]
NoteNoPrice = Callable[[int, str, int], None]


def key_of(node: sourcing.NodeResult, consolidate: bool) -> Key:
    """Ключ узла для группировки общего спроса.

    При ``consolidate=False`` — identity узла (``id(node)``): каждый узел уникален сам по
    себе — ровно то, что нужно, когда юзер явно выключил объединение общих
    компонентов. Используется только структурными функциями Фазы 1 (``build_key_dag``/
    ``compute_llc``) и напрямую в тестах — ``net_and_finalize`` для ``consolidate=False`` эти
    функции не вызывает вообще (см. докстринг модуля)."""
    if not consolidate:
        return id(node)
    return (node.product_type_id, node.blueprint_type_id, node.activity_id)


def build_key_dag(
    roots: list[sourcing.NodeResult], consolidate: bool
) -> tuple[dict[Key, sourcing.NodeResult], dict[Key, set[Key]], dict[Key, set[Key]]]:
    """Обойти дерево ОДИН раз (до всякого неттинга склада) и построить статичную структуру по
    ключам — больше не меняется, даже когда неттинг ниже пересобирает узлы.

    Возвращает ``(canonical, parent_keys_of, children_keys_of)``:
    - ``canonical[key]`` — узел-представитель этого ключа (для ``produced``/``runs``/``streams``
      исходного, ещё не уменьшенного складом дерева);
    - ``parent_keys_of[key]`` — множество ключей-родителей (кто ссылается на этот компонент);
    - ``children_keys_of[key]`` — обратное отображение.

    Дедуплицирует обход по identity узла — общий (после ``core.sourcing.
    consolidate_shared_components``) узел обходится один раз, даже если на него ссылаются
    несколько родителей или несколько корней корзины."""
    canonical: dict[Key, sourcing.NodeResult] = {}
    parent_keys_of: dict[Key, set[Key]] = {}
    children_keys_of: dict[Key, set[Key]] = {}
    visited_ids: set[int] = set()

    def visit(node: sourcing.NodeResult) -> None:
        if id(node) in visited_ids:
            return
        visited_ids.add(id(node))
        k = key_of(node, consolidate)
        canonical.setdefault(k, node)
        parent_keys_of.setdefault(k, set())
        children_keys_of.setdefault(k, set())
        for ln in node.lines:
            if ln.child is not None:
                ck = key_of(ln.child, consolidate)
                children_keys_of[k].add(ck)
                parent_keys_of.setdefault(ck, set()).add(k)
                visit(ln.child)

    for root in roots:
        visit(root)
    return canonical, parent_keys_of, children_keys_of


def compute_llc(parent_keys_of: dict[Key, set[Key]]) -> dict[Key, int]:
    """Low-level code: для каждого ключа — самый глубокий уровень, на котором он встречается
    в дереве (``0`` у ключей без родителей, т.е. у корней). Обработка ключей в порядке
    ВОЗРАСТАНИЯ этого числа гарантирует, что прямой родитель обработан строго раньше любого
    своего прямого потомка — а значит, ко времени обработки потомка известен ПОЛНЫЙ, уже
    финализированный спрос от всех его родителей, а не оценка."""
    llc: dict[Key, int] = {}

    def resolve(k: Key) -> int:
        if k in llc:
            return llc[k]
        llc[k] = 0  # временная защита от цикла (дерево ациклично по построению build_node_cost)
        parents = parent_keys_of.get(k) or set()
        llc[k] = (max(resolve(p) for p in parents) + 1) if parents else 0
        return llc[k]

    for k in parent_keys_of:
        resolve(k)
    return llc


def net_and_finalize(
    conn: sqlite3.Connection,
    roots: list[sourcing.NodeResult],
    params: cost.BuildParams,
    *,
    consolidate: bool,
    take_from_stock: TakeFromStock,
    classify_built: ClassifyBuilt,
    add_buy: AddBuy,
    note_no_price: NoteNoPrice,
    build_kwargs: dict,
) -> dict[Key, sourcing.NodeResult | None]:
    """Списать склад и, где нужно, пересобрать дерево постройки. Мутирует ``roots`` (и их
    поддеревья) IN PLACE: у каждой строки, ссылающейся на построенный материал, ``ln.child``
    выставляется на финальный (после вычета склада) узел этого компонента или на ``None`` при
    полном покрытии складом.

    ``roots`` — уже отфильтрованный список ВЕРХНИХ узлов корзины (без непостроиваемых и без
    тех, чей верхний продукт целиком на складе — ``skip_est``), уже пропущенных через
    цикл шринка верхних продуктов ПО СЕБЕ САМИМ (``report.py``, до вызова этой
    функции) — эта функция НЕ списывает склад повторно под сами ``roots`` и не
    пересобирает их: их ``produced`` уже финален, взят как есть. Она занимается ТОЛЬКО тем, что
    лежит НИЖЕ — материалами/под-компонентами на любой глубине.

    ``take_from_stock``/``classify_built``/``add_buy``/``note_no_price`` — замыкания
    ``build_report_payload``; эта функция только решает,
    КОГДА и на каком (уже правильно объединённом) узле их вызывать. ``build_kwargs`` —
    ``default_me``/``default_te``/``max_depth``/``allow_build``/``whole_blueprint``/
    ``substream_fn``/``force_buy_extra`` для ``sourcing.build_node_cost`` — передаются как есть.

    При ``consolidate=False`` делегирует на ``_net_independent_branches`` (простая рекурсия,
    без LLC/статичного графа — см. докстринг модуля, почему единый алгоритм здесь не годится).

    Ничего не пересчитывает по стоимости ВЫШЕ уровня самого пересобранного узла — ``totals``/
    ``items`` в ``report.py`` посчитаны РАНЬШЕ, от нетронутого дерева Прохода 1 (design-решение
    «полное замещение» как стабильная база план/факт, см. докстринг модуля)."""
    if not consolidate:
        return _net_independent_branches(
            conn, roots, params,
            take_from_stock=take_from_stock, classify_built=classify_built,
            add_buy=add_buy, note_no_price=note_no_price, build_kwargs=build_kwargs,
        )

    canonical, parent_keys_of, _children_keys_of = build_key_dag(roots, consolidate=True)
    llc = compute_llc(parent_keys_of)
    order = sorted(parent_keys_of, key=lambda k: llc[k])
    root_keys = {key_of(r, True) for r in roots}

    current: dict[Key, sourcing.NodeResult | None] = {}

    for k in order:
        node = canonical[k]

        if k in root_keys:
            final_node = node  # уже пересобран (если нужно) до вызова этой функции — не трогаем
        else:
            gross_demand = 0
            for pk in parent_keys_of[k]:
                parent_final = current[pk]  # родитель гарантированно уже обработан (LLC меньше)
                if parent_final is None:
                    continue  # родитель целиком на складе — не строится вообще, спроса от него нет
                for ln in parent_final.lines:
                    if ln.child is not None and key_of(ln.child, True) == k:
                        gross_demand += ln.quantity

            if gross_demand <= 0:
                # ВСЕ родители этого ключа сами целиком на складе (current[pk] is None) — этот
                # компонент реально не нужен вообще, ни в каком количестве. НЕ вызываем
                # take_from_stock: иначе (если у юзера случайно есть немного этого типа на складе
                # по несвязанной причине) он попадёт в отчёт строкой «0/0» — шум без экономии.
                current[k] = None
                continue

            remaining = take_from_stock(node.product_type_id, node.name, gross_demand, node.unit_cost)
            if remaining <= 0:
                current[k] = None
                continue

            # ``bp.product_qty_per_run`` — НЕ ``node.produced / node.runs``: для узла-обёртки
            # переработки (``blueprint_source == "reprocess"``) поле ``runs`` НЕ значит «прогонов
            # чертежа» — ``_try_reprocess_node`` кладёт туда ``target_quantity`` (запрошенное
            # количество), поэтому ``produced/runs`` там ≈ 1 (produced лишь слегка овершутит
            # target из-за округления по целым порциям переработки). С ``produced/runs``
            # `new_runs` получился бы ≈ remaining (в единицах ПРОДУКТА), но передался бы в
            # build_node_cost как «прогонов ЧЕРТЕЖА» — тот умножил бы его на qty_per_run чертежа
            # ЕЩЁ РАЗ, раздувая produced в ~200 раз (пример: Nomad, эфф. переработки
            # 55% — Prometium запрошено 9348, пересобралось бы на 1 862 839; недостача Unrefined
            # Prometium — с адекватных 240 прогонов (как в Калькуляторе, БЕЗ этой пересборки) до
            # 46 397 в отчёте). ``bom.blueprint_for_product`` даёт РЕАЛЬНЫЙ чертёж продукта
            # (Prometium Reaction Formula и т.п.) независимо от того, будет ли build_node_cost
            # внутри в итоге снова выбирать переработку — та же логика, что и
            # в ``_source_material`` для обычных материалов.
            bp = bom.blueprint_for_product(conn, node.product_type_id)
            qty_per_run = bp.product_qty_per_run if bp is not None else 0
            if qty_per_run <= 0:
                final_node = node
            else:
                # ВСЕГДА пересобираем заново, без short-circuit «размер не изменился» — на
                # LLC-упорядоченном проходе gross_demand для НЕ-корневого ключа может отличаться
                # от исходного (родитель мог уже сжаться), даже когда remaining == gross_demand
                # (склада на этом уровне вообще нет) — переиспользование объекта в этом случае
                # тихо потеряло бы объединённый спрос (см. докстринг модуля).
                new_runs = max(1, math.ceil(remaining / qty_per_run))
                # С явным «Макс. дней/поток» потоки ОБЯЗАНЫ пересчитываться под НОВЫЙ new_runs
                # через тот же substream_fn, что и у обычных материалов (_source_material) — не
                # наследовать старый node.streams (посчитан под ДРУГОЕ, обычно меньшее,
                # gross_demand на предыдущем проходе): иначе срок «≤ N дней/поток» тихо
                # перестаёт соблюдаться именно там, где это чаще всего критично — LLC-пересборка
                # типично УВЕЛИЧИВАЕТ спрос (сумма от нескольких родителей). Пример:
                # переработка Nomad (низкий выход «Unrefined X» — 1 ед./прогон) довела бы
                # джоб до 447 дней вместо заданных ≤3 дней/поток — streams=2 Прохода 1 (посчитан
                # под маленький исходный спрос) тащился бы на пересборку с multi-кратно бОльшим
                # new_runs без переоценки.
                #
                # БЕЗ явного срока (substream_fn=None) — наследуем streams самого узла Прохода 1
                # (min с new_runs, чтобы не дробить мельче, чем есть работы), а не хардкодим 1:
                # та же ловушка, что и с buy/build (см. build_report_payload/pass1_market_
                # only) — «ВСЕГДА пересобираем заново» иначе молча стёр бы параллелизм, который
                # consolidate_shared_components(auto_streams=True) намеренно дал общей постройке
                # (по числу слитых веток), даже когда gross_demand практически не изменился.
                substream_fn = build_kwargs.get("substream_fn")
                new_streams = (
                    substream_fn(node.product_type_id, new_runs) if substream_fn
                    else min(node.streams, new_runs)
                )
                final_node = sourcing.build_node_cost(
                    conn, node.product_type_id, new_runs, new_streams, params, **build_kwargs
                )

        current[k] = final_node
        classify_built(final_node)
        _process_leaf_lines(
            final_node, take_from_stock=take_from_stock, add_buy=add_buy, note_no_price=note_no_price
        )

    # Сплайс: проставить ln.child на финальные узлы по всему дереву — структурный обход от
    # корней, дедуп по identity (дерево может быть DAG — общий узел достижим из нескольких
    # родителей/корней).
    spliced: set[int] = set()

    def splice(node: sourcing.NodeResult) -> None:
        if id(node) in spliced:
            return
        spliced.add(id(node))
        for ln in node.lines:
            if ln.child is not None:
                k = key_of(ln.child, True)
                final_node = current[k]
                ln.child = final_node
                if final_node is not None:
                    splice(final_node)

    for root in roots:
        splice(root)

    return current


def _process_leaf_lines(
    node: sourcing.NodeResult, *, take_from_stock: TakeFromStock, add_buy: AddBuy, note_no_price: NoteNoPrice
) -> None:
    """Списать склад/учесть покупку для ЛИСТОВЫХ (``ln.child is None``) строк узла —
    построенные материалы (``ln.child is not None``) сюда не входят, они обрабатываются на
    СВОЁМ ключе отдельно (LLC-путь) или отдельным рекурсивным вызовом (независимый путь)."""
    for ln in node.lines:
        if ln.child is not None:
            continue
        if ln.source == "buy":
            r = take_from_stock(ln.type_id, ln.name, ln.quantity, ln.unit_cost)
            if r > 0:
                unit = (ln.subtotal / ln.quantity) if ln.quantity and ln.subtotal is not None else 0.0
                add_buy(ln.type_id, ln.name, r, unit, ln.buy_hub, ln.buy_shortage)
        elif ln.source == "unknown":
            r = take_from_stock(ln.type_id, ln.name, ln.quantity, ln.unit_cost)
            if r > 0:
                note_no_price(ln.type_id, ln.name, r)


def _net_independent_branches(
    conn: sqlite3.Connection,
    roots: list[sourcing.NodeResult],
    params: cost.BuildParams,
    *,
    take_from_stock: TakeFromStock,
    classify_built: ClassifyBuilt,
    add_buy: AddBuy,
    note_no_price: NoteNoPrice,
    build_kwargs: dict,
) -> dict[Key, sourcing.NodeResult | None]:
    """``consolidate=False``: общих компонентов между ветками нет (по определению — юзер явно
    выключил объединение) — значит, у каждого узла РОВНО один родитель, координировать нечего,
    и обычная рекурсия сверху вниз корректна по построению (см. докстринг модуля про то, почему
    LLC-путь здесь неприменим). ``current`` ключуется по ``id()`` исходного (Прохода 1) узла —
    только для единообразия сигнатуры с основным путём, сама функция это не переиспользует."""
    current: dict[Key, sourcing.NodeResult | None] = {}

    def net_one(node: sourcing.NodeResult, gross_demand: int) -> sourcing.NodeResult | None:
        remaining = take_from_stock(node.product_type_id, node.name, gross_demand, node.unit_cost)
        if remaining <= 0:
            return None
        # См. докстринг на аналогичной строке основного (LLC) пути выше: для узла-обёртки
        # переработки ``node.runs`` хранит ``target_quantity``, а не «прогонов чертежа» —
        # та же ловушка возможна и здесь (переработка не зависит от consolidate).
        bp = bom.blueprint_for_product(conn, node.product_type_id)
        qty_per_run = bp.product_qty_per_run if bp is not None else 0
        final_node = node
        if qty_per_run > 0:
            new_runs = max(1, math.ceil(remaining / qty_per_run))
            final_node = sourcing.build_node_cost(
                conn, node.product_type_id, new_runs, node.streams, params, **build_kwargs
            )
        classify_built(final_node)
        _process_leaf_lines(final_node, take_from_stock=take_from_stock, add_buy=add_buy, note_no_price=note_no_price)
        for ln in final_node.lines:
            if ln.child is not None:
                ln.child = net_one(ln.child, ln.quantity)
        return final_node

    for root in roots:
        current[id(root)] = root  # корень уже финален (см. общий докстринг net_and_finalize)
        classify_built(root)
        _process_leaf_lines(root, take_from_stock=take_from_stock, add_buy=add_buy, note_no_price=note_no_price)
        for ln in root.lines:
            if ln.child is not None:
                ln.child = net_one(ln.child, ln.quantity)

    return current
