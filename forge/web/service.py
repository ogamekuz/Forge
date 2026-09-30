"""Сервисный слой Forge — всё, что умеет интерфейс, без привязки к UI-фреймворку.

Общий для десктопного пульта (``forge.desktop``, PySide6) и локального HTTP-сервера отчётов
(``forge.web.app``, FastAPI — тонкая обёртка над этим модулем). Каждый метод открывает
СВОЁ соединение с БД и закрывает его сам — безопасно вызывать из рабочих потоков пульта
(``run_bg``) и из обработчиков FastAPI одновременно (SQLite в WAL пускает читателей параллельно).

Сети не делает (правило 1 docs/ARCHITECTURE.md): читает локальную БД; синк запускается через
``sync.runner`` (там сеть разрешена). Результаты — JSON-совместимые dict/list, те же, что
отдаёт ``/api/*`` (HTML-отчёты и тесты на них опираются).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config as config_mod
from .. import core, storage
from .. import planner as planner_mod
from .. import recommend as recommend_mod
from ..core import prices
from ..i18n import N_, plural, tr
from ..ingest.character import tokens as sso_tokens
from ..planner import slots as planner_slots
from ..storage import repositories as repo
from ..storage import sync_state
from ..sync import runner
from . import report as report_mod
from . import serializers


class ServiceError(Exception):
    """Ошибка запроса с HTTP-подобным кодом (404 — не найдено, 400 — плохой запрос, 409 —
    занято). FastAPI превращает её в HTTPException, пульт — в строку статуса."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def align(values: Sequence[int], n: int, default: int) -> list[int]:
    """Выровнять список чисел под ``n`` предметов корзины: одно значение распространяется на
    все, недостающие добиваются ``default``."""
    vals = [int(v) for v in values]
    if len(vals) == 1:
        vals = vals * n
    return [vals[i] if i < len(vals) else default for i in range(n)]


def align_optional(values: Sequence[int | None], n: int) -> list[int | None]:
    """Как ``align``, но БЕЗ распространения одиночного значения: оверрайд ME/TE должен быть
    явным по каждому предмету; недостающие позиции — ``None`` (нет оверрайда)."""
    vals = list(values)
    return [vals[i] if i < len(vals) else None for i in range(n)]


def parse_csv_ints(raw: str) -> list[int]:
    """«1, 2,3» → [1, 2, 3]; пустые позиции пропускаются."""
    return [int(v) for v in (s.strip() for s in (raw or "").split(",")) if v]


def parse_csv_optional_ints(raw: str) -> list[int | None]:
    """«3,,5» → [3, None, 5]; пустая строка → []."""
    if not raw:
        return []
    return [int(p) if p.strip() else None for p in raw.split(",")]


