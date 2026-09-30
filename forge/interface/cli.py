"""CLI Forge: ``forge sync`` и ``forge status``.

Никто не импортирует ``interface``. Команды собирают конфиг, открывают БД, инициализируют
схему и вызывают оркестратор ``sync``.
"""

from __future__ import annotations

import enum
import sqlite3
import sys

import typer

# Консоль Windows по умолчанию cp1252 — кириллический вывод падал бы с UnicodeEncodeError.
# Принудительно выводим в UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from .. import config as config_mod
from .. import core, storage
from .. import planner as planner_mod
from .. import recommend as recommend_mod
from ..core import prices
from ..ingest import character
from ..storage import repositories as repo
from ..storage import sync_state
from ..sync import orchestrator

app = typer.Typer(add_completion=False, help="Forge — локальный помощник по индустрии EVE.")
auth_app = typer.Typer(add_completion=False, help="EVE SSO: добавление и список персонажей.")
app.add_typer(auth_app, name="auth")


class Source(enum.StrEnum):
    all = "all"
    sde = "sde"
    market = "market"
    industry = "industry"
    character = "character"


def _load_config_or_default(path: str) -> config_mod.Config:
    try:
        return config_mod.load(path)
    except FileNotFoundError:
        typer.echo(f"[warn] Конфиг {path} не найден — использую значения по умолчанию.")
        return config_mod.Config()


def _open(cfg: config_mod.Config) -> sqlite3.Connection:
    conn = storage.connect(cfg.db_path)
    storage.init_db(conn)
    return conn


def _print_result(r: orchestrator.SyncResult) -> None:
    if r.skipped:
        typer.echo(f"  {r.source:10} — пропущено ({r.note})")
    else:
        extra = f" — {r.note}" if r.note else ""
        typer.echo(f"  {r.source:10} — {r.rows} строк{extra}")


@app.command()
def sync(
    source: Source = typer.Argument(Source.all, help="Источник: all/sde/market/industry/character"),
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
    force: bool = typer.Option(False, "--force", help="Игнорировать свежесть ESI-кэша"),
    type_id: list[int] = typer.Option(None, "--type", "-t", help="type_id для рынка (можно повторять)"),
) -> None:
    """Загрузить данные выбранного источника в локальную БД."""
    cfg = _load_config_or_default(config)
    conn = _open(cfg)
    config_mod.resolve_locations(cfg, conn)

    # Свежий процесс синка ⇒ любой оставшийся 'running' — от мёртвого прошлого run.
    for src in sync_state.reap_stale(conn, max_age_minutes=0):
        typer.echo(f"  сброшен зависший статус: {src}")

    def progress(done: int, total: int) -> None:
        # \r — перерисовываем строку прогресса на месте; в конце переводим на новую строку.
        typer.echo(f"\r  история: {done}/{total}   ", nl=(done >= total))

    typer.echo(f"Синк: {source.value}")
    if source in (Source.all,):
        for r in orchestrator.sync_all(conn, cfg, force=force, progress=progress):
            _print_result(r)
    elif source is Source.sde:
        _print_result(orchestrator.sync_sde(conn, force=force))
    elif source is Source.industry:
        _print_result(orchestrator.sync_industry(conn, force=force))
    elif source is Source.market:
        _print_result(orchestrator.sync_market(conn, cfg, type_ids=type_id or None, force=force, progress=progress))
    elif source is Source.character:
        _print_result(orchestrator.sync_character(conn, cfg))
    conn.close()


# Таблицы для отчёта по наполнению.
_REPORT_TABLES = [
    "sde_types",
    "sde_systems",
    "sde_blueprints",
    "market_history",
    "market_snapshot",
    "market_adjusted_prices",
    "system_cost_indices",
    "characters",
]


@app.command()
def status(
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
) -> None:
    """Показать свежесть данных по каждому источнику."""
    cfg = _load_config_or_default(config)
    conn = _open(cfg)

    typer.echo("Источники (sync_state):")
    states = sync_state.all_states(conn)
    if not states:
        typer.echo("  (ещё ничего не синкалось)")
    for s in states:
        last = s["last_success"] or s["last_run"] or "—"
        typer.echo(
            f"  {s['source']:10} {s['status'] or '—':8} last={last} rows={s['rows'] if s['rows'] is not None else '—'}"
        )
        if s["note"]:
            typer.echo(f"             note: {s['note']}")

    typer.echo("\nНаполнение таблиц:")
    for table in _REPORT_TABLES:
        try:
            typer.echo(f"  {table:24} {repo.count(conn, table)}")
        except sqlite3.OperationalError:
            typer.echo(f"  {table:24} (нет таблицы)")
    conn.close()


def _isk(v: float | None) -> str:
    return f"{v:,.2f}" if v is not None else "—"


