"""Локальный HTTP-сервер Forge (FastAPI) — тонкая обёртка над ``service.ForgeService``.

Интерфейс Forge — десктопный пульт (``forge.desktop``); этот сервер поднимается внутри
пульта (или командой ``forge web``) ради HTML-отчётов по стройкам: отдаёт ``/reports/*`` и
принимает их отметки о прогрессе (``/api/report-tracking``). Остальные ``/api/*`` — для
скриптов; всё считается в сервисе, здесь только разбор параметров.

Сеть наружу не делает (читает локальную БД). Слушает только 127.0.0.1 (см. forge.desktop).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config as config_mod
from .service import (
    Basket,
    ForgeService,
    ServiceError,
    parse_csv,
    parse_csv_ints,
    parse_csv_optional_ints,
)

# Каталог собранного веб-фронта (vite build → web/dist) — в поставке его нет; если кто-то
# соберёт его сам, сервер отдаст его статикой.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class ConfigUpdate(BaseModel):
    """Тело PUT /api/config: любые секции конфига (частичное обновление, см.
    ``ForgeService.put_config``). На уровне модуля — иначе FastAPI не разрезолвит аннотацию."""

    model_config = {"extra": "allow"}


class ResolveNamesBody(BaseModel):
    """Тело POST /api/resolve-names — точные имена предметов (вставка фита EVE из буфера)."""

    names: list[str]


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ServiceError as e:
        raise HTTPException(e.status, e.message) from e


def _basket(types: str, runs: str, streams: str, me: int, te: int, build: bool,
            max_stream_days: float | None, consolidate: bool, auto_streams: bool,
            buy: str, buy_components: str, me_override: str, te_override: str) -> Basket:
    return Basket(
        types=list(parse_csv(types)), runs=parse_csv_ints(runs) or [1],
        streams=parse_csv_ints(streams) or [1], me=me, te=te, build=build,
        max_stream_days=max_stream_days, consolidate=consolidate, auto_streams=auto_streams,
        buy=list(parse_csv(buy)), buy_components=parse_csv_ints(buy_components),
        me_override=parse_csv_optional_ints(me_override),
        te_override=parse_csv_optional_ints(te_override),
    )


def create_app(config_path: str = config_mod.DEFAULT_CONFIG_PATH) -> FastAPI:
    app = FastAPI(title="Forge", docs_url="/api/docs", openapi_url="/api/openapi.json")
    svc = ForgeService(config_path)
    app.state.service = svc

    # --- API ---------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/status")
    def status() -> dict:
        return svc.status()

    @app.post("/api/sync/{source}")
    def trigger_sync(source: str) -> dict:
        return _call(svc.trigger_sync, source)

    @app.post("/api/auth/add")
    def trigger_auth() -> dict:
        return _call(svc.trigger_auth)

    @app.get("/api/characters")
    def characters() -> list[dict]:
        return svc.characters()

    @app.get("/api/blueprint-locations")
    def blueprint_locations() -> list[dict]:
        return svc.blueprint_locations()

    @app.get("/api/search")
    def search(q: str, limit: int = 20) -> list[dict]:
        return svc.search(q, limit)

    @app.post("/api/resolve-names")
    def resolve_names(body: ResolveNamesBody) -> list[dict]:
        return svc.resolve_names(body.names)

    @app.get("/api/cost")
    def cost(type: str, runs: int = 1, streams: int = 1, me: int = 0, te: int = 0, build: bool = True,
             max_stream_days: float | None = None, consolidate: bool = False,
             auto_streams: bool = False, me_override: int | None = None,
             te_override: int | None = None) -> dict:
        return _call(svc.cost, type, runs, streams, me, te, build, max_stream_days, consolidate,
                     auto_streams, me_override, te_override)

    @app.get("/api/cost-basket")
    def cost_basket(types: str, runs: str = "1", streams: str = "1", me: int = 0, te: int = 0,
                    build: bool = True, max_stream_days: float | None = None,
                    consolidate: bool = False, auto_streams: bool = False, buy: str = "",
                    buy_components: str = "", me_override: str = "", te_override: str = "") -> dict:
        b = _basket(types, runs, streams, me, te, build, max_stream_days, consolidate,
                    auto_streams, buy, buy_components, me_override, te_override)
        return _call(svc.cost_basket, b)

    @app.get("/api/plan")
    def plan(type: str, runs: int = 1, streams: int = 1, me: int = 0, te: int = 0, build: bool = True,
             max_stream_days: float | None = None) -> dict:
        return _call(svc.plan, type, runs, streams, me, te, build, max_stream_days)

    @app.get("/api/plan-basket")
    def plan_basket(types: str, runs: str = "1", streams: str = "1", me: int = 0, te: int = 0,
                    build: bool = True, max_stream_days: float | None = None,
                    consolidate: bool = True, auto_streams: bool = False,
                    buy: str = "", buy_components: str = "",
                    me_override: str = "", te_override: str = "") -> dict:
        b = _basket(types, runs, streams, me, te, build, max_stream_days, consolidate,
                    auto_streams, buy, buy_components, me_override, te_override)
        return _call(svc.plan_basket, b)

    @app.get("/api/compare-basket")
    def compare_basket(types: str, runs: str = "1", streams: str = "1", me: int = 0, te: int = 0,
                       build: bool = True, buy: str = "", buy_components: str = "",
                       me_override: str = "", te_override: str = "") -> dict:
        b = _basket(types, runs, streams, me, te, build, None, True, False, buy, buy_components,
                    me_override, te_override)
        return _call(svc.compare_basket, b)

    @app.get("/api/compare-me")
    def compare_me(type: str, runs: int = 1, streams: int = 1, me: int = 0, te: int = 0,
                   build: bool = True, max_stream_days: float | None = None,
                   consolidate: bool = False, auto_streams: bool = False) -> dict:
        return _call(svc.compare_me, type, runs, streams, me, te, build, max_stream_days,
                     consolidate, auto_streams)

    @app.get("/api/report-basket")
    def report_basket(types: str, runs: str = "1", streams: str = "1", me: int = 0, te: int = 0,
                      build: bool = True, max_stream_days: float | None = None,
                      consolidate: bool = True, auto_streams: bool = False,
                      buy: str = "", buy_components: str = "",
                      me_override: str = "", te_override: str = "",
                      use_stock: bool = True, respect_reservations: bool = True) -> dict:
        b = _basket(types, runs, streams, me, te, build, max_stream_days, consolidate,
                    auto_streams, buy, buy_components, me_override, te_override)
        return _call(svc.report_basket, b, use_stock=use_stock,
                     respect_reservations=respect_reservations)

    @app.post("/api/report-tracking")
    def post_report_tracking(body: dict) -> dict:
        """Сохранить заполненные факт-данные отчёта в reports/<report_id>.tracking.json."""
        return _call(svc.save_report_tracking, body.get("report_id", ""), body.get("data", {}))

    @app.get("/api/reports")
    def list_reports() -> list[dict]:
        return svc.list_reports()

    @app.delete("/api/reports/{report_id}")
    def delete_report(report_id: str) -> dict:
        return _call(svc.delete_report, report_id)

    @app.get("/api/report-tracking/{report_id}")
    def get_report_tracking(report_id: str) -> dict:
        return _call(svc.report_tracking, report_id)

    @app.get("/api/recommend")
    def recommend(owned: bool = True, top: int = 30, runs: int | None = None,
                  budget: float | None = None, min_volume: float | None = None,
                  limit: int | None = None) -> list[dict]:
        return svc.recommend(owned, top, runs, budget, min_volume, limit)

    @app.get("/api/buy-cheaper")
    def buy_cheaper(owned: bool = False, top: int = 60, min_volume: float | None = None,
                    limit: int | None = None, groups: str | None = None) -> list[dict]:
        return svc.buy_cheaper(owned, top, min_volume, limit, parse_csv_ints(groups or ""))

    @app.get("/api/recommend-stock")
    def recommend_stock(top: int = 30, min_volume: float | None = None,
                        limit: int | None = None, groups: str | None = None) -> list[dict]:
        return svc.recommend_stock(top, min_volume, limit, parse_csv_ints(groups or ""))

    @app.get("/api/recommend/grouped")
    def recommend_grouped(owned: bool = True, runs: int | None = None,
                          budget: float | None = None, min_volume: float | None = None,
                          limit: int | None = None) -> list[dict]:
        return svc.recommend_grouped(owned, runs, budget, min_volume, limit)

    @app.get("/api/groups")
    def groups(q: str, limit: int = 30) -> list[dict]:
        return svc.groups(q, limit)

    @app.get("/api/categories")
    def categories(q: str, limit: int = 30) -> list[dict]:
        return svc.categories(q, limit)

    @app.get("/api/systems")
    def systems(q: str, limit: int = 30) -> list[dict]:
        return svc.systems(q, limit)

    @app.get("/api/category-names")
    def category_names(ids: str) -> dict:
        return svc.category_names(parse_csv_ints(ids))

    @app.get("/api/rigs")
    def rigs_search(q: str, limit: int = 30) -> list[dict]:
        return svc.rigs(q, limit)

    @app.get("/api/group-names")
    def group_names(ids: str) -> dict:
        return svc.group_names(parse_csv_ints(ids))

    @app.get("/api/type-names")
    def type_names(ids: str) -> dict:
        return svc.type_names(parse_csv_ints(ids))

    @app.get("/api/stock/locations")
    def stock_locations() -> dict:
        return svc.stock_locations()

    @app.get("/api/stock/contents")
    def stock_contents(q: str = "", limit: int = 400) -> dict:
        return svc.stock_contents(q, limit)

    @app.get("/api/config")
    def get_config() -> dict:
        return svc.get_config()

    @app.put("/api/config")
    def put_config(update: ConfigUpdate) -> dict:
        data: dict[str, Any] = update.model_dump(exclude_none=True)
        return _call(svc.put_config, data)

    # --- статика: отчёты (до catch-all '/') и собранный фронт (если есть) --------
    # html=True → запрос каталога /reports/ отдаёт index.html (страница-список строек).
    app.mount("/reports", StaticFiles(directory=str(svc.reports_dir), html=True), name="reports")
    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

    return app