def parse_csv(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


@dataclass
class Basket:
    """Корзина для расчётов Калькулятора/Расписания/Отчёта.

    ``types`` — type_id или точные имена; ``runs``/``streams`` выровнены по ``types`` (см.
    ``align``); ``me_override``/``te_override`` — «Точный ME/TE» по товару (``None`` — нет).
    ``buy`` — товары, которые игрок решил купить целиком (готовыми); ``buy_components`` —
    под-компоненты, переключённые «строить→купить» в дереве калькулятора.
    """

    types: list[str | int]
    runs: list[int] = field(default_factory=lambda: [1])
    streams: list[int] = field(default_factory=lambda: [1])
    me: int = 0
    te: int = 0
    build: bool = True
    max_stream_days: float | None = None
    consolidate: bool = True
    auto_streams: bool = False
    buy: list[str | int] = field(default_factory=list)
    buy_components: list[int] = field(default_factory=list)
    me_override: list[int | None] = field(default_factory=list)
    te_override: list[int | None] = field(default_factory=list)


@dataclass
class _Resolved:
    tids: list[int]
    runs: list[int]
    streams: list[int]
    force_buy_ids: set[int]
    force_buy_extra: frozenset[int]
    me_overrides: dict[int, int] | None
    te_overrides: dict[int, int] | None


# Готовые точки сравнения для compare_basket по умолчанию: (подпись, consolidate,
# auto_streams, max_stream_days). Сроки «Макс. N дней/поток» берутся из конфига
# ([planner] compare_max_days) — см. ForgeService._compare_candidates.
_BASE_COMPARE = [
    (N_("Без консолидации"), False, False, None),
    (N_("Консолидация, 1 поток"), True, False, None),
    (N_("Консолидация, авто-потоки"), True, True, None),
]

STATUS_TABLES = [
    "sde_types", "sde_blueprints", "market_snapshot", "market_adjusted_prices",
    "system_cost_indices", "characters", "character_blueprints",
]


def _days_label(days: float) -> str:
    """0.5 → «Макс. 12 часов/поток», 3 → «Макс. 3 дня/поток», 1.5 → «Макс. 1.5 дня/поток»
    (на текущем языке пульта)."""
    if days < 1:
        hours = max(1, round(days * 24))
        return tr("Макс. {n} {unit}/поток", n=hours, unit=plural(hours, "час", "часа", "часов"))
    if not float(days).is_integer():
        return tr("Макс. {n} дня/поток", n=f"{days:g}")
    n = int(days)
    return tr("Макс. {n} {unit}/поток", n=n, unit=plural(n, "день", "дня", "дней"))


class ForgeService:
    """Фасад над core/planner/recommend/report для интерфейсов.

    ``config_path`` — путь к forge.toml; БД (``db_path``) — относительно файла конфига,
    отчёты — в ``reports/`` рядом с ним.
    """

    def __init__(self, config_path: str | Path = config_mod.DEFAULT_CONFIG_PATH):
        self.config_path = str(config_path)
        self.cfg_dir = Path(self.config_path).resolve().parent
        self.reports_dir = self.cfg_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_mod.write_index(self.reports_dir)  # страница-список /reports/ доступна сразу

    # ------------------------------------------------------------------ база
    def load_cfg(self) -> config_mod.Config:
        try:
            return config_mod.load(self.config_path)
        except FileNotFoundError:
            return config_mod.Config()

    def db_path(self, cfg: config_mod.Config | None = None) -> Path:
        cfg = cfg or self.load_cfg()
        db = Path(cfg.db_path)
        return db if db.is_absolute() else self.cfg_dir / db  # относительно конфига, не cwd

    def open_conn(self, cfg: config_mod.Config) -> sqlite3.Connection:
        conn = storage.connect(str(self.db_path(cfg)))
        conn.execute("PRAGMA busy_timeout = 5000")  # фоновый синк может держать запись
        storage.init_db(conn)
        return conn

    def ctx(self) -> tuple[config_mod.Config, sqlite3.Connection]:
        cfg = self.load_cfg()
        conn = self.open_conn(cfg)
        config_mod.resolve_locations(cfg, conn)
        return cfg, conn

    @staticmethod
    def resolve_type(conn: sqlite3.Connection, product: str | int) -> int:
        s = str(product).strip()
        tid = int(s) if s.isdigit() else prices.resolve_type_id(conn, s)
        if tid is None:
            raise ServiceError(404, tr("Не найден предмет: {product}", product=product))
        return tid

    def _resolve_basket(self, conn: sqlite3.Connection, b: Basket) -> _Resolved:
        n = len(b.types)
        tids = [self.resolve_type(conn, t) for t in b.types]
        me_list = align_optional(b.me_override, n)
        te_list = align_optional(b.te_override, n)
        me_overrides = {t: v for t, v in zip(tids, me_list, strict=True) if v is not None} or None
        te_overrides = {t: v for t, v in zip(tids, te_list, strict=True) if v is not None} or None
        return _Resolved(
            tids=tids,
            runs=align(b.runs, n, 1),
            streams=align(b.streams, n, 1),
            force_buy_ids={self.resolve_type(conn, t) for t in b.buy if str(t).strip()},
            force_buy_extra=frozenset(int(t) for t in b.buy_components),
            me_overrides=me_overrides,
            te_overrides=te_overrides,
        )

    # --------------------------------------------------------------- статус
    def status(self) -> dict:
        _cfg, conn = self.ctx()
        try:
            sync_state.reap_stale(conn, max_age_minutes=30)  # самовосстановление зависшего 'running'
            states = [dict(r) for r in sync_state.all_states(conn)]
            counts = {t: repo.count(conn, t) for t in STATUS_TABLES}
            # Персонажи, чей сохранённый вход EVE SSO не принял на последнем синке.
            relogin = [r["name"] for r in conn.execute(
                "SELECT name FROM characters WHERE refresh_token = ? ORDER BY name",
                (sso_tokens.MARKER_RELOGIN,))]
        finally:
            conn.close()
        return {"sources": states, "counts": counts, "runner": runner.snapshot(), "relogin": relogin}

    def trigger_sync(self, source: str) -> dict:
        if source not in runner.VALID_SOURCES:
            raise ServiceError(404, tr("Неизвестный источник: {source}", source=source))
        res = runner.trigger(source, self.config_path)
        if res.get("busy"):
            raise ServiceError(409, tr("Синк уже идёт — дождись завершения."))
        return res

    def trigger_auth(self) -> dict:
        res = runner.trigger_auth(self.config_path)
        if res.get("busy"):
            raise ServiceError(409, tr("Вход уже выполняется."))
        return res

    # ------------------------------------------------------------ персонажи
    def characters(self) -> list[dict]:
        _cfg, conn = self.ctx()
        try:
            out = []
            for r in conn.execute("SELECT * FROM characters ORDER BY name"):
                cid = int(r["character_id"])
                sp = planner_slots.slots_for(conn, cid, r["name"])
                bp = conn.execute(
                    "SELECT COUNT(*) AS n FROM character_blueprints WHERE character_id = ?", (cid,)
                ).fetchone()["n"]
                jobs = conn.execute(
                    "SELECT COUNT(*) AS n FROM character_industry_jobs WHERE character_id = ?", (cid,)
                ).fetchone()["n"]
                active = conn.execute(
                    "SELECT COUNT(*) AS n FROM character_industry_jobs "
                    "WHERE character_id = ? AND status = 'active'", (cid,)
                ).fetchone()["n"]
                assets = conn.execute(
                    "SELECT COUNT(*) AS n FROM character_assets WHERE character_id = ?", (cid,)
                ).fetchone()["n"]
                out.append({
                    "character_id": cid, "name": r["name"],
                    "wallet_balance": r["wallet_balance"],
                    "updated_at": r["updated_at"],
                    "relogin": r["refresh_token"] == sso_tokens.MARKER_RELOGIN,
                    "slots": {"manufacturing": sp.manufacturing, "reaction": sp.reaction,
                              "science": sp.science},
                    "blueprints": bp, "jobs": jobs, "active_jobs": active, "assets": assets,
                })
        finally:
            conn.close()
        return out

    # ---------------------------------------------------------------- поиск
    def search(self, q: str, limit: int = 20) -> list[dict]:
        _cfg, conn = self.ctx()
        try:
            rows = conn.execute(
                "SELECT t.type_id, t.name, "
                "EXISTS(SELECT 1 FROM sde_blueprint_products p WHERE p.product_type_id = t.type_id) AS buildable "
                "FROM sde_types t WHERE t.name LIKE ? AND t.is_published = 1 "
                "ORDER BY LENGTH(t.name) LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        finally:
            conn.close()
        return [{"type_id": r["type_id"], "name": r["name"], "buildable": bool(r["buildable"])}
                for r in rows]

    def resolve_names(self, names: Iterable[str]) -> list[dict]:
        """Точный (без опечаток, без учёта регистра) резолв имён в type_id — для вставки фита
        из буфера обмена EVE. Порядок ответа = порядок входа; ``type_id: None`` — не нашли."""
        names = [n.strip() for n in names if n.strip()]
        if not names:
            return []
        _cfg, conn = self.ctx()
        try:
            ph = ",".join("?" for _ in names)
            rows = conn.execute(
                "SELECT t.type_id, t.name, "
                "EXISTS(SELECT 1 FROM sde_blueprint_products p WHERE p.product_type_id = t.type_id) AS buildable "
                f"FROM sde_types t WHERE t.name COLLATE NOCASE IN ({ph})",
                names,
            ).fetchall()
        finally:
            conn.close()
        by_lower = {r["name"].lower(): {"type_id": r["type_id"], "buildable": bool(r["buildable"])}
                    for r in rows}
        return [{"name": n, **(by_lower.get(n.lower()) or {"type_id": None, "buildable": False})}
                for n in names]

    def groups(self, q: str, limit: int = 30) -> list[dict]:
        _cfg, conn = self.ctx()
        try:
            rows = conn.execute(
                "SELECT g.group_id, g.name, c.name AS category FROM sde_groups g "
                "LEFT JOIN sde_categories c ON c.category_id = g.category_id "
                "WHERE g.name LIKE ? ORDER BY LENGTH(g.name) LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        finally:
            conn.close()
        return [{"group_id": r["group_id"], "name": r["name"], "category": r["category"]}
                for r in rows]

    def categories(self, q: str, limit: int = 30) -> list[dict]:
        _cfg, conn = self.ctx()
        try:
            rows = conn.execute(
                "SELECT category_id, name FROM sde_categories WHERE name LIKE ? "
                "ORDER BY LENGTH(name) LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        finally:
            conn.close()
        return [{"category_id": r["category_id"], "name": r["name"]} for r in rows]

    def rigs(self, q: str, limit: int = 30) -> list[dict]:
        """Риги/сервис-модули с известным material/time-бонусом (для фита станции)."""
        _cfg, conn = self.ctx()
        try:
            rows = conn.execute(
                "SELECT DISTINCT t.type_id, t.name, g.name AS group_name "
                "FROM sde_types t "
                "JOIN sde_dogma_type_attributes da ON da.type_id = t.type_id "
                "LEFT JOIN sde_groups g ON g.group_id = t.group_id "
                "WHERE t.name LIKE ? ORDER BY LENGTH(t.name) LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        finally:
            conn.close()
        return [{"type_id": r["type_id"], "name": r["name"], "group_name": r["group_name"]}
                for r in rows]

    def systems(self, q: str, limit: int = 30) -> list[dict]:
        """Солнечные системы SDE по имени (для локаций/хабов и склада)."""
        _cfg, conn = self.ctx()
        try:
            rows = conn.execute(
                "SELECT system_id, name, region_id, security FROM sde_systems "
                "WHERE name LIKE ? ORDER BY LENGTH(name) LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()
        finally:
            conn.close()
        return [{"system_id": r["system_id"], "name": r["name"], "region_id": r["region_id"],
                 "security": r["security"]} for r in rows]

    def _names(self, sql: str, ids: Iterable[int]) -> dict[str, str]:
        idlist = [int(x) for x in ids]
        if not idlist:
            return {}
        _cfg, conn = self.ctx()
        try:
            ph = ",".join("?" for _ in idlist)
            rows = conn.execute(sql.format(ph=ph), idlist).fetchall()
        finally:
            conn.close()
        return {str(r[0]): r[1] for r in rows}

    def group_names(self, ids: Iterable[int]) -> dict[str, str]:
        return self._names("SELECT group_id, name FROM sde_groups WHERE group_id IN ({ph})", ids)

    def category_names(self, ids: Iterable[int]) -> dict[str, str]:
        return self._names(
            "SELECT category_id, name FROM sde_categories WHERE category_id IN ({ph})", ids)

    def type_names(self, ids: Iterable[int]) -> dict[str, str]:
        return self._names("SELECT type_id, name FROM sde_types WHERE type_id IN ({ph})", ids)

    def system_names(self, ids: Iterable[int]) -> dict[str, str]:
        return self._names("SELECT system_id, name FROM sde_systems WHERE system_id IN ({ph})", ids)

    def decryptors(self) -> list[dict]:
        """8 декрипторов инвенты с модификаторами и текущей landed-ценой до места стройки (None —
        нет цены: такой декриптор недоступен, заданный вручную — фолбэк на авто) — для раздела
        «Инвента» в настройках."""
        cfg, conn = self.ctx()
        try:
            params = core.build_params_from_config(cfg, conn)
            out = []
            for d in core.blueprint.DECRYPTORS:
                out.append({"type_id": d.type_id, "name": d.name,
                            "full_name": prices.type_name(conn, d.type_id),
                            "prob_mult": d.prob_mult, "me_mod": d.me_mod, "te_mod": d.te_mod,
                            "run_mod": d.run_mod,
                            "price": core.cost.landed_unit_cost(conn, d.type_id, params)})
        finally:
            conn.close()
        return out

    def market_structures(self) -> dict:
        """Рынки-структуры для «Настройки → Логистика и рынок»: известные структуры (имя и
        система из ESI ``universe_structures``) — чтобы добавлять их из списка, а не по голому id,
        и система хаба рынка сбыта — для предупреждения «структура не в системе хаба»."""
        cfg, conn = self.ctx()
        try:
            cj = cfg.locations.get("c_j6mt")
            hub_sys = cj.system_id if cj else 0
            hub = conn.execute("SELECT name FROM sde_systems WHERE system_id = ?", (hub_sys,)).fetchone()
            rows = conn.execute(
                "SELECT us.structure_id, us.name, us.solar_system_id, us.status, s.name AS system_name "
                "FROM universe_structures us LEFT JOIN sde_systems s ON s.system_id = us.solar_system_id"
            ).fetchall()
        finally:
            conn.close()
        known = [{"structure_id": int(r["structure_id"]), "name": r["name"],
                  "system_id": r["solar_system_id"], "system_name": r["system_name"],
                  "status": r["status"], "in_hub": bool(hub_sys and r["solar_system_id"] == hub_sys)}
                 for r in rows]
        known.sort(key=lambda k: (k["status"] != "ok", not k["in_hub"], k["name"] or ""))
        return {"hub_system_id": hub_sys, "hub_system_name": hub["name"] if hub else None,
                "known": known, "effective": cfg.structures.market_ids()}

    # ------------------------------------------------------------- чертежи
    def blueprint_locations(self) -> list[dict]:
        """Локации, где лежат чертежи (location_id + количество), с подписью — для выбора
        «Где искать чертежи» в настройках. Подпись — из справочника локаций склада
        (структуры ESI, NPC-станции SDE, контейнеры), см. ``core.locations``."""
        cfg, conn = self.ctx()
        try:
            rows = conn.execute(
                "SELECT location_id, COUNT(*) AS n FROM character_blueprints "
                "WHERE location_id IS NOT NULL GROUP BY location_id ORDER BY n DESC"
            ).fetchall()
            resolver = core.locations.LocationResolver(conn, cfg)
            out = []
            for r in rows:
                loc_id = int(r["location_id"])
                info = resolver.describe(loc_id)
                out.append({"location_id": loc_id, "count": r["n"], "label": info.label,
                            "system_id": info.system_id, "system_name": info.system_name})
        finally:
            conn.close()
        return out

    # ------------------------------------------------------------- расчёты
    def _cost_one(self, conn, cfg, params, tid: int, runs: int, streams: int, me: int, build: bool,
                  substream_fn=None, force_buy: bool = False,
                  force_buy_extra: frozenset = frozenset(), est=None, te: int = 0,
                  me_overrides: dict[int, int] | None = None,
                  te_overrides: dict[int, int] | None = None) -> dict:
        """Расчёт + обогащение хабами одного предмета (общая логика cost и корзины).

        ``streams`` — потоки верхнего продукта (явно); ``substream_fn`` — разбивка под-компонентов
        под срок («Макс. дней/поток»), если задана. ``force_buy`` — игрок решил КУПИТЬ предмет
        целиком (готовым). ``force_buy_extra`` — type_id под-компонентов, которые игрок переключил
        «строить→купить» в дереве калькулятора. ``est`` — уже построенная (и, при необходимости,
        УЖЕ консолидированная) оценка; если не передана, строится здесь заново.
        """
        if est is None:
            est = core.estimate_build(conn, cfg, tid, runs=runs, streams=streams, default_me=me,
                                      default_te=te, allow_build=build, whole_blueprint=True,
                                      substream_fn=substream_fn,
                                      force_buy_extra=force_buy_extra, params=params,
                                      me_overrides=me_overrides, te_overrides=te_overrides)
        out = serializers.estimate_to_dict(est)

        # Цены по хабам для каждого материала и продукта. Для строк передаём quantity —
        # тогда выбор хаба учитывает наличие объёма.
        sys_names: dict[int, str | None] = {}

        def sys_name(system_id) -> str | None:
            """Имя системы для подписи «индекс системы X» (станция может стоять не в системе стройки)."""
            if not system_id:
                return None
            if system_id not in sys_names:
                r = conn.execute("SELECT name FROM sde_systems WHERE system_id = ?", (system_id,)).fetchone()
                sys_names[system_id] = r["name"] if r else str(system_id)
            return sys_names[system_id]

        def enrich(node: dict) -> None:
            node["hub"] = core.cost.hub_unit_prices(conn, node["product_type_id"], params)
            node["cost_system_name"] = sys_name(node.get("cost_system_id"))
            ib = node.get("invention_breakdown")
            if ib:
                ib["job_fee_system_name"] = sys_name(ib.get("job_fee_system_id"))
                ib["t1_copy_system_name"] = sys_name(ib.get("t1_copy_system_id"))
            for ln in node["lines"]:
                ln["hub"] = core.cost.hub_unit_prices(conn, ln["type_id"], params, ln["quantity"])
                if ln.get("child"):
                    enrich(ln["child"])

        enrich(out["node"])

        # Покупаемый предмет — непостроиваемый (мета/дроп) ИЛИ по решению игрока (force_buy).
        # Считаем landed-цену до места стройки и где дешевле, кол-во = runs.
        if force_buy or core.sourcing.is_unbuildable_leaf(est.node):
            qty = max(1, runs)
            out["node"]["hub"] = core.cost.hub_unit_prices(conn, tid, params, qty)
            out["node"]["lines"] = []             # покупка — без дерева постройки
            out["node"]["blueprint_source"] = ""  # чтобы «Подготовка/проблемы» его не флагала
            out["node"]["activity_id"] = 0
            out["node"]["missing_prices"] = []
            choice = core.cost.choose_hub(conn, tid, params, qty)
            landed = choice.landed_gplb if choice else None
            out["node"]["produced"] = qty
            out["node"]["unit_cost"] = landed or 0.0
            out["node"]["material_cost"] = (landed or 0.0) * qty
            out["node"]["total_cost"] = (landed or 0.0) * qty
            out["buy_only"] = True
            out["buy_hub"] = choice.hub if choice else None
            out["buy_shortage"] = bool(choice and not choice.enough)
            out["agg"] = {"materials": out["node"]["total_cost"], "jobs": 0.0, "blueprints": 0.0}
            # Это покупаемый вход, а не продукт на продажу — выручку/прибыль не считаем.
            out["profit"] = {"sell_unit_price": out["profit"]["sell_unit_price"], "revenue": None,
                             "profit": None, "roi": None, "export_freight": 0.0,
                             "export_is_jump": False}
            vol = core.prices.volume(conn, tid) or 0.0
            f_cg = params.freight.get(("c_j6mt", "gplb_c"), 0.0)
            f_jc = params.freight.get(("jita", "c_j6mt"), 0.0)
            rate = f_cg if out["buy_hub"] == "cj" else (f_jc + f_cg)
            out["freight"] = {"inbound": qty * vol * rate, "export_unit": 0.0,
                              "export_total": 0.0, "export_is_jump": False}
            out["buy_unit_price"] = None
            out["build_cheaper"] = False
            out["streams"] = streams
            return out

        # «купить готовым»: рыночная цена самого предмета (C-J6MT → Jita)
        buy = (prices.sell_min(conn, tid, params.cj6mt_region_id)
               or prices.sell_min(conn, tid, params.jita_region_id))
        out["buy_unit_price"] = buy
        out["build_cheaper"] = (buy is None) or (est.node.unit_cost < buy)
        out["streams"] = streams
        # Суммарная разбивка по всему дереву (материалы / джобы / чертежи).
        m_all, j_all, b_all = core.sourcing.aggregate_costs(est.node)
        out["agg"] = {"materials": m_all, "jobs": j_all, "blueprints": b_all}
        # Логистика: входящий фрахт (зашит в материалы) + вывоз на весь заказ (из выручки).
        freight_in = core.sourcing.inbound_freight(conn, est.node, params)
        out["freight"] = {
            "inbound": freight_in,
            "export_unit": est.profit.export_freight,
            "export_total": est.profit.export_freight * est.node.produced,
            "export_is_jump": est.profit.export_is_jump,
        }
        return out

    def _profit(self, conn, cfg, params, node) -> core.profit.ProfitResult:
        sell_region = params.cj6mt_region_id or params.jita_region_id
        return core.profit.compute_profit(
            conn, node, params, sell_region,
            broker_fee=cfg.industry.broker_fee, sales_tax=cfg.industry.sales_tax,
        )

    def cost(self, type_: str | int, runs: int = 1, streams: int = 1, me: int = 0, te: int = 0,
             build: bool = True, max_stream_days: float | None = None, consolidate: bool = False,
             auto_streams: bool = False, me_override: int | None = None,
             te_override: int | None = None) -> dict:
        cfg, conn = self.ctx()
        try:
            tid = self.resolve_type(conn, type_)
            substream_fn = planner_mod.deadline_substreams(conn, cfg, max_stream_days)
            me_overrides = {tid: me_override} if me_override is not None else None
            te_overrides = {tid: te_override} if te_override is not None else None
            # estimate_basket — двухпроходный батч-расчёт фрахта (даже одно дерево может иметь
            # десяток разных материалов: считать рейс на каждый по отдельности абсурдно
            # завышает фрахт, см. core.batched_freight_params).
            _, params = core.estimate_basket(conn, cfg, [(tid, runs, streams)], default_me=me,
                                             default_te=te, allow_build=build, whole_blueprint=True,
                                             substream_fn=substream_fn,
                                             me_overrides=me_overrides, te_overrides=te_overrides)
            est = None
            if consolidate:
                # Даже ОДИН предмет может делить общий под-компонент между СВОИМИ ветками.
                est = core.estimate_build(conn, cfg, tid, runs=runs, streams=streams, default_me=me,
                                          default_te=te, allow_build=build, whole_blueprint=True,
                                          substream_fn=substream_fn, params=params,
                                          me_overrides=me_overrides, te_overrides=te_overrides)
                core.sourcing.consolidate_shared_components(
                    conn, [est.node], params, default_me=me, default_te=te, allow_build=build,
                    substream_fn=substream_fn, auto_streams=auto_streams,
                    me_overrides=me_overrides, te_overrides=te_overrides,
                )
                est.profit = self._profit(conn, cfg, params, est.node)
            return self._cost_one(conn, cfg, params, tid, runs, streams, me, build,
                                  substream_fn=substream_fn, est=est, te=te,
                                  me_overrides=me_overrides, te_overrides=te_overrides)
        finally:
            conn.close()

    def cost_basket(self, b: Basket) -> dict:
        """Суммарный расчёт корзины (Калькулятор). См. ``Basket``: ``max_stream_days`` дробит
        под-компоненты под срок (меняет ME-округление по джобам ⇒ итог материалов);
        ``consolidate``/``auto_streams`` — объединять общий под-компонент между товарами."""
        cfg, conn = self.ctx()
        try:
            substream_fn = planner_mod.deadline_substreams(conn, cfg, b.max_stream_days)
            r = self._resolve_basket(conn, b)
            me, te, build = b.me, b.te, b.build
            # Батч-расчёт фрахта по ВСЕЙ корзине разом (кроме force_buy — те не строятся деревом).
            build_products = [(tid, rr, s) for tid, rr, s in zip(r.tids, r.runs, r.streams, strict=True)
                              if tid not in r.force_buy_ids]
            if build_products:
                _, params = core.estimate_basket(conn, cfg, build_products, default_me=me,
                                                 default_te=te, allow_build=build,
                                                 whole_blueprint=True, substream_fn=substream_fn,
                                                 force_buy_extra=r.force_buy_extra,
                                                 me_overrides=r.me_overrides,
                                                 te_overrides=r.te_overrides)
            else:
                params = core.build_params_from_config(cfg, conn)
            # Все покупаемые деревья строим ОТДЕЛЬНО — чтобы при консолидации слить общий
            # под-компонент МЕЖДУ товарами до сериализации.
            ests: dict[int, core.BuildEstimate] = {}
            for tid, rr, s in build_products:
                ests[tid] = core.estimate_build(conn, cfg, tid, runs=rr, streams=s, default_me=me,
                                                default_te=te, allow_build=build,
                                                whole_blueprint=True, substream_fn=substream_fn,
                                                force_buy_extra=r.force_buy_extra, params=params,
                                                me_overrides=r.me_overrides,
                                                te_overrides=r.te_overrides)
            if b.consolidate and ests:
                core.sourcing.consolidate_shared_components(
                    conn, [e.node for e in ests.values()], params, default_me=me, default_te=te,
                    allow_build=build, substream_fn=substream_fn,
                    force_buy_extra=r.force_buy_extra, auto_streams=b.auto_streams,
                    me_overrides=r.me_overrides, te_overrides=r.te_overrides,
                )
                for e in ests.values():
                    e.profit = self._profit(conn, cfg, params, e.node)
            items: list[dict] = []
            totals: dict[str, Any] = {"material_cost": 0.0, "job_cost": 0.0, "blueprint_cost": 0.0,
                                      "total_cost": 0.0, "revenue": 0.0, "profit": 0.0}
            for tid, rr, s in zip(r.tids, r.runs, r.streams, strict=True):
                one = self._cost_one(conn, cfg, params, tid, rr, s, me, build,
                                     substream_fn=substream_fn, force_buy=tid in r.force_buy_ids,
                                     force_buy_extra=r.force_buy_extra, est=ests.get(tid), te=te,
                                     me_overrides=r.me_overrides, te_overrides=r.te_overrides)
                node, prof, agg = one["node"], one["profit"], one["agg"]
                totals["material_cost"] += agg["materials"]
                totals["job_cost"] += agg["jobs"]
                totals["blueprint_cost"] += agg["blueprints"]
                totals["total_cost"] += node["total_cost"]
                totals["revenue"] += prof["revenue"] or 0.0
                totals["profit"] += prof["profit"] or 0.0
                items.append(one)
            totals["roi"] = totals["profit"] / totals["total_cost"] if totals["total_cost"] else None
            # «Останется от переработки»: физическая побочка со ВСЕХ узлов переработки корзины —
            # с реалистичной ценой продажи (cost.unit_sell_value).
            byproducts = core.sourcing.collect_reprocess_byproducts([e.node for e in ests.values()])
            sell_region = params.cj6mt_region_id or params.jita_region_id
            leftovers: list[dict[str, Any]] = []
            for mid, (mname, qty) in byproducts.items():
                unit = core.cost.unit_sell_value(
                    conn, mid, params, sell_region, cfg.industry.broker_fee, cfg.industry.sales_tax
                )
                leftovers.append({
                    "type_id": mid, "name": mname, "quantity": qty,
                    "sell_unit": unit, "sell_total": (unit * qty) if unit is not None else None,
                })
            leftovers.sort(key=lambda x: -(x["sell_total"] or 0))
            return {"items": items, "totals": totals, "reprocess_leftovers": leftovers}
        finally:
            conn.close()

    def plan(self, type_: str | int, runs: int = 1, streams: int = 1, me: int = 0, te: int = 0,
             build: bool = True, max_stream_days: float | None = None) -> dict:
        cfg, conn = self.ctx()
        try:
            tid = self.resolve_type(conn, type_)
            p = planner_mod.plan_build(conn, cfg, tid, runs=runs, streams=streams, default_me=me,
                                       default_te=te, allow_build=build,
                                       max_stream_days=max_stream_days)
            out = serializers.plan_to_dict(p)
            out["streams"] = p.estimate.node.streams
            return out
        finally:
            conn.close()

    def plan_basket(self, b: Basket) -> dict:
        """Один общий график (Gantt) для корзины; купленные целиком (``buy``) не строятся."""
        cfg, conn = self.ctx()
        try:
            r = self._resolve_basket(conn, b)
            products = [(tid, rr, s) for tid, rr, s in zip(r.tids, r.runs, r.streams, strict=True)
                        if tid not in r.force_buy_ids]
            bp = planner_mod.plan_basket(
                conn, cfg, products, default_me=b.me, default_te=b.te, allow_build=b.build,
                max_stream_days=b.max_stream_days, consolidate=b.consolidate,
                auto_streams=b.auto_streams, force_buy_extra=r.force_buy_extra,
                me_overrides=r.me_overrides, te_overrides=r.te_overrides,
            )
            return {"schedule": serializers.schedule_to_dict(bp.schedule)}
        finally:
            conn.close()

    def _compare_candidates(self, cfg: config_mod.Config) -> list[tuple[str, bool, bool, float | None]]:
        days = [d for d in cfg.planner.compare_max_days if d and d > 0]
        base = [(tr(label), c, a, d) for label, c, a, d in _BASE_COMPARE]
        return base + [(_days_label(d), True, False, float(d)) for d in days]

    def compare_basket(self, b: Basket) -> dict:
        """Сравнить срок/себестоимость корзины при нескольких готовых настройках консолидации
        и «Макс. дней/поток» (набор сроков — из [planner] compare_max_days конфига)."""
        if not b.types:
            raise ServiceError(400, tr("Пустая корзина"))
        cfg, conn = self.ctx()
        try:
            r = self._resolve_basket(conn, b)
            products = [(tid, rr, s) for tid, rr, s in zip(r.tids, r.runs, r.streams, strict=True)
                        if tid not in r.force_buy_ids]
            if not products:
                raise ServiceError(400, tr("Нечего сравнивать (всё в корзине помечено «купить»)"))
            rows = []
            for label, consolidate, auto_streams, max_stream_days in self._compare_candidates(cfg):
                bp = planner_mod.plan_basket(
                    conn, cfg, products, default_me=b.me, default_te=b.te, allow_build=b.build,
                    max_stream_days=max_stream_days, consolidate=consolidate,
                    auto_streams=auto_streams, force_buy_extra=r.force_buy_extra,
                    me_overrides=r.me_overrides, te_overrides=r.te_overrides,
                )
                rows.append({
                    "label": label, "consolidate": consolidate, "auto_streams": auto_streams,
                    "max_stream_days": max_stream_days,
                    "total_cost": sum(e.node.total_cost for e in bp.estimates),
                    "makespan": bp.schedule.makespan,
                    "jobs": len(bp.schedule.items),
                })
            return {"rows": rows}
        finally:
            conn.close()

    def compare_me(self, type_: str | int, runs: int = 1, streams: int = 1, me: int = 0,
                   te: int = 0, build: bool = True, max_stream_days: float | None = None,
                   consolidate: bool = False, auto_streams: bool = False) -> dict:
        """Себестоимость ОДНОГО товара при ME его СОБСТВЕННОГО чертежа 0..10 (остальное дерево
        резолвится как обычно), с теми же настройками, что в Калькуляторе. Для чертежей,
        добываемых только инвентой, ME задаёт декриптор — ``reason: "invention"``, строк нет."""
        cfg, conn = self.ctx()
        try:
            tid = self.resolve_type(conn, type_)
            substream_fn = planner_mod.deadline_substreams(conn, cfg, max_stream_days)
            probe = core.estimate_build(conn, cfg, tid, runs=runs, streams=streams, default_me=me,
                                        default_te=te, allow_build=build, whole_blueprint=True,
                                        substream_fn=substream_fn)
            if probe.node.blueprint_source == "invention":
                return {"rows": [], "reason": "invention"}
            rows = []
            for me_level in range(11):
                me_overrides = {tid: me_level}
                # батч-фрахт пересчитывается на КАЖДЫЙ me_level: объём материалов меняется
                _, params = core.estimate_basket(conn, cfg, [(tid, runs, streams)], default_me=me,
                                                 default_te=te, allow_build=build,
                                                 whole_blueprint=True, substream_fn=substream_fn,
                                                 me_overrides=me_overrides)
                est = core.estimate_build(conn, cfg, tid, runs=runs, streams=streams, default_me=me,
                                          default_te=te, allow_build=build, whole_blueprint=True,
                                          substream_fn=substream_fn, me_overrides=me_overrides,
                                          params=params)
                if consolidate:
                    core.sourcing.consolidate_shared_components(
                        conn, [est.node], params, default_me=me, default_te=te,
                        allow_build=build, substream_fn=substream_fn,
                        auto_streams=auto_streams, me_overrides=me_overrides,
                    )
                pr = self._profit(conn, cfg, params, est.node)
                m_all, j_all, b_all = core.sourcing.aggregate_costs(est.node)
                rows.append({
                    "me": me_level, "total_cost": est.node.total_cost,
                    "unit_cost": est.node.unit_cost, "material_cost": m_all, "job_cost": j_all,
                    "blueprint_cost": b_all, "profit": pr.profit, "roi": pr.roi,
                })
            return {"rows": rows, "reason": None}
        finally:
            conn.close()

    # ---------------------------------------------------------------- отчёты
    def report_basket(self, b: Basket, *, use_stock: bool = True,
                      respect_reservations: bool = True) -> dict:
        """Сформировать HTML-отчёт по корзине и сохранить в reports/.

        ``use_stock`` — вычитать склад ([stock]) из закупок; ``respect_reservations`` —
        уменьшать доступный склад на то, что зарезервировали незавершённые стройки.
        Возвращает ``{report_id, filename, url}`` (url — путь на локальном сервере отчётов)."""
        if not b.types:
            raise ServiceError(400, tr("Пустая корзина"))
        cfg, conn = self.ctx()
        try:
            r = self._resolve_basket(conn, b)
            products = list(zip(r.tids, r.runs, r.streams, strict=True))
            # Резерв склада под уже существующие отчёты (вычитается из доступного склада нового).
            external_reserved = (report_mod.external_reservations(self.reports_dir)
                                 if use_stock and respect_reservations else None)
            payload = report_mod.build_report_payload(
                conn, cfg, products, default_me=b.me, default_te=b.te, allow_build=b.build,
                max_stream_days=b.max_stream_days, consolidate=b.consolidate,
                auto_streams=b.auto_streams, force_buy_ids=r.force_buy_ids,
                force_buy_extra=r.force_buy_extra, external_reserved=external_reserved,
                me_overrides=r.me_overrides, te_overrides=r.te_overrides,
                use_stock=use_stock,
            )
        finally:
            conn.close()
        html = report_mod.render_report_html(payload)
        return report_mod.write_report(self.reports_dir, payload, html)

    def refresh_reports_index(self) -> None:
        """Переписать страницу-список отчётов (/reports/) — на текущем языке (пульт зовёт после
        переключения RUS/ENG)."""
        report_mod.write_index(self.reports_dir)

    @staticmethod
    def _safe_report_id(report_id: str) -> str:
        rid = str(report_id or "").strip()
        if not rid or "/" in rid or "\\" in rid or ".." in rid:
            raise ServiceError(400, tr("Некорректный report_id"))
        return rid

    def save_report_tracking(self, report_id: str, data: dict) -> dict:
        rid = self._safe_report_id(report_id)
        (self.reports_dir / (rid + ".tracking.json")).write_text(
            json.dumps(data or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True}

    def report_tracking(self, report_id: str) -> dict:
        rid = self._safe_report_id(report_id)
        f = self.reports_dir / (rid + ".tracking.json")
        if not f.exists():
            return {}
        return {"data": json.loads(f.read_text(encoding="utf-8"))}

    def list_reports(self) -> list[dict]:
        """Все сформированные отчёты с живым прогрессом (по отметкам в tracking.json)."""
        out = []
        for mf in self.reports_dir.glob("*.meta.json"):
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rid = meta.get("report_id", "")
            done = 0
            tf = self.reports_dir / (rid + ".tracking.json")
            if rid and tf.exists():
                try:
                    data = json.loads(tf.read_text(encoding="utf-8"))
                    steps = data.get("steps", {}) if isinstance(data, dict) else {}
                    done = sum(1 for v in steps.values() if isinstance(v, dict) and v.get("done"))
                except (OSError, json.JSONDecodeError):
                    pass
            jobs = meta.get("jobs") or 0
            meta["done"] = done
            meta["progress"] = (done / jobs) if jobs else None
            out.append(meta)
        out.sort(key=lambda m: m.get("generated_at", ""), reverse=True)
        return out

    def delete_report(self, report_id: str) -> dict:
        try:
            deleted = report_mod.delete_report_files(self.reports_dir, report_id)
        except ValueError as e:
            raise ServiceError(400, str(e)) from e
        if not deleted:
            raise ServiceError(404, tr("Отчёт не найден"))
        return {"ok": True, "deleted": deleted}

    # ---------------------------------------------------------- рекомендации
    def recommend(self, owned: bool = True, top: int = 30, runs: int | None = None,
                  budget: float | None = None, min_volume: float | None = None,
                  limit: int | None = None) -> list[dict]:
        cfg, conn = self.ctx()
        try:
            recs = recommend_mod.recommend(
                conn, cfg, owned=owned, top=top, runs=runs,
                budget=budget, min_volume=min_volume, limit_candidates=limit,
            )
        finally:
            conn.close()
        return [serializers.recommendation_to_dict(r) for r in recs]

    def recommend_grouped(self, owned: bool = True, runs: int | None = None,
                          budget: float | None = None, min_volume: float | None = None,
                          limit: int | None = None) -> list[dict]:
        """ТОП по каждой настроенной группе (cfg.recommend.groups) — с теми же фильтрами, что
        и общий ТОП (бюджет/мин. объём/лимит из формы)."""
        cfg, conn = self.ctx()
        try:
            rc = cfg.recommend
            out = []
            for g in rc.groups:
                recs = recommend_mod.recommend(
                    conn, cfg, owned=owned, runs=runs, top=rc.top_per_group, group_ids=g.group_ids,
                    budget=budget, min_volume=min_volume, limit_candidates=limit,
                )
                out.append({"name": g.name,
                            "items": [serializers.recommendation_to_dict(r) for r in recs]})
        finally:
            conn.close()
        return out

    def buy_cheaper(self, owned: bool = False, top: int = 60, min_volume: float | None = None,
                    limit: int | None = None, groups: Iterable[int] | None = None) -> list[dict]:
        """Предметы, которые рынок продаёт ДЕШЕВЛЕ себестоимости постройки."""
        cfg, conn = self.ctx()
        try:
            group_ids = [int(g) for g in (groups or [])]
            deals = recommend_mod.buy_cheaper(
                conn, cfg, owned=owned, top=top, min_volume=min_volume, limit_candidates=limit,
                group_ids=group_ids or None,
            )
        finally:
            conn.close()
        return [serializers.buy_deal_to_dict(d) for d in deals]

    def recommend_stock(self, top: int = 30, min_volume: float | None = None,
                        limit: int | None = None, groups: Iterable[int] | None = None,
                        respect_reservations: bool = True) -> list[dict]:
        """ТОП своих чертежей по (реальная прибыль, доля себестоимости со склада). Склад —
        по [stock], за вычетом резерва незавершённых строек (если ``respect_reservations``)."""
        cfg, conn = self.ctx()
        try:
            group_ids = [int(g) for g in (groups or [])]
            reserved = (report_mod.external_reservations(self.reports_dir)
                        if respect_reservations else None)
            recs = recommend_mod.recommend_by_stock(
                conn, cfg, top=top, min_volume=min_volume,
                limit_candidates=limit, group_ids=group_ids or None, reserved=reserved,
            )
        finally:
            conn.close()
        return [serializers.stock_recommendation_to_dict(r) for r in recs]

    def liquidity_info(self) -> dict:
        """Откуда берётся «Объём/сут» ([recommend] liquidity_source) — подсказка на вкладке «Что
        строить» + предупреждение, если нужной истории в БД нет."""
        cfg, conn = self.ctx()
        try:
            rc = cfg.recommend
            src = rc.liquidity_source if rc.liquidity_source in recommend_mod.LIQUIDITY_SOURCES else "sell_region"
            days = max(1, int(rc.liquidity_days or 30))
            places = report_mod.place_labels(cfg)
            jita_region = self._jita_region(cfg)
            cj = cfg.locations.get("c_j6mt")
            sell_region = cj.region_id if cj and cj.region_id else jita_region
            anchor = None
            if src != "sell_region":
                anchor = recommend_mod.engine.history_anchor(
                    conn, jita_region if src == "jita" else sell_region)
        finally:
            conn.close()
        window = (tr("за {days} календ. дн. до {anchor}, дни без сделок = 0", days=days, anchor=anchor)
                  if anchor else tr("за {days} календ. дн., дни без сделок = 0", days=days))
        cj_name, jita_name = places["cj"], places["jita"]
        warning = None
        if src == "sell_region":
            text = tr("Объём/сут — по записям: среднее по последним 30 записям истории рынка (дни без сделок "
                      "не в счёт — неликвид завышен); без истории (для {cj} она не синкается) — "
                      "выставленный на продажу объём, а не проданное.", cj=cj_name)
        elif src == "sell_region_history":
            text = tr("Объём/сут — реальный оборот региона {cj} по истории ESI (со сделками в "
                      "структурах игроков) {window}.", cj=cj_name, window=window)
            if anchor is None:
                warning = tr("История региона {cj} ещё не скачана — «Обзор → Рынок Jita и индексы» "
                             "(качается раз в сутки); до этого объём = 0.", cj=cj_name)
        else:
            text = tr("Объём/сут — оборот {jita} как прокси, по истории ESI {window}.", jita=jita_name, window=window)
            if anchor is None:
                warning = tr("Истории {jita} в БД нет — «Обзор → Рынок Jita и индексы».", jita=jita_name)
        cheaper = (tr("В «Дешевле купить» — тоже оборот {jita}.", jita=jita_name) if src == "jita" else
                   tr("В «Дешевле купить» рынок — хаб покупки предмета ({jita} или {cj}).", jita=jita_name, cj=cj_name))
        return {"source": src, "days": days, "anchor": anchor, "text": text, "warning": warning,
                "cheaper_note": cheaper}

    # ------------------------------------------------------------------ склад
    @staticmethod
    def _jita_region(cfg: config_mod.Config) -> int:
        jita = cfg.locations.get("jita")
        return (jita.region_id if jita and jita.region_id else 10000002)

    # Фильтры «Склада» по предметам, которые дерево «Где что лежит» может взять из несохранённых
    # галок страницы (остальное — из сохранённого [stock]).
    TREE_FILTER_KEYS = ("exclude_fitted", "exclude_assembled_ships", "exclude_flags",
                        "exclude_type_ids", "exclude_group_ids")

    def stock_locations(self, character_ids: Iterable[int] | None = None,
                        filters: dict | None = None) -> dict:
        """Где что лежит — для вкладки «Склад»: системы → станции/структуры (→ контейнеры и
        корабли с содержимым), с числом предметов, оценкой по Jita и отметкой, что сейчас
        входит в склад по настройке [stock].

        ``character_ids`` — только ассеты этих персонажей (пусто/None — всех), как галки «Чьи
        ассеты считать». ``filters`` — фильтры по предметам (``TREE_FILTER_KEYS``; нет — из
        [stock]): у каждого узла ``passing`` — сколько предметов в нём их проходит, ``reasons`` —
        почему отсечены остальные; у контейнера/корабля ещё ``self_passes`` — он сам (корпус).
        Корабли вне ассетов (ESI отдаёт лишь их модули/риги, системы у них нет, в склад не
        входят) в дерево не попадают — только их число в ``ghosts``."""
        cfg, conn = self.ctx()
        try:
            resolver = core.locations.LocationResolver(conn, cfg)
            sc = cfg.stock
            custom = sc.mode == "custom"
            auto_ids = core.stock.auto_location_ids(cfg)
            hub_names = {"jita": "jita", "c_j6mt": "c_j6mt", "gplb_c": "gplb_c"}
            hub_of_system = {loc.system_id: hub_names[k] for k, loc in cfg.locations.items()
                             if k in hub_names and loc.system_id}
            chars = {int(c) for c in character_ids or ()}
            # Персонажей дерево отсекает само (строки не показываются), исключённые локации — это
            # галки дерева; тут — только фильтры по предметам.
            upd = {k: v for k, v in (filters or {}).items() if k in self.TREE_FILTER_KEYS}
            tree_sc = sc.model_copy(update={**upd, "character_ids": [], "exclude_location_ids": []})
            price = {int(r["type_id"]): r["sell_min"] or 0.0 for r in conn.execute(
                "SELECT type_id, sell_min FROM market_snapshot WHERE region_id = ?", (self._jita_region(cfg),))}
            rows = conn.execute(core.stock.ASSET_ROWS_SQL).fetchall()
            items = resolver.items()
            item_filters = core.stock.ItemFilters(tree_sc, rows, items)

            def node() -> dict[str, Any]:
                return {"items": 0, "value": 0.0, "passing": 0, "reasons": {}}

            def add(rec: dict[str, Any], value: float, reason: str | None) -> None:
                rec["items"] += 1
                rec["value"] += value
                if reason is None:
                    rec["passing"] += 1
                else:
                    rec["reasons"][reason] = rec["reasons"].get(reason, 0) + 1

            roots: dict[int, dict[str, Any]] = {}
            containers: dict[int, dict[str, Any]] = {}
            own_reason: dict[int, str | None] = {}  # контейнер/корабль прямо в корне -> почему он сам не склад
            ships: set[int] = set()                  # …из них корабли
            ghosts: dict[int, int] = {}  # корабль вне ассетов -> предметов в нём
            for r in rows:
                loc = r["location_id"]
                if loc is None or (chars and int(r["character_id"]) not in chars):
                    continue
                chain = resolver.chain(int(loc))
                root = chain[-1]
                if resolver.is_ghost_ship(root):
                    ghosts[root] = ghosts.get(root, 0) + 1
                    continue
                value = price.get(int(r["type_id"]), 0.0) * max(int(r["quantity"] or 0), 0)
                reason = item_filters.reason(r, chain)
                rec = roots.setdefault(root, {**node(), "characters": set()})
                add(rec, value, reason)
                rec["characters"].add(int(r["character_id"]))
                if len(chain) == 1 and int(r["item_id"]) in item_filters.parents:
                    own_reason[int(r["item_id"])] = reason
                    if r["category_id"] == core.stock.SHIP_CATEGORY:
                        ships.add(int(r["item_id"]))
                top = chain[-2] if len(chain) >= 2 else None  # контейнер/корабль прямо в корне
                if top is not None and top in items:
                    c = containers.setdefault(top, {**node(), "root": root})
                    add(c, value, reason)

            def reasons(d: dict[str, int]) -> list[list]:
                return [[k, n] for k, n in sorted(d.items(), key=lambda kv: -kv[1])]

            systems: dict[int | None, dict[str, Any]] = {}
            for root, rec in roots.items():
                info = resolver.describe(root)
                sys_key = info.system_id
                srec = systems.setdefault(sys_key, {
                    "system_id": info.system_id,
                    "name": info.system_name or tr("Система не известна"),
                    "security": info.security, "region_id": info.region_id,
                    "hub": hub_of_system.get(info.system_id) if info.system_id else None,
                    "items": 0, "value": 0.0, "passing": 0, "locations": [],
                    "selected": bool(custom and info.system_id in sc.system_ids),
                })
                srec["items"] += rec["items"]
                srec["value"] += rec["value"]
                srec["passing"] += rec["passing"]
                children = []
                for cid, c in containers.items():
                    if c["root"] != root:
                        continue
                    own = own_reason.get(cid)
                    rs = dict(c["reasons"])
                    if own is not None:
                        rs[own] = rs.get(own, 0) + 1
                    children.append({
                        "location_id": cid, "name": resolver.describe(cid).name, "ship": cid in ships,
                        "items": c["items"], "value": c["value"], "passing": c["passing"],
                        "self_passes": own is None, "reasons": reasons(rs),
                        "excluded": cid in sc.exclude_location_ids,
                        "selected": bool(custom and cid in sc.location_ids)})
                children.sort(key=lambda x: -x["value"])
                srec["locations"].append({
                    "location_id": root, "kind": info.kind, "name": info.name,
                    "resolved": info.resolved, "items": rec["items"], "value": rec["value"],
                    "passing": rec["passing"], "reasons": reasons(rec["reasons"]),
                    "characters": sorted(rec["characters"]),
                    "selected": (root in sc.location_ids) if custom else (root in auto_ids),
                    "excluded": root in sc.exclude_location_ids,
                    "children": children,
                })
            out_systems = sorted(systems.values(), key=lambda s: -s["value"])
            for s in out_systems:
                s["locations"].sort(key=lambda x: -x["value"])
            unresolved = sum(1 for s in out_systems for loc in s["locations"] if not loc["resolved"])
        finally:
            conn.close()
        return {"mode": sc.mode, "systems": out_systems, "unresolved": unresolved,
                "auto_location_ids": sorted(auto_ids),
                "ghosts": {"ships": len(ghosts), "items": sum(ghosts.values())}}

    def stock_contents(self, query: str = "", limit: int = 400) -> dict:
        """Что сейчас считается складом (по [stock]): по типам — сколько, где, оценка по Jita."""
        cfg, conn = self.ctx()
        try:
            resolver = core.locations.LocationResolver(conn, cfg)
            view = core.stock.stock_view(conn, cfg, resolver)
            region = self._jita_region(cfg)
            q = query.strip().lower()
            rows: list[dict[str, Any]] = []
            names: dict[int, str] = {}
            total_value = 0.0
            for tid, total in view.totals.items():
                name = prices.type_name(conn, tid)
                price = prices.sell_min(conn, tid, region)
                value = (price or 0.0) * total
                total_value += value
                if q and q not in name.lower():
                    continue
                lots = []
                for lot in view.lots.get(tid, []):
                    if lot.root_id not in names:
                        names[lot.root_id] = resolver.describe(lot.root_id).name
                    lots.append({"root_id": lot.root_id, "label": names[lot.root_id],
                                 "hub": lot.hub, "quantity": lot.quantity})
                rows.append({"type_id": tid, "name": name, "quantity": total,
                             "kept": view.kept.get(tid, 0), "unit": price, "value": value,
                             "lots": lots})
            rows.sort(key=lambda x: -x["value"])
        finally:
            conn.close()
        return {"rows": rows[:limit], "types": len(view.totals), "shown": min(len(rows), limit),
                "total_value": total_value}

    def asset_flags(self) -> list[dict]:
        """Какие location_flag встречаются у ассетов (для исключений «не считать складом»)."""
        _cfg, conn = self.ctx()
        try:
            rows = conn.execute(
                "SELECT location_flag AS flag, COUNT(*) AS n FROM character_asset_flags "
                "WHERE location_flag IS NOT NULL GROUP BY location_flag ORDER BY n DESC"
            ).fetchall()
        finally:
            conn.close()
        return [{"flag": r["flag"], "count": r["n"]} for r in rows]

    # ------------------------------------------------------------- настройки
    def _facility_dict(self, conn, f: config_mod.Facility, resolver, build_system: int) -> dict:
        """Facility + какая система реально используется (своя / из структуры / стройки) и
        расчётный % из фитованных ригов/типа структуры по security ЭТОЙ системы (только для
        показа; расчёт — core.build_params_from_config)."""
        d = f.model_dump()
        sys_id, how = core.facility_system(f, resolver, build_system)
        info = resolver.system(sys_id)
        d["resolved_system_id"] = sys_id
        d["resolved_system_how"] = how
        d["resolved_system_name"] = info[0] if info else None
        d["resolved_security"] = info[2] if info else None
        security = info[2] if info else None
        if f.fitted_type_ids or f.structure_type_id:
            for kind in ("material", "time", "cost"):
                d[f"computed_{kind}_bonus_pct"] = core.rigs.facility_bonus_pct(
                    conn, f.fitted_type_ids, f.role, kind, security, f.structure_type_id
                )
        return d

    def get_config(self) -> dict:
        """Весь конфиг для экрана настроек (+ система и расчётные бонусы станций)."""
        cfg, conn = self.ctx()
        try:
            resolver = core.locations.LocationResolver(conn, cfg)
            gplb = cfg.locations.get("gplb_c")
            build_system = gplb.system_id if gplb else 0
            facilities = [self._facility_dict(conn, f, resolver, build_system) for f in cfg.facilities]
        finally:
            conn.close()
        data = cfg.model_dump(by_alias=True)
        data["facilities"] = facilities
        return data

    def put_config(self, update: dict[str, Any]) -> dict:
        """Сохранить изменения в forge.toml. Секции-словари сливаются на один уровень
        (частичное обновление не сбрасывает остальные поля секции), списки и скаляры —
        заменяются. Валидация — pydantic-моделью ``Config`` целиком."""
        cfg = self.load_cfg()
        data = cfg.model_dump(by_alias=True)
        for key, value in (update or {}).items():
            if value is None:
                continue
            if key not in data:
                raise ServiceError(400, tr("Неизвестная настройка: {key}", key=key))
            if isinstance(value, dict) and isinstance(data.get(key), dict) and key != "locations":
                data[key] = {**data[key], **value}
            else:
                data[key] = value
        try:
            new_cfg = config_mod.Config.model_validate(data)
        except Exception as e:  # pydantic.ValidationError — показать как есть
            raise ServiceError(400, tr("Настройки не приняты: {e}", e=e)) from e
        config_mod.save(new_cfg, self.config_path)
        return {"ok": True}
