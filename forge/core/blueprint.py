"""Стоимость чертежа на 1 run продукта (чистые расчёты, без сети).

Политика: свой BPO/BPC → 0 (sunk); T2 без своего чертежа → стоимость инвенты из SDE
(датакоры / вероятность / раны на копию); ручной оверрайд имеет приоритет; иначе — 0 с
флагом `missing` (нет цены чертежа, нужен ручной ввод). Реакции чертёж не стоят.

Шанс инвенты — как в EVE: база SDE × (1 + Encryption/40 + (наука1 + наука2)/30) × множитель
декриптора, не больше 1.0 (см. ``invention_skills``; скиллы — лучшего кандидата-инвентора из
``BuildParams.inventor_skills``, опция ``[industry] invention_use_skills``).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from ..i18n import tr
from . import cost, prices
from .bom import REACTION

INVENTION = 8

# База инвентированной T2-копии (до декриптора): ME 2 / TE 4.
INVENT_BASE_ME = 2
INVENT_BASE_TE = 4

# Скилл шифрования инвенты — «<раса> Encryption Methods» (в SDE их 7: Amarr/Caldari/Gallente/
# Minmatar/Sleeper/Triglavian/Upwell); остальные требуемые инвентой скиллы — две науки
# (датакорные: Mechanical Engineering, Gallente Starship Engineering и т.п.).
ENCRYPTION_SKILL_MARK = "Encryption Methods"
ENCRYPTION_DIVISOR = 40.0
SCIENCE_DIVISOR = 30.0


@dataclass(frozen=True)
class Decryptor:
    """Декриптор инвенты (модификаторы из SDE, фиксированные игровые константы)."""

    type_id: int
    name: str
    prob_mult: float   # множитель вероятности (1.9 = +90%)
    me_mod: int        # модификатор ME итоговой BPC
    te_mod: int        # модификатор TE
    run_mod: int       # модификатор числа прогонов на копию


# 8 декрипторов (type_id, имя, вероятность, ME, TE, прогоны) — значения из dgmTypeAttributes SDE.
DECRYPTORS = [
    Decryptor(34201, "Accelerant", 1.2, 2, 10, 1),
    Decryptor(34202, "Attainment", 1.8, -1, 4, 4),
    Decryptor(34203, "Augmentation", 0.6, -2, 2, 9),
    Decryptor(34204, "Parity", 1.5, 1, -2, 3),
    Decryptor(34205, "Process", 1.1, 3, 6, 0),
    Decryptor(34206, "Symmetry", 1.0, 1, 8, 2),
    Decryptor(34207, "Optimized Attainment", 1.9, 1, -2, 2),
    Decryptor(34208, "Optimized Augmentation", 0.9, 2, 0, 7),
]
DECRYPTOR_BY_ID = {d.type_id: d for d in DECRYPTORS}


@dataclass
class InventionOption:
    """Вариант инвенты: стоимость на 1 прогон продукта + ME/TE итоговой BPC."""

    per_run: float
    me: int
    te: int
    runs_per_copy: int
    decryptor: str | None = None
    decryptor_type_id: int | None = None  # type_id декриптора (для MaterialLine — список закупок)
    prob: float = 0.0           # эффективная вероятность (скиллы × декриптор, не больше 1.0)
    datacores: float = 0.0      # стоимость датакоров за попытку
    decryptor_cost: float = 0.0  # цена декриптора за попытку (0 без него)
    base_prob: float = 0.0      # базовый шанс из SDE (без скиллов и декриптора)
    skill_mult: float = 1.0     # множитель скиллов лучшего инвентора (1.0 — без скиллов)
    decryptor_mult: float = 1.0  # множитель шанса декриптора (1.0 — без него)
    inventor_id: int | None = None      # лучший кандидат-инвентор (по множителю скиллов)
    inventor_name: str | None = None


@dataclass(frozen=True)
class InventionSkills:
    """Множитель шанса инвенты от скиллов лучшего кандидата-инвентора для ОДНОГО чертежа.

    ``mult`` = 1 + Σ Encryption/40 + Σ наук/30 (скиллы — те, что требует инвента этого
    T1-чертежа-источника, ``sde_blueprint_skills`` activity 8). ``enabled`` — включена ли опция
    вообще (для подписи в UI: «не учитываются» против «нет назначенных на науку»)."""

    mult: float = 1.0
    character_id: int | None = None
    character_name: str | None = None
    enabled: bool = False


def attempts_for(bpcs: int, prob: float) -> int:
    """Целое число попыток инвенты, чтобы получить ``bpcs`` успешных копий при шансе ``prob``
    (всегда вверх — дробную попытку не провести). ``round(…, 9)`` — защита от плавающей
    запятой: 9 / (0.3 × 1.5) = 20.000000000000004 дало бы 21 попытку вместо 20 (со скиллами
    такие «почти целые» произведения шансов встречаются чаще)."""
    if prob <= 0:
        return 0
    return math.ceil(round(bpcs / prob, 9))


@dataclass
class BlueprintCost:
    per_run: float
    total: float           # per_run × runs
    source: str            # owned_bpo|owned_bpc|owned_bpc_insufficient|invention|manual|missing|missing_reaction
    decryptor: str | None = None  # выбранный декриптор (если source='invention')
    # Только для реакций: сколько физических копий формулы во владении/нужно под потоки.
    reaction_owned: int | None = None
    reaction_needed: int | None = None
    # Только для source='owned_bpc_insufficient': суммарный остаток ранов своих копий vs
    # сколько прогонов реально нужно этому узлу (см. owned_bpc_total_runs).
    bpc_runs_owned: int | None = None
    bpc_runs_needed: int | None = None


def owned_kind(conn: sqlite3.Connection, blueprint_type_id: int, location_ids=None) -> str | None:
    """'bpo' если есть свой оригинал, 'bpc' если копия с остатком ранов, иначе None.

    ``location_ids`` (если задан) ограничивает поиск чертежами в этих локациях.
    """
    clause, loc_params = prices.loc_in_clause(location_ids)
    row = conn.execute(
        """
        SELECT MIN(is_copy) AS has_bpo,
               MAX(CASE WHEN is_copy = 1 AND (runs IS NULL OR runs > 0) THEN 1 ELSE 0 END) AS has_bpc
        FROM character_blueprints WHERE type_id = ?
        """ + clause,
        (blueprint_type_id, *loc_params),
    ).fetchone()
    if row is None or row["has_bpo"] is None:
        return None
    if row["has_bpo"] == 0:
        return "bpo"
    return "bpc" if row["has_bpc"] else None


def owned_bpc_total_runs(conn: sqlite3.Connection, blueprint_type_id: int, location_ids=None) -> int:
    """Суммарный остаток прогонов по ВСЕМ своим КОПИЯМ (BPC, не BPO) чертежа в этих локациях.

    Вызывать ТОЛЬКО когда ``owned_kind(...) == 'bpc'`` (не 'bpo' — у оригинала раны не
    ограничены, эту функцию для него звать не нужно). Одиночная копия (``quantity=-2``) —
    её ``runs``. Стек идентичных копий, слитых ESI в одну позицию (``quantity>0``, у каждой
    копии в стеке один и тот же ``runs``) — ``quantity × runs``. Строки без определённого
    остатка (``runs`` NULL/≤0) не считаются — это не полноценная возможность запустить джоб.
    """
    clause, loc_params = prices.loc_in_clause(location_ids)
    rows = conn.execute(
        "SELECT quantity, runs FROM character_blueprints WHERE type_id = ? AND is_copy = 1" + clause,
        (blueprint_type_id, *loc_params),
    ).fetchall()
    total = 0
    for r in rows:
        qty, runs = r["quantity"], r["runs"]
        if runs is None or runs <= 0:
            continue
        if qty == -2:
            total += runs
        elif qty is not None and qty > 0:
            total += qty * runs
    return total


def owned_bpo_count(conn: sqlite3.Connection, blueprint_type_id: int, location_ids=None) -> int:
    """Сколько своих ОРИГИНАЛОВ (BPO) чертежа в этих локациях — столько копи-джобов с него могут
    идти одновременно (один BPO в EVE одновременно — только в одном джобе). ESI ``quantity``: -1 —
    одиночный BPO, N>0 при ``is_copy=0`` — стек из N оригиналов."""
    clause, loc_params = prices.loc_in_clause(location_ids)
    rows = conn.execute(
        "SELECT quantity FROM character_blueprints WHERE type_id = ? AND is_copy = 0" + clause,
        (blueprint_type_id, *loc_params),
    ).fetchall()
    return sum(int(r["quantity"]) if r["quantity"] and r["quantity"] > 0 else 1 for r in rows)


def reaction_formula_copies(
    conn: sqlite3.Connection, blueprint_type_id: int, location_ids=None
) -> int:
    """Сколько физических копий формулы реакции доступно для ОДНОВРЕМЕННЫХ джобов.

    Как и обычный чертёж, формула занимает один джоб-слот на копию — N параллельных
    потоков нужно покрыть N отдельными экземплярами (BPO переиспользуется между
    последовательными джобами, но не между параллельными). ESI ``quantity``: -1 = BPO,
    -2 = одиночная BPC, N>0 = стек из N одинаковых копий (даёт сразу N штук — на живых
    данных встречается и стек BPO-подобных формул реакции: ``quantity>0`` вместе с
    ``runs=-1``, поэтому доступность по ранам проверяем отдельно от знака ``quantity``).
    """
    def usable(runs) -> bool:
        return runs is None or runs == -1 or runs > 0  # BPO (-1/NULL) всегда; BPC — пока есть раны

    clause, loc_params = prices.loc_in_clause(location_ids)
    rows = conn.execute(
        "SELECT quantity, runs FROM character_blueprints WHERE type_id = ?" + clause,
        (blueprint_type_id, *loc_params),
    ).fetchall()
    total = 0
    for r in rows:
        qty, runs = r["quantity"], r["runs"]
        if qty == -1:
            total += 1
        elif qty == -2:
            if usable(runs):
                total += 1
        elif qty is not None and qty > 0 and usable(runs):
            total += qty
    return total


def _invention_base(
    conn: sqlite3.Connection, manuf_bp_type_id: int, params: cost.BuildParams
) -> tuple[float, float, int] | None:
    """База инвенты: (стоимость датакоров за попытку, вероятность, прогонов/копия) или None."""
    inv = conn.execute(
        """
        SELECT blueprint_type_id, quantity, probability
        FROM sde_blueprint_products
        WHERE product_type_id = ? AND activity_id = ?
        """,
        (manuf_bp_type_id, INVENTION),
    ).fetchone()
    if inv is None:
        return None
    probability = inv["probability"] or 0.0
    runs_per_copy = inv["quantity"] or 1
    if probability <= 0 or runs_per_copy <= 0:
        return None

    attempt = 0.0
    mats = conn.execute(
        "SELECT material_type_id, quantity FROM sde_blueprint_materials "
        "WHERE blueprint_type_id = ? AND activity_id = ?",
        (inv["blueprint_type_id"], INVENTION),
    ).fetchall()
    for m in mats:
        unit = cost.landed_unit_cost(conn, m["material_type_id"], params)
        if unit is None:
            return None  # нет цены датакора → честно не считаем инвенту
        attempt += unit * m["quantity"]
    return attempt, probability, runs_per_copy


def invention_per_run(
    conn: sqlite3.Connection, manuf_bp_type_id: int, params: cost.BuildParams
) -> float | None:
    """Стоимость инвенты на 1 run T2-продукта БЕЗ декриптора; None если пути инвенты нет.

    per_run = Σ landed(датакор)·qty / (вероятность × раны_на_копию). job-fee инвенты опускаем.
    Вероятность — со скиллами лучшего инвентора (``invention_skills``), не больше 1.0.
    """
    base = _invention_base(conn, manuf_bp_type_id, params)
    if base is None:
        return None
    attempt, probability, runs_per_copy = base
    prob = min(1.0, probability * invention_skills(conn, manuf_bp_type_id, params).mult)
    return attempt / (prob * runs_per_copy)


def invention_skills(
    conn: sqlite3.Connection, manuf_bp_type_id: int, params: cost.BuildParams
) -> InventionSkills:
    """Множитель шанса инвенты T2-чертежа ``manuf_bp_type_id`` от скиллов — ЛУЧШИЙ среди
    кандидатов-инвенторов (``params.inventor_skills``, пул «наука»).

    Формула EVE: 1 + Σ(уровень Encryption Methods)/40 + Σ(уровни двух наук)/30 — по тем
    скиллам, которые требует инвента ИМЕННО этого T1-чертежа-источника (``sde_blueprint_skills``
    activity 8: у Fenrir — Minmatar Encryption Methods + Minmatar Starship Engineering +
    Molecular Engineering). Скилл шифрования узнаём по имени (``ENCRYPTION_SKILL_MARK``),
    остальные требуемые — науки. Опция выключена или кандидатов нет — ``mult=1.0`` (базовый шанс
    SDE). Кэш — в ``params.invention_skill_cache`` (SDE и скиллы в рамках расчёта
    неизменны)."""
    if not params.invention_use_skills:
        return InventionSkills()
    cached = params.invention_skill_cache.get(manuf_bp_type_id)
    if cached is not None:
        return cached
    result = InventionSkills(enabled=True)
    src = invention_source(conn, manuf_bp_type_id)
    if src is not None and params.inventor_skills:
        rows = conn.execute(
            "SELECT bs.skill_type_id, t.name FROM sde_blueprint_skills bs "
            "LEFT JOIN sde_types t ON t.type_id = bs.skill_type_id "
            "WHERE bs.blueprint_type_id = ? AND bs.activity_id = ?",
            (src, INVENTION),
        ).fetchall()
        encryption = [int(r["skill_type_id"]) for r in rows if ENCRYPTION_SKILL_MARK in (r["name"] or "")]
        science = [int(r["skill_type_id"]) for r in rows if ENCRYPTION_SKILL_MARK not in (r["name"] or "")]
        # Детерминированный выбор при равенстве — по имени, затем по id (как список чаров в UI).
        order = sorted(params.inventor_skills,
                       key=lambda c: (params.inventor_names.get(c, ""), c))
        for cid in order:
            levels = params.inventor_skills[cid]
            mult = (1.0 + sum(levels.get(s, 0) for s in encryption) / ENCRYPTION_DIVISOR
                    + sum(levels.get(s, 0) for s in science) / SCIENCE_DIVISOR)
            if result.character_id is None or mult > result.mult:
                result = InventionSkills(mult, cid, params.inventor_names.get(cid), True)
    params.invention_skill_cache[manuf_bp_type_id] = result
    return result


def invention_source(conn: sqlite3.Connection, manuf_bp_type_id: int) -> int | None:
    """T1-чертёж, НА котором запускается инвента этого T2-чертежа (напр. Fenrir для Nomad)."""
    r = conn.execute(
        "SELECT blueprint_type_id FROM sde_blueprint_products "
        "WHERE product_type_id = ? AND activity_id = ?",
        (manuf_bp_type_id, INVENTION),
    ).fetchone()
    return int(r["blueprint_type_id"]) if r else None


def invention_options(
    conn: sqlite3.Connection, manuf_bp_type_id: int, params: cost.BuildParams,
    extra_attempt_cost: float = 0.0,
) -> list[InventionOption]:
    """Все варианты инвенты: без декриптора + с каждым из 8 (у кого есть цена).

    per_run = (Σ датакоры + цена декриптора + ``extra_attempt_cost``) / (вероятность·mult × раны).
    ``extra_attempt_cost`` — стоимость расходуемой за попытку T1-копии (копи-джоб с BPO).
    Декриптор оплачивается на каждую попытку; ME/TE итоговой BPC сдвигаются модификаторами.
    Вероятность = база SDE × множитель скиллов лучшего инвентора (``invention_skills``) ×
    множитель декриптора, не больше 1.0. Без скиллов (опция выкл/нет кандидатов) множитель
    ровно 1.0 — вероятность равна базе SDE × декриптор.
    """
    base = _invention_base(conn, manuf_bp_type_id, params)
    if base is None:
        return []
    attempt, base_prob, runs = base  # attempt = только датакоры
    skills = invention_skills(conn, manuf_bp_type_id, params)
    skilled = base_prob * skills.mult
    prob = min(1.0, skilled)
    datacores = attempt
    attempt += extra_attempt_cost  # + T1-копия и джоб-взнос инвенты (общие для всех вариантов)
    opts = [InventionOption(
        attempt / (prob * runs), INVENT_BASE_ME, INVENT_BASE_TE, runs, None,
        prob=prob, datacores=datacores, decryptor_cost=0.0,
        base_prob=base_prob, skill_mult=skills.mult,
        inventor_id=skills.character_id, inventor_name=skills.character_name,
    )]
    for d in DECRYPTORS:
        dprice = cost.landed_unit_cost(conn, d.type_id, params)
        if dprice is None:
            continue  # нет цены декриптора → вариант недоступен
        r = runs + d.run_mod
        p = min(1.0, skilled * d.prob_mult)
        if r <= 0 or p <= 0:
            continue
        opts.append(InventionOption(
            (attempt + dprice) / (p * r),
            INVENT_BASE_ME + d.me_mod, INVENT_BASE_TE + d.te_mod, r, d.name, d.type_id,
            prob=p, datacores=datacores, decryptor_cost=dprice,
            base_prob=base_prob, skill_mult=skills.mult, decryptor_mult=d.prob_mult,
            inventor_id=skills.character_id, inventor_name=skills.character_name,
        ))
    return opts


@dataclass
class DecryptorChoice:
    """Выбранный вариант инвенты + как он выбран (для пометок в UI/отчёте).

    ``manual`` — вариант задан вручную (режим ``none``/``fixed`` или ``per_product``) и
    применён. ``fallback`` — текст, если заданный вручную декриптор недоступен (нет цены) и
    взят авто-выбор; тогда ``manual=False``."""

    option: InventionOption
    manual: bool = False
    fallback: str | None = None


def decryptor_label(type_id: int) -> str:
    """Короткое имя декриптора («Process»); неизвестный id — «тип N»."""
    d = DECRYPTOR_BY_ID.get(type_id)
    return d.name if d else tr("тип {type_id}", type_id=type_id)


def choose_decryptor(
    options: list[InventionOption],
    product_type_id: int,
    params: cost.BuildParams,
    key,
) -> DecryptorChoice:
    """Выбрать вариант инвенты (декриптор) по настройке ``[invention]`` (``params.decryptor_*``).

    ``options`` — непустой список из ``invention_options`` (первый — «без декриптора», он есть
    всегда); ``key(option) -> float`` — что минимизирует авто-выбор (материалы при ME копии +
    целые попытки инвенты — у вызывающего, ``sourcing.build_node_cost``; одна и та же функция и
    для обычной инвенты, и для закрытия недостачи ранов своей копии).

    Приоритет: оверрайд по T2-продукту ``product_type_id`` → режим ``none``/``fixed`` → авто.
    Авто — минимум ``key`` среди «без декриптора» и разрешённых ``params.decryptor_allowed``
    (пусто — все). Заданный вручную декриптор, которого нет среди ``options`` (нет цены на
    рынке — ``invention_options`` такие не предлагает), — фолбэк на авто с текстом ``fallback``.
    По умолчанию (``auto_cost``, без ограничений и оверрайдов) — просто ``min(options)`` по ``key``.
    """
    auto_pool = [o for o in options
                 if o.decryptor_type_id is None or not params.decryptor_allowed
                 or o.decryptor_type_id in params.decryptor_allowed]

    def auto() -> InventionOption:
        return min(auto_pool or options, key=key)

    target = params.decryptor_per_product.get(product_type_id)
    if target is None:
        if params.decryptor_mode == "none":
            target = 0
        elif params.decryptor_mode == "fixed":
            target = params.decryptor_fixed
        else:
            return DecryptorChoice(auto())
    if not target:
        plain = next((o for o in options if o.decryptor_type_id is None), None)
        if plain is not None:
            return DecryptorChoice(plain, manual=True)
        return DecryptorChoice(auto())  # не бывает: «без декриптора» есть всегда
    chosen = next((o for o in options if o.decryptor_type_id == target), None)
    if chosen is not None:
        return DecryptorChoice(chosen, manual=True)
    fb = auto()
    why = tr("нет цены на рынке") if target in DECRYPTOR_BY_ID else tr("это не декриптор инвенты")
    if fb.decryptor:
        note = tr("декриптор «{target}» задан вручную, но недоступен ({why}) — взят авто-выбор: «{auto}»",
                  target=decryptor_label(target), why=why, auto=fb.decryptor)
    else:
        note = tr("декриптор «{target}» задан вручную, но недоступен ({why}) — взят авто-выбор: "
                  "без декриптора", target=decryptor_label(target), why=why)
    return DecryptorChoice(fb, fallback=note)


def invention_datacore_materials(
    conn: sqlite3.Connection, manuf_bp_type_id: int
) -> list[tuple[int, int]]:
    """[(type_id, кол-во на 1 попытку), ...] датакоров инвенты — сырой список материалов, БЕЗ
    цены (для цены — ``_invention_base``/``invention_options``, где она уже нужна для выбора
    декриптора). Нужен отдельно, чтобы завести датакоры как настоящие ``MaterialLine`` (список
    закупок + проверка остатков на складе GPLB-C), а не только как абстрактную ISK-сумму внутри
    ``blueprint_cost`` — см. ``sourcing.build_node_cost``."""
    inv = conn.execute(
        "SELECT blueprint_type_id FROM sde_blueprint_products WHERE product_type_id = ? AND activity_id = ?",
        (manuf_bp_type_id, INVENTION),
    ).fetchone()
    if inv is None:
        return []
    mats = conn.execute(
        "SELECT material_type_id, quantity FROM sde_blueprint_materials "
        "WHERE blueprint_type_id = ? AND activity_id = ?",
        (inv["blueprint_type_id"], INVENTION),
    ).fetchall()
    return [(m["material_type_id"], m["quantity"]) for m in mats]


def blueprint_cost(
    conn: sqlite3.Connection,
    product_type_id: int,
    manuf_bp_type_id: int,
    activity_id: int,
    runs: int,
    params: cost.BuildParams,
    streams_needed: int = 1,
) -> BlueprintCost:
    """Стоимость чертежа узла стройки по политике владение → инвента → оверрайд/флаг.

    Реакции сами по себе не стоят ISK (формула не расходуется), но физически нужна
    отдельная копия формулы на КАЖДЫЙ параллельный джоб — ``streams_needed`` (число
    одновременных потоков ЭТОГО узла) сверяется с ``reaction_formula_copies``; нехватка
    флагается ``missing_reaction`` (см. ``sourcing.build_node_cost``, откуда пробрасывается
    фактическое число параллельных джобов после разбивки на потоки).

    Не-реакция, владение через КОПИЮ (не оригинал): копия физически ограничена остатком
    ранов — сверяем его с ``runs`` (сколько прогонов реально нужно этому узлу целиком, не
    ``streams_needed``: раны копии тратятся суммарно по всем джобам, а не по параллельным
    потокам отдельно). Не хватает — флагаем ``owned_bpc_insufficient`` (стоимость всё равно
    0.0, копия — sunk cost; это только предупреждение, как и ``missing_reaction``)."""
    # Ручной оверрайд имеет приоритет (per run продукта) — действует и для реакций.
    override = params.blueprint_overrides.get(product_type_id)
    if override is not None:
        return BlueprintCost(override, override * runs, "manual")

    if activity_id == REACTION:
        owned = reaction_formula_copies(conn, manuf_bp_type_id, params.blueprint_location_ids)
        needed = max(1, streams_needed)
        source = "owned_bpo" if owned >= needed else "missing_reaction"
        return BlueprintCost(0.0, 0.0, source, reaction_owned=owned, reaction_needed=needed)

    kind = owned_kind(conn, manuf_bp_type_id, params.blueprint_location_ids)
    if kind == "bpo":
        return BlueprintCost(0.0, 0.0, "owned_bpo")
    if kind == "bpc":
        available = owned_bpc_total_runs(conn, manuf_bp_type_id, params.blueprint_location_ids)
        if available < runs:
            return BlueprintCost(
                0.0, 0.0, "owned_bpc_insufficient", bpc_runs_owned=available, bpc_runs_needed=runs
            )
        return BlueprintCost(0.0, 0.0, "owned_bpc")

    inv = invention_per_run(conn, manuf_bp_type_id, params)
    if inv is not None:
        return BlueprintCost(inv, inv * runs, "invention")

    return BlueprintCost(0.0, 0.0, "missing")