@app.command()
def cost(
    product: str = typer.Argument(..., help="Название предмета или type_id"),
    runs: int = typer.Option(1, "--runs", "-r", help="Сколько прогонов всего"),
    streams: int = typer.Option(1, "--streams", "-s", help="На сколько параллельных джобов разбить"),
    me: int = typer.Option(0, "--me", help="ME по умолчанию, если нет своего чертежа"),
    no_build: bool = typer.Option(False, "--no-build", help="Только покупать материалы (без make-or-buy)"),
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
) -> None:
    """Посчитать себестоимость и прибыль постройки предмета."""
    cfg = _load_config_or_default(config)
    conn = _open(cfg)
    config_mod.resolve_locations(cfg, conn)

    type_id = int(product) if product.isdigit() else prices.resolve_type_id(conn, product)
    if type_id is None:
        typer.echo(f"Не нашёл предмет: {product}")
        raise typer.Exit(code=1)

    est = core.estimate_build(
        conn, cfg, type_id, runs=runs, streams=streams,
        default_me=me, allow_build=not no_build,
    )
    n = est.node
    typer.echo(f"{n.name}  (type {n.product_type_id})")
    typer.echo(f"  прогонов: {runs} в {streams} поток(а/ов) -> {n.produced} шт.")
    if n.activity_id == 0:
        typer.echo("  Этот предмет нельзя построить (нет чертежа).")
        conn.close()
        return
    typer.echo("  Материалы:")
    for ln in n.lines:
        src = {"buy": "купить", "build": "строить", "unknown": "нет цены"}[ln.source]
        typer.echo(f"    {ln.name:28} ×{ln.quantity:<10} {src:8} {_isk(ln.subtotal)}")
    typer.echo(f"  Материалы итого: {_isk(n.material_cost)} ISK")
    typer.echo(f"  Установка джоба: {_isk(n.job_cost)} ISK")
    typer.echo(f"  СЕБЕСТОИМОСТЬ:   {_isk(n.total_cost)} ISK  ({_isk(n.unit_cost)} за шт.)")
    p = est.profit
    typer.echo(f"  Цена продажи:    {_isk(p.sell_unit_price)} ISK/шт")
    typer.echo(f"  Выручка:         {_isk(p.revenue)} ISK")
    roi = f"{p.roi*100:,.1f}%" if p.roi is not None else "—"
    typer.echo(f"  ПРИБЫЛЬ:         {_isk(p.profit)} ISK  (ROI {roi})")
    if n.missing_prices:
        typer.echo(f"  ⚠ нет цен для {len(set(n.missing_prices))} материал(ов) — результат неполный")
    conn.close()


@app.command()
def plan(
    product: str = typer.Argument(..., help="Название предмета или type_id"),
    runs: int = typer.Option(1, "--runs", "-r"),
    streams: int = typer.Option(1, "--streams", "-s"),
    max_days: float = typer.Option(None, "--max-days", help="Макс. дней на поток (число потоков выводится из срока)"),
    me: int = typer.Option(0, "--me"),
    no_build: bool = typer.Option(False, "--no-build", help="Только покупать материалы"),
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
) -> None:
    """Построить расписание (кто/когда/в каком слоте строит) и срок."""
    cfg = _load_config_or_default(config)
    conn = _open(cfg)
    config_mod.resolve_locations(cfg, conn)

    type_id = int(product) if product.isdigit() else prices.resolve_type_id(conn, product)
    if type_id is None:
        typer.echo(f"Не нашёл предмет: {product}")
        raise typer.Exit(code=1)

    p = planner_mod.plan_build(
        conn, cfg, type_id, runs=runs, streams=streams, default_me=me,
        allow_build=not no_build, max_stream_days=max_days,
    )
    n = p.estimate.node
    typer.echo(f"{n.name}  (type {n.product_type_id})")
    typer.echo(f"  себестоимость: {_isk(n.total_cost)} ISK ({_isk(n.unit_cost)}/шт), {n.produced} шт.")
    pr = p.estimate.profit
    roi = f"{pr.roi*100:,.1f}%" if pr.roi is not None else "—"
    typer.echo(f"  прибыль: {_isk(pr.profit)} ISK (ROI {roi})")

    s = p.schedule
    if not s.items:
        typer.echo("  Расписание пустое (нет джобов/персонажей).")
        for w in s.warnings:
            typer.echo(f"  ⚠ {w}")
        conn.close()
        return

    typer.echo(f"\n  Расписание ({len(s.items)} джоб(ов), срок {planner_mod.format_duration(s.makespan)}):")
    by_char: dict[str, list] = {}
    for it in sorted(s.items, key=lambda x: (x.character_name, x.start)):
        by_char.setdefault(it.character_name, []).append(it)
    fd = planner_mod.format_duration
    for cname, items in by_char.items():
        typer.echo(f"  • {cname}:")
        for it in items:
            act = "реакция" if it.activity_id == 11 else "произв."
            typer.echo(
                f"      [{it.pool[:4]}#{it.slot}] {act} {it.name} ×{it.runs}: "
                f"{fd(it.start)} → {fd(it.end)}"
            )
    for w in dict.fromkeys(s.warnings):
        typer.echo(f"  ⚠ {w}")
    conn.close()


