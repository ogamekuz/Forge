"""Движок рекомендаций «что строить» (без сети).

Для каждого кандидата считает себестоимость/прибыль (core), ISK/час (через время джоба)
и ликвидность («Объём/сут» — источник по ``[recommend] liquidity_source``, см. ``Liquidity``),
применяет жёсткие фильтры (бюджет, мин. объём) и ранжирует по взвешенной сумме нормированных
метрик.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from .. import core
from ..config import Config
from ..core import prices
from ..planner import timing


@dataclass
class Recommendation:
    product_type_id: int
    name: str
    runs: int
    capital: float          # вложение на партию (себестоимость)
    unit_cost: float
    unit_profit: float
    roi: float
    isk_per_hour: float
    daily_volume: float
    score: float = 0.0


def _in(ids) -> str:
    return ",".join("?" for _ in ids)


def exclusion_clause(cfg: Config | None, column: str = "p.product_type_id") -> tuple[str, list[int]]:
    """``AND …`` для «запретов» рекомендаций ([recommend] exclude_type/group/category_ids):
    эти продукты не предлагаются нигде — ни в ТОП, ни в «из остатков», ни в «дешевле купить»."""
    if cfg is None:
        return "", []
    rc = cfg.recommend
    types = [int(x) for x in rc.exclude_type_ids]
    groups = [int(x) for x in rc.exclude_group_ids]
    cats = [int(x) for x in rc.exclude_category_ids]
    sql, params = "", []
    if types:
        sql += f" AND {column} NOT IN ({_in(types)})"
        params += types
    if groups or cats:
        cond = []
        if groups:
            cond.append(f"group_id IN ({_in(groups)})")
            params += groups
        if cats:
            cond.append(f"category_id IN ({_in(cats)})")
            params += cats
        sql += f" AND {column} NOT IN (SELECT type_id FROM sde_types WHERE {' OR '.join(cond)})"
    return sql, params


def candidate_products(
    conn: sqlite3.Connection,
    owned: bool = True,
    limit: int | None = None,
    group_ids: list[int] | None = None,
    *,
    location_ids=None,
    cfg: Config | None = None,
) -> list[int]:
    """type_id продуктов-кандидатов: из своих чертежей (owned) либо все производимые.

    ``group_ids`` ограничивает выдачу продуктами из указанных групп SDE (для ТОП по группам).
    ``location_ids`` — «Где искать чертежи» (уже с контейнерами, см. BuildParams): «свои» —
    только чертежи в этих локациях, как и в самом расчёте себестоимости (иначе ТОП брал бы любые
    чертежи чаров, а расчёт потом считал бы их «не во владении»). ``cfg`` — для «запретов».
    """
    params: list[int] = []
    if owned:
        loc = ""
        ids = sorted(int(x) for x in (location_ids or []))
        if ids:
            loc = f" WHERE location_id IN ({_in(ids)})"
            params.extend(ids)
        sql = (
            "SELECT DISTINCT p.product_type_id FROM sde_blueprint_products p "
            f"JOIN (SELECT DISTINCT type_id FROM character_blueprints{loc}) cb "
            "  ON cb.type_id = p.blueprint_type_id "
            "WHERE p.activity_id IN (1, 11)"
        )
    else:
        sql = "SELECT DISTINCT p.product_type_id FROM sde_blueprint_products p WHERE p.activity_id IN (1, 11)"
    if group_ids:
        sql += (
            f" AND p.product_type_id IN "
            f"(SELECT type_id FROM sde_types WHERE group_id IN ({_in(group_ids)}))"
        )
        params.extend(int(g) for g in group_ids)
    ex_sql, ex_params = exclusion_clause(cfg)
    sql += ex_sql
    params.extend(ex_params)
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [int(r["product_type_id"]) for r in conn.execute(sql, params)]


@dataclass
class BuyDeal:
    """Предмет, который выгоднее КУПИТЬ, чем строить (рынок дешевле себестоимости)."""

    product_type_id: int
    name: str
    build_unit: float       # себестоимость постройки за 1 ед
    buy_unit: float         # landed-цена покупки до GPLB-C за 1 ед
    buy_hub: str            # 'jita' | 'cj'
    savings_unit: float     # build_unit − buy_unit (сколько экономишь на штуке покупкой)
    savings_pct: float      # savings_unit / build_unit
    daily_volume: float     # ликвидность в хабе покупки


def _traded_buildable(conn, sell_regions, owned, limit, exclude_categories=None, group_ids=None,
                      location_ids=None, cfg: Config | None = None) -> list[int]:
    """type_id производимых предметов, у которых ЕСТЬ sell-цена в нужных регионах (дешёвый отсев).

    Без этого пришлось бы считать себестоимость для тысяч неторгуемых предметов.
    ``exclude_categories`` — категории SDE, которые выкидываем (по умолч. 25 = Asteroid: руда/лёд —
    компрессия, это не «постройка» в индустриальном смысле, а шум в выдаче).
    ``location_ids``/``cfg`` — как у ``candidate_products`` (локации чертежей, «запреты»).
    """
    regions = [r for r in sell_regions if r]
    if not regions:
        return []
    rph = ",".join("?" for _ in regions)
    own_params: list[int] = []
    if owned:
        ids = sorted(int(x) for x in (location_ids or []))
        loc = f" WHERE location_id IN ({_in(ids)})" if ids else ""
        own_params = ids
        own_join = (f"JOIN (SELECT DISTINCT type_id FROM character_blueprints{loc}) cb "
                    "  ON cb.type_id = p.blueprint_type_id ")
    else:
        own_join = ""
    cat_clause, cat_params = "", []
    cats = [int(c) for c in (exclude_categories or [])]
    if cats:
        cph = ",".join("?" for _ in cats)
        cat_clause = f" AND (g.category_id IS NULL OR g.category_id NOT IN ({cph}))"
        cat_params = cats
    grp_clause, grp_params = "", []
    gids = [int(x) for x in (group_ids or [])]
    if gids:
        gph = ",".join("?" for _ in gids)
        grp_clause = f" AND t.group_id IN ({gph})"
        grp_params = gids
    sql = (
        "SELECT DISTINCT p.product_type_id FROM sde_blueprint_products p "
        + own_join +
        f"JOIN market_snapshot m ON m.type_id = p.product_type_id "
        f"  AND m.region_id IN ({rph}) AND m.sell_min IS NOT NULL AND m.sell_min > 0 "
        "LEFT JOIN sde_types t ON t.type_id = p.product_type_id "
        "LEFT JOIN sde_groups g ON g.group_id = t.group_id "
        f"WHERE p.activity_id IN (1, 11){cat_clause}{grp_clause}"
    )
    ex_sql, ex_params = exclusion_clause(cfg)
    sql += ex_sql
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [int(r["product_type_id"])
            for r in conn.execute(sql, own_params + regions + cat_params + grp_params + ex_params)]


def buy_cheaper(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    owned: bool = False,
    min_volume: float | None = None,
    top: int = 60,
    limit_candidates: int | None = None,
    max_savings_pct: float | None = None,
    group_ids: list[int] | None = None,
) -> list[BuyDeal]:
    """Найти предметы, которые рынок продаёт дешевле твоей себестоимости постройки.

    Сравнивается landed-цена покупки до GPLB-C (как дешевле: Jita+доставка vs C-J6MT) с
    себестоимостью постройки (рекурсивный make-or-buy, core). Возвращает где buy < build,
    по убыванию % экономии. Скан только по торгуемым производимым предметам (дешёвый предфильтр).

    ``max_savings_pct`` отсекает артефакты SDE (формулы компрессии руды и т.п. с абсурдно
    завышенной себестоимостью — «экономия» под 100%): реальные находки — это умеренные проценты.
    """
    rc = cfg.recommend
    min_volume = min_volume if min_volume is not None else rc.min_daily_volume
    if max_savings_pct is None:
        max_savings_pct = rc.buy_cheaper_max_savings_pct
    params = core.build_params_from_config(cfg, conn)
    liq = Liquidity(conn, cfg, params.jita_region_id)

    deals: list[BuyDeal] = []
    for type_id in _traded_buildable(
        conn, (params.cj6mt_region_id, params.jita_region_id), owned, limit_candidates,
        rc.buy_cheaper_exclude_categories, group_ids,
        location_ids=params.blueprint_location_ids, cfg=cfg,
    ):
        ch = core.cost.choose_hub(conn, type_id, params)        # дешёвый хаб покупки (landed)
        if ch is None:
            continue
        buy_unit = ch.landed_gplb
        region = params.jita_region_id if ch.hub == "jita" else params.cj6mt_region_id
        vol = liq.daily(type_id, region)
        if min_volume and vol < min_volume:
            continue
        est = core.estimate_build(conn, cfg, type_id, runs=1, streams=1, params=params)  # дорогая часть — после отсева
        node = est.node
        if node.activity_id == 0 or node.produced == 0 or node.unit_cost <= 0:
            continue
        build_unit = node.unit_cost
        # Битые чертежи SDE: себестоимость абсурдно мала или велика относительно цены — артефакт данных.
        if build_unit < rc.min_cost_ratio * buy_unit:
            continue
        savings_pct = (build_unit - buy_unit) / build_unit
        if buy_unit < build_unit and savings_pct <= max_savings_pct:
            deals.append(BuyDeal(
                product_type_id=type_id, name=prices.type_name(conn, type_id),
                build_unit=build_unit, buy_unit=buy_unit, buy_hub=ch.hub,
                savings_unit=build_unit - buy_unit, savings_pct=savings_pct,
                daily_volume=vol,
            ))
    deals.sort(key=lambda d: d.savings_pct, reverse=True)
    return deals[:top]


def _max_skill(conn: sqlite3.Connection, skill_name: str) -> int:
    from ..planner.slots import _skill_id

    sid = _skill_id(conn, skill_name)
    if sid is None:
        return 0
    row = conn.execute(
        "SELECT MAX(active_level) AS m FROM character_skills WHERE skill_type_id = ?", (sid,)
    ).fetchone()
    return int(row["m"]) if row and row["m"] is not None else 0


def daily_volume(conn: sqlite3.Connection, type_id: int, region_id: int) -> float:
    """Простая оценка «объём/сут» — источник ``liquidity_source = "sell_region"`` (по умолчанию).

    Среднее по последним 30 ЗАПИСЯМ истории (не календарным дням: дней без сделок в истории ESI
    нет — у неликвида среднее завышено); нет истории — ``sell_volume`` снапшота, то есть
    ВЫСТАВЛЕННОЕ на продажу, а не проданное. Оценка грубая; честный оборот —
    ``history_daily_volume``/``Liquidity``."""
    row = conn.execute(
        "SELECT AVG(volume) AS v, COUNT(*) AS n FROM "
        "(SELECT volume FROM market_history WHERE type_id = ? AND region_id = ? "
        " ORDER BY day DESC LIMIT 30)",
        (type_id, region_id),
    ).fetchone()
    if row and row["n"]:
        return float(row["v"] or 0.0)
    r2 = conn.execute(
        "SELECT sell_volume FROM market_snapshot WHERE type_id = ? AND region_id = ?",
        (type_id, region_id),
    ).fetchone()
    return float(r2["sell_volume"]) if r2 and r2["sell_volume"] else 0.0


LIQUIDITY_SOURCES = ("sell_region", "sell_region_history", "jita")


def history_anchor(conn: sqlite3.Connection, region_id: int) -> str | None:
    """Последний день истории рынка региона (``YYYY-MM-DD``) — конец окна ликвидности. Берём по
    РЕГИОНУ, а не по типу: иначе неликвид, последний раз торговавшийся месяц назад, получил бы
    окно «до своей последней сделки» и выглядел бы живым. None — истории региона нет вообще."""
    row = conn.execute("SELECT MAX(day) AS d FROM market_history WHERE region_id = ?", (region_id,)).fetchone()
    return str(row["d"]) if row and row["d"] else None


def history_daily_volume(
    conn: sqlite3.Connection, type_id: int, region_id: int, days: int, anchor: str | None
) -> float:
    """Реальный оборот, шт./сут: Σ volume истории ESI за ``days`` КАЛЕНДАРНЫХ дней до ``anchor``
    включительно / ``days`` — дни без сделок (их в истории нет) считаются нулём. Нет ``anchor``
    (история региона не синкалась) или сделок в окне — 0."""
    if not anchor or days <= 0:
        return 0.0
    start = (date.fromisoformat(anchor) - timedelta(days=days - 1)).isoformat()
    row = conn.execute(
        "SELECT SUM(volume) AS v FROM market_history "
        "WHERE type_id = ? AND region_id = ? AND day BETWEEN ? AND ?",
        (type_id, region_id, start, anchor),
    ).fetchone()
    return float(row["v"] or 0.0) / days if row else 0.0


class Liquidity:
    """«Объём/сут» по настройке ``[recommend] liquidity_source`` / ``liquidity_days`` — один
    объект на расчёт (кэширует последний день истории по регионам).

    - ``sell_region`` — ``daily_volume`` по региону рынка (по умолчанию);
    - ``sell_region_history`` — ``history_daily_volume`` по региону рынка (для C-J6MT история
      синкается отдельно, см. ``sync.orchestrator.sync_market``);
    - ``jita`` — ``history_daily_volume`` по Jita, какой бы рынок ни спросили (прокси).
    «Регион рынка» — место сбыта для ТОП/«из остатков», хаб покупки для «дешевле купить»."""

    def __init__(self, conn: sqlite3.Connection, cfg: Config, jita_region_id: int):
        self.conn = conn
        rc = cfg.recommend
        self.source = rc.liquidity_source if rc.liquidity_source in LIQUIDITY_SOURCES else "sell_region"
        self.days = max(1, int(rc.liquidity_days or 30))
        self.jita_region_id = jita_region_id
        self._anchor: dict[int, str | None] = {}

    def anchor(self, region_id: int) -> str | None:
        if region_id not in self._anchor:
            self._anchor[region_id] = history_anchor(self.conn, region_id)
        return self._anchor[region_id]

    def daily(self, type_id: int, region_id: int) -> float:
        if self.source == "sell_region":
            return daily_volume(self.conn, type_id, region_id)
        region = self.jita_region_id if self.source == "jita" else region_id
        return history_daily_volume(self.conn, type_id, region, self.days, self.anchor(region))


def _winsorize(values: list[float], pct: float) -> list[float]:
    """Подрезать крайние значения к перцентилям [pct, 1-pct], чтобы выбросы не ломали шкалу."""
    if not values or pct <= 0:
        return values
    s = sorted(values)
    n = len(s) - 1
    lo, hi = s[int(pct * n)], s[int((1 - pct) * n)]
    return [min(max(v, lo), hi) for v in values]


def _normalize(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def recommend(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    owned: bool = True,
    runs: int | None = None,
    budget: float | None = None,
    min_volume: float | None = None,
    top: int = 20,
    limit_candidates: int | None = None,
    group_ids: list[int] | None = None,
) -> list[Recommendation]:
    """Отранжировать кандидатов. Возвращает top рекомендаций по убыванию score."""
    rc = cfg.recommend
    runs = runs if runs is not None else rc.runs
    budget = budget if budget is not None else (rc.max_capital or None)
    min_volume = min_volume if min_volume is not None else rc.min_daily_volume

    params = core.build_params_from_config(cfg, conn)
    sell_region = params.cj6mt_region_id or params.jita_region_id
    max_ind = _max_skill(conn, "Industry")
    max_adv = _max_skill(conn, "Advanced Industry")
    liq = Liquidity(conn, cfg, params.jita_region_id)

    recs: list[Recommendation] = []
    for type_id in candidate_products(conn, owned=owned, limit=limit_candidates, group_ids=group_ids,
                                      location_ids=params.blueprint_location_ids, cfg=cfg):
        est = core.estimate_build(conn, cfg, type_id, runs=runs, streams=1, params=params)
        node = est.node
        pr = est.profit
        if node.activity_id == 0 or node.total_cost <= 0 or pr.profit is None or node.produced == 0:
            continue
        # Битые чертежи SDE (напр. Praxis = «1× Tritanium»): себестоимость неправдоподобно мала
        # относительно рыночной цены предмета — это артефакт данных, не реальная стройка.
        if pr.sell_unit_price and node.unit_cost < rc.min_cost_ratio * pr.sell_unit_price:
            continue

        base = timing.base_time_per_run(conn, node.blueprint_type_id, node.activity_id)
        te = max(
            (timing.best_owned_te(conn, int(r["character_id"]), node.blueprint_type_id)
             for r in conn.execute("SELECT character_id FROM characters")),
            default=0,
        )
        time_mult = core.cost.facility_mults(
            params, node.activity_id, prices.group_id(conn, type_id), prices.category_id(conn, type_id)
        ).time_mult
        total_time = timing.job_seconds(base, node.runs, node.activity_id, te, max_ind, max_adv, time_mult)
        unit_time = total_time / node.produced if node.produced else 0.0
        unit_profit = pr.profit / node.produced
        isk_hour = unit_profit / (unit_time / 3600.0) if unit_time > 0 else 0.0
        vol = liq.daily(type_id, sell_region)

        if budget and node.total_cost > budget:
            continue
        if min_volume and vol < min_volume:
            continue

        recs.append(
            Recommendation(
                product_type_id=type_id,
                name=prices.type_name(conn, type_id),
                runs=runs,
                capital=node.total_cost,
                unit_cost=node.unit_cost,
                unit_profit=unit_profit,
                roi=pr.roi or 0.0,
                isk_per_hour=isk_hour,
                daily_volume=vol,
            )
        )

    if not recs:
        return []

    n_roi = _normalize(_winsorize([r.roi for r in recs], rc.winsor_pct))
    n_isk = _normalize(_winsorize([r.isk_per_hour for r in recs], rc.winsor_pct))
    n_liq = _normalize(_winsorize([r.daily_volume for r in recs], rc.winsor_pct))
    for r, a, b, c in zip(recs, n_roi, n_isk, n_liq, strict=True):
        r.score = rc.w_roi * a + rc.w_isk_hour * b + rc.w_liquidity * c

    recs.sort(key=lambda x: x.score, reverse=True)
    return recs[:top]