def _isk_short(v: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(v) >= div:
            return f"{v/div:,.1f}{unit}"
    return f"{v:,.0f}"


@app.command()
def desktop(
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
    no_server: bool = typer.Option(False, "--no-server", help="Не поднимать сервер HTML-отчётов"),
) -> None:
    """Открыть пульт Forge (PySide6) — основной интерфейс."""
    try:
        from ..desktop.app import main as desktop_main
    except ImportError:
        typer.echo("Нужны зависимости пульта: pip install -e .[desktop]")
        raise typer.Exit(code=1) from None
    args = ["--config", config] + (["--no-server"] if no_server else [])
    raise typer.Exit(code=desktop_main(args))


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port", "-p"),
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
) -> None:
    """Только сервер HTML-отчётов и API (http://127.0.0.1:8000/reports/) — без пульта."""
    try:
        import uvicorn
    except ImportError:
        typer.echo("Нужны веб-зависимости: pip install -e .[web]")
        raise typer.Exit(code=1) from None
    from .. import i18n
    from ..sync.scheduler import AutoSyncScheduler
    from ..web import create_app

    # Отчёты и страница-список — на языке пульта ([ui] lang); сам CLI остаётся русским.
    i18n.set_lang(config_mod.load(config).ui.lang)

    # Фоновое автообновление (выключено в конфиге по умолчанию; включается на вкладке «Статус»).
    scheduler = AutoSyncScheduler(config)
    scheduler.start()

    typer.echo(f"Forge web: http://{host}:{port}  (Ctrl+C для выхода)")
    try:
        uvicorn.run(create_app(config), host=host, port=port, log_level="warning")
    finally:
        scheduler.stop()


@app.command()
def recommend(
    top: int = typer.Option(20, "--top", "-n", help="Сколько позиций показать"),
    all_buildable: bool = typer.Option(False, "--all", help="Все производимые (не только свои чертежи)"),
    budget: float = typer.Option(None, "--budget", help="Потолок вложения на партию, ISK"),
    min_volume: float = typer.Option(None, "--min-volume", help="Мин. суточный объём"),
    runs: int = typer.Option(None, "--runs", "-r", help="Прогонов на партию"),
    limit: int = typer.Option(None, "--limit", help="Ограничить число кандидатов (скорость)"),
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
) -> None:
    """Отранжировать, что выгоднее строить (ROI / ISK-час / ликвидность)."""
    cfg = _load_config_or_default(config)
    conn = _open(cfg)
    config_mod.resolve_locations(cfg, conn)

    typer.echo("Считаю кандидатов…")
    recs = recommend_mod.recommend(
        conn, cfg, owned=not all_buildable, runs=runs, budget=budget,
        min_volume=min_volume, top=top, limit_candidates=limit,
    )
    if not recs:
        typer.echo("Нет подходящих позиций (проверь данные рынка/чертежей и фильтры).")
        conn.close()
        return

    typer.echo(f"\n{'#':>2} {'Предмет':28} {'ROI':>7} {'ISK/час':>10} {'сут.объём':>10} {'вложение':>10}")
    typer.echo("  " + "-" * 70)
    for i, r in enumerate(recs, 1):
        roi = f"{r.roi*100:,.0f}%"
        typer.echo(
            f"{i:>2} {r.name[:28]:28} {roi:>7} {_isk_short(r.isk_per_hour):>10} "
            f"{_isk_short(r.daily_volume):>10} {_isk_short(r.capital):>10}"
        )
    conn.close()


@auth_app.command("add")
def auth_add(
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
) -> None:
    """Добавить персонажа через EVE SSO (откроется браузер)."""
    cfg = _load_config_or_default(config)
    conn = _open(cfg)
    if not cfg.sso.client_id:
        typer.echo("Ошибка: не задан sso.client_id в конфиге.")
        raise typer.Exit(code=1)
    typer.echo(f"Открываю браузер для авторизации (callback {cfg.sso.redirect_uri})…")
    claims = character.add_character(conn, cfg)
    typer.echo(f"Добавлен: {claims.name} (id {claims.character_id}), scopes: {len(claims.scopes)}")
    conn.close()


@auth_app.command("list")
def auth_list(
    config: str = typer.Option(config_mod.DEFAULT_CONFIG_PATH, "--config", "-c"),
) -> None:
    """Показать добавленных персонажей."""
    cfg = _load_config_or_default(config)
    conn = _open(cfg)
    rows = character.list_characters(conn)
    if not rows:
        typer.echo("Персонажи не добавлены. Запусти `forge auth add`.")
    for r in rows:
        wallet = r["wallet_balance"]
        wallet_s = f"{wallet:,.0f} ISK" if wallet is not None else "—"
        relogin = r["refresh_token"] == character.tokens.MARKER_RELOGIN
        typer.echo(f"  {r['character_id']}  {r['name']:24} кошелёк={wallet_s}"
                   + ("  НУЖЕН ВХОД — `forge auth add` этим персонажем" if relogin else ""))
    conn.close()


if __name__ == "__main__":
    app()
