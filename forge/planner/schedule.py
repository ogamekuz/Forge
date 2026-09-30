"""Извлечение джобов из дерева постройки и их раскладка по слотам/чарам (без сети).

Джобы строятся из дерева core.NodeResult: каждый «строить»-узел даёт по джобу на поток,
с зависимостью от джобов своих под-компонентов. Раскладка — жадная: каждый готовый джоб
идёт в самый ранний свободный слот подходящего персонажа (владельца чертежа).

Инвента (activity_id=INVENTION) — отдельный джоб (не из ``node.lines``, а из
``node.invention_breakdown``/``node.invention_source_id``, когда ``node.blueprint_source ==
"invention"``): она тоже занимает джоб-слот и требует времени (SDE-время T1-источника на
активности INVENTION), но не является материальным под-компонентом дерева — джоб продукта
ждёт её явно, отдельно от child_ids материалов.

Копирование T1-чертежа (activity_id=COPYING, опция ``[planner] schedule_copy_jobs``) — перед
инвентой, когда T1-копия получается копи-джобом со СВОЕГО BPO (``node.invention_t1_kind ==
"bpo"``). Модель EVE, принятая здесь: каждый прогон инвенты съедает прогон T1-копии, а раны
копии на результат инвенты не влияют — поэтому копии «минимальные»: на каждый джоб инвенты из
r прогонов — одна копия на r прогонов (6 инвент-джобов по 1 прогону = 6 копий по 1 прогону,
одним копи-джобом на 6 копий). Один копи-джоб делает копии с ОДИНАКОВЫМИ ранами — поэтому
джобы группируются по r. Один BPO одновременно — только в одном джобе: копий делится не больше
чем на число своих BPO копи-джобов, а в ``schedule_jobs`` копи-джобы одного T1-чертежа не идут
параллельно больше, чем есть BPO. Время копирования — ``timing.job_seconds`` (без TE).
Упрощение: свои готовые T1-BPC не вычитаются (как и в себестоимости — копия считается
копи-джобом на каждую попытку).
"""

from __future__ import annotations

import itertools
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import Config
from ..core import build_params_from_config, prices
from ..core import cost as ccost
from ..core.blueprint import owned_bpo_count
from ..core.cost import COPYING, INVENTION, split_runs
from ..core.sourcing import NodeResult
from ..i18n import EN, N_, tr
from . import slots, timing


def _facility_time_mult(params, conn: sqlite3.Connection, activity_id: int, product_type_id: int) -> float:
    """Множитель времени станции под джоб (по активности и группе/категории продукта)."""
    return ccost.facility_mults(
        params, activity_id, prices.group_id(conn, product_type_id),
        prices.category_id(conn, product_type_id),
    ).time_mult

# Потолок числа потоков на один узел при дроблении по сроку — предохранитель, чтобы огромные
# объёмы не порождали сотни тысяч джобов (используется в planner.deadline_substreams).
MAX_STREAMS_PER_NODE = 64

# Имена служебных джобов — на языке пульта в момент планирования: «Копия: <T1-чертёж>»,
# «Инвента: <T2>», «Инвента (недостача): <T2>». Отличать джобы — по ``activity_id``, а имя
# предмета без префикса брать через ``job_resource_name`` (понимает оба языка).
COPY_JOB_NAME = N_("Копия: {name}")
INVENTION_JOB_NAME = N_("Инвента: {name}")
SHORTFALL_JOB_NAME = N_("Инвента (недостача): {name}")
# Заметка о запущенных джобах — справка, не предупреждение (пульт узнаёт её по началу текста).
RUNNING_JOBS_NOTE = N_("Учтены запущенные джобы ({busy}): их слоты заняты до окончания — "
                       "новые джобы встают после них.")


def job_resource_name(name: str) -> str:
    """Имя предмета/чертежа из имени джоба без служебного префикса («Копия: X» → «X»,
    «Invention: X» → «X») — на любом языке, чем бы ни было создано имя (отчёт мог быть
    сформирован до переключения RUS/ENG)."""
    for tmpl in (SHORTFALL_JOB_NAME, INVENTION_JOB_NAME, COPY_JOB_NAME):
        for t in (tmpl, EN.get(tmpl, tmpl)):
            prefix = t.split("{name}")[0]
            if name.startswith(prefix):
                return name[len(prefix):]
    return name


@dataclass
class Job:
    id: int
    product_type_id: int
    blueprint_type_id: int
    activity_id: int
    name: str
    runs: int
    deps: frozenset[int]
    # TE, который ОБЯЗАН использовать планировщик (не выводить самому) — см.
    # sourcing.NodeResult.resulting_te: для invention — TE итоговой BPC, для manual/missing —
    # default_te. None — обычный случай (владение), TE решает schedule_jobs по кандидату.
    resulting_te: int | None = None
    # Только копи-джоб (activity_id=COPYING): ``runs`` — сколько копий, ``copy_runs`` — прогонов
    # на каждой (время копирования ∝ копий × прогонов). Для прочих джобов не используется.
    copy_runs: int = 1


@dataclass
class Scheduled:
    job_id: int
    product_type_id: int
    name: str
    activity_id: int
    runs: int
    character_id: int
    character_name: str
    pool: str
    slot: int
    start: float
    end: float
    te: int
    # Владение чертежом джоба (формулой, T1-источником инвенты): "owner" — назначенный персонаж
    # им владеет, "transfer" — нужна передача от владельца, "" — владельцев нет (напр. BPC ещё
    # только будет заинвентирован). ``owners`` — имена владельцев (для подсказки Gantt).
    owner_status: str = ""
    owners: tuple[str, ...] = ()
    copy_runs: int = 1  # копи-джоб: прогонов на каждой копии (``runs`` — число копий)


@dataclass
class Schedule:
    items: list[Scheduled] = field(default_factory=list)
    makespan: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def transfer_jobs(self) -> int:
        """Сколько джобов назначено НЕ владельцу чертежа (нужна передача)."""
        return sum(1 for it in self.items if it.owner_status == "transfer")


def extract_jobs(
    node: NodeResult, conn: sqlite3.Connection | None = None, *, copy_jobs: bool = False
) -> list[Job]:
    """Плоский список джобов (пост-порядок: компоненты раньше родителя) с зависимостями, для
    ОДНОГО корневого дерева. См. ``extract_jobs_many`` — то же самое для НЕСКОЛЬКИХ деревьев
    сразу (нужно, если общий под-компонент делится между РАЗНЫМИ товарами корзины)."""
    return extract_jobs_many([node], conn, copy_jobs=copy_jobs)


def plan_copy_jobs(inv_runs: list[int], bpos: int) -> list[tuple[int, int, list[int]]]:
    """Копи-джобы под разбивку инвенты ``inv_runs`` (прогонов в каждом джобе инвенты): список
    ``(копий, прогонов на копию, индексы джобов инвенты, которым они)``.

    Каждому джобу инвенты из r прогонов — одна копия на r прогонов (прогон инвенты съедает
    прогон копии, раны копии на результат не влияют — лишних не делаем). Копи-джоб выпускает
    копии с одинаковыми ранами → группируем джобы инвенты по r; группу делим на не больше
    ``bpos`` копи-джобов (один BPO одновременно — только в одном джобе)."""
    by_runs: dict[int, list[int]] = {}
    for idx, r in enumerate(inv_runs):
        by_runs.setdefault(r, []).append(idx)
    out: list[tuple[int, int, list[int]]] = []
    for r, idxs in by_runs.items():
        parts = split_runs(len(idxs), max(1, min(bpos, len(idxs))))
        pos = 0
        for n_copies in parts:
            out.append((n_copies, r, idxs[pos:pos + n_copies]))
            pos += n_copies
    return out


def extract_jobs_many(
    roots: list[NodeResult], conn: sqlite3.Connection | None = None, *, copy_jobs: bool = False
) -> list[Job]:
    """Как ``extract_jobs``, но для НЕСКОЛЬКИХ корневых деревьев сразу, с ОБЩЕЙ мемоизацией
    между ними.

    Число потоков каждого узла берётся из дерева core (``node.streams``): верхний продукт —
    по явным ``streams``, под-компоненты — по разбивке, заданной при расчёте (напр. под срок,
    см. ``planner.deadline_substreams``). Так расписание и материалы используют одну разбивку.

    ``conn`` — опционален; если передан И узел получен инвентой (``blueprint_source ==
    "invention"``), добавляется ОТДЕЛЬНЫЙ джоб инвенты (роль ``science``, activity_id=
    INVENTION, runs=ceil(попыток) из ``invention_breakdown["attempts"]``), от которого зависит
    джоб постройки продукта. Без ``conn`` инвента не планируется как джоб, только учитывается
    в стоимости.

    После ``sourcing.consolidate_shared_components`` дерево может быть DAG: НЕСКОЛЬКО строк
    (из разных родителей, в т.ч. из РАЗНЫХ корней ``roots`` — общий под-компонент двух разных
    товаров корзины) указывают на ОДИН и тот же общий ``NodeResult``. ``visit()`` мемоизирован
    по ``id(n)`` ОБЩО для ВСЕХ ``roots`` — общий узел получает джоб(ы) РОВНО ОДИН раз, а все
    родители, что на него ссылаются (даже из разных корней), зависят от ТЕХ ЖЕ id. При
    ОТДЕЛЬНОМ ``extract_jobs`` на каждый корень (своя мемоизация у каждого вызова) общий узел
    получил бы ПОЛНЫЙ джоб (не долю) от КАЖДОГО корня, а ``consolidate_jobs`` затем СЛОЖИЛ бы
    эти дублирующиеся runs, задваивая количество прогонов сверх уже объединённого спроса
    (пример: Hexite, общий у Sylramic Fibers и Ferrogel, — 98 прогонов вместо верных 49).

    ``copy_jobs`` (``[planner] schedule_copy_jobs``) — перед инвентой, чья T1-копия получается
    копи-джобом со своего BPO (``node.invention_t1_kind == "bpo"``), ставятся копи-джобы
    (``plan_copy_jobs``; activity_id=COPYING, пул «наука», имя «Копия: <T1-чертёж>»), и каждый
    джоб инвенты зависит от копи-джоба своей копии. По умолчанию — без них."""
    jobs: list[Job] = []
    counter = itertools.count()
    memo: dict[int, list[int]] = {}

    def invention_jobs(n: NodeResult, attempts: int, name_tmpl: str) -> list[int]:
        """Джобы инвенты узла (параллельные потоки по попыткам) + их копи-джобы при
        ``copy_jobs``. Возвращает id джобов инвенты."""
        assert conn is not None and n.invention_source_id is not None
        inv_runs = split_runs(attempts, min(attempts, MAX_STREAMS_PER_NODE))
        copy_of: dict[int, int] = {}   # индекс джоба инвенты → id копи-джоба его копии
        if (copy_jobs and n.invention_t1_kind == "bpo"
                and timing.base_time_per_run(conn, n.invention_source_id, COPYING) > 0):
            src_name = prices.type_name(conn, n.invention_source_id)
            for n_copies, copy_runs, idxs in plan_copy_jobs(inv_runs, n.invention_t1_bpos):
                cid = next(counter)
                jobs.append(Job(cid, n.product_type_id, n.invention_source_id, COPYING,
                                tr(COPY_JOB_NAME, name=src_name), n_copies, frozenset(),
                                copy_runs=copy_runs))
                for i in idxs:
                    copy_of[i] = cid
        ids: list[int] = []
        for i, r in enumerate(inv_runs):
            jid = next(counter)
            deps = frozenset({copy_of[i]}) if i in copy_of else frozenset()
            jobs.append(Job(jid, n.product_type_id, n.invention_source_id, INVENTION,
                            tr(name_tmpl, name=n.name), r, deps))
            ids.append(jid)
        return ids

    def visit(n: NodeResult) -> list[int]:
        key = id(n)
        if key in memo:
            return memo[key]  # общий узел (DAG) — джоб(ы) уже созданы при первом посещении
        child_ids: list[int] = []
        for line in n.lines:
            if line.child is not None:
                child_ids += visit(line.child)

        if n.activity_id == 0 or n.blueprint_type_id == 0:
            # Лист/покупное (без lines — child_ids и так []) ИЛИ узел-обёртка переработки
            # (core.sourcing._try_reprocess_node, blueprint_source='reprocess' — сама
            # переработка не джоб и не занимает слот, но её lines ссылаются на РЕАЛЬНУЮ
            # постройку прекурсора, чьи джобы уже собраны выше в child_ids) — прозрачно
            # прокидываем их как СВОИ id, никакого джоба для самой обёртки не создаём.
            memo[key] = child_ids
            return child_ids

        inv_dep: list[int] = []
        if (
            conn is not None and n.blueprint_source == "invention"
            and n.invention_breakdown and n.invention_source_id
        ):
            attempts = math.ceil(n.invention_breakdown["attempts"])
            base = timing.base_time_per_run(conn, n.invention_source_id, INVENTION)
            if attempts > 0 and base > 0:
                # Попытки инвенты НЕЗАВИСИМЫ (отдельная T1-копия на каждую, без зависимости
                # друг от друга) — незачем гнать их ОДНИМ последовательным джобом (напр. 6
                # попыток Nomad — 1 джоб на всё), когда в пуле "science" несколько
                # слотов/чаров. Дробим на параллельные потоки (как и обычные джобы —
                # ``split_runs``), чтобы инвента не висела на критическом пути дольше
                # необходимого; постройка продукта всё равно ждёт ВСЕ потоки инвенты разом
                # (зависит от каждого id в ``inv_dep``, не только от последнего).
                inv_dep += invention_jobs(n, attempts, INVENTION_JOB_NAME)
        elif (
            conn is not None and n.blueprint_source == "owned_bpc_insufficient"
            and n.bpc_shortfall_invention_attempts and n.invention_source_id
        ):
            # Недостачу ранов своей копии закрывает ОТДЕЛЬНАЯ, НЕ блокирующая инвента — в
            # отличие от обычной инвенты (ВСЕ прогоны ждут новую BPC), тут часть прогонов уже
            # можно строить с СУЩЕСТВУЮЩЕЙ копией прямо сейчас (bpc_runs_owned > 0), поэтому
            # джоб продукта НЕ зависит от этого джоба (в inv_dep не попадает) — просто
            # появляется в расписании/контроле стоимости, чтобы быть видимым и учтённым (юзер:
            # «почему в контроле сроков и стоимости нет инвентов», проверял на Multispectrum
            # Shield Hardener II — недостача 4 из 5 ранов).
            attempts = math.ceil(n.bpc_shortfall_invention_attempts)
            base = timing.base_time_per_run(conn, n.invention_source_id, INVENTION)
            if attempts > 0 and base > 0:
                invention_jobs(n, attempts, SHORTFALL_JOB_NAME)

        deps = frozenset(child_ids + inv_dep)
        my_ids: list[int] = []
        for r in split_runs(n.runs, n.streams):
            jid = next(counter)
            jobs.append(
                Job(jid, n.product_type_id, n.blueprint_type_id, n.activity_id, n.name, r, deps,
                    resulting_te=n.resulting_te)
            )
            my_ids.append(jid)
        memo[key] = my_ids
        return my_ids

    for root in roots:
        visit(root)
    return jobs


def consolidate_jobs(job_lists: list[list[Job]]) -> list[Job]:
    """Слить джобы одинаковых компонентов между деревьями корзины в общие джобы.

    ``job_lists`` — джобы каждого продукта отдельным списком (id уже уникальны между
    списками). Джобы с одинаковым ключом ``(product_type_id, blueprint_type_id,
    activity_id)`` объединяются: runs суммируются, спрос дробится на потоки заново
    (число потоков = максимум среди продуктов, чтобы сохранить параллелизм), зависимости
    перешиваются на новые джобы. Так общий компонент для двух предметов строится одними
    джобами — экономия слотов и ME-округления. Возвращает плоский список в пост-порядке.
    """
    def key_of(j: Job) -> tuple[int, int, int, int]:
        # copy_runs — в ключе: копи-джобы с разными ранами на копию не сливаются (у прочих всегда 1)
        return (j.product_type_id, j.blueprint_type_id, j.activity_id, j.copy_runs)

    groups: dict[tuple[int, int, int, int], list[Job]] = {}
    per_product_count: dict[tuple[int, int, int, int], list[int]] = {}
    group_of: dict[int, tuple[int, int, int, int]] = {}
    for jobs in job_lists:
        local: dict[tuple[int, int, int, int], int] = {}
        for j in jobs:
            k = key_of(j)
            groups.setdefault(k, []).append(j)
            group_of[j.id] = k
            local[k] = local.get(k, 0) + 1
        for k, c in local.items():
            per_product_count.setdefault(k, []).append(c)

    # Зависимости на уровне ключей (компонент → набор дочерних ключей).
    key_deps: dict[tuple[int, int, int, int], set[tuple[int, int, int, int]]] = {}
    for k, members in groups.items():
        deps: set[tuple[int, int, int, int]] = set()
        for j in members:
            for d in j.deps:
                if group_of[d] != k:
                    deps.add(group_of[d])
        key_deps[k] = deps

    # Топосортировка ключей: зависимости раньше родителей (нужно для schedule_jobs).
    order: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()

    def visit(k: tuple[int, int, int, int]) -> None:
        if k in seen:
            return
        seen.add(k)
        for dk in key_deps[k]:
            visit(dk)
        order.append(k)

    for k in groups:
        visit(k)

    counter = itertools.count()
    new_ids_of: dict[tuple[int, int, int, int], list[int]] = {}
    new_runs: dict[tuple[int, int, int, int], list[int]] = {}
    for k in order:
        total = sum(j.runs for j in groups[k])
        streams = max(per_product_count[k])
        parts = split_runs(total, streams)
        new_ids_of[k] = [next(counter) for _ in parts]
        new_runs[k] = parts

    out: list[Job] = []
    for k in order:
        tpl = groups[k][0]
        job_deps = frozenset(nid for dk in key_deps[k] for nid in new_ids_of[dk])
        for jid, r in zip(new_ids_of[k], new_runs[k], strict=True):
            out.append(Job(jid, tpl.product_type_id, tpl.blueprint_type_id,
                           tpl.activity_id, tpl.name, r, job_deps, resulting_te=tpl.resulting_te,
                           copy_runs=tpl.copy_runs))
    return out


def _owners(conn: sqlite3.Connection, blueprint_type_id: int, location_ids=None) -> set[int]:
    from ..core.prices import loc_in_clause
    clause, loc_params = loc_in_clause(location_ids)
    return {
        int(r["character_id"])
        for r in conn.execute(
            "SELECT DISTINCT character_id FROM character_blueprints WHERE type_id = ?" + clause,
            (blueprint_type_id, *loc_params),
        )
    }


# ESI activity_id запущенных джобов → пул слотов (реакции в ESI — 9, в SDE — 11).
_REACTION_ACTIVITIES = frozenset({9, 11})
_SCIENCE_ACTIVITIES = frozenset({3, 4, 5, 7, 8})


def pool_for_activity(activity_id: int) -> str:
    if activity_id in _REACTION_ACTIVITIES:
        return "reaction"
    if activity_id in _SCIENCE_ACTIVITIES:
        return "science"
    return "manufacturing"


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def occupy_running_jobs(
    conn: sqlite3.Connection, avail: dict[int, dict[str, list[float]]], now: datetime | None = None
) -> int:
    """Занять слоты уже запущенными джобами (``character_industry_jobs.status = 'active'``) до
    их окончания: слот свободен не с нуля, а с ``end_date − now`` (иначе, пока идут прошлые
    джобы, ETA выходит оптимистичным). Возвращает,
    сколько запущенных джобов учтено (закончившиеся, но не сданные — слот уже свободен)."""
    now = now or datetime.now(UTC)
    used = 0
    for r in conn.execute(
        "SELECT character_id, activity_id, end_date FROM character_industry_jobs WHERE status = 'active'"
    ):
        cid = int(r["character_id"])
        pools = avail.get(cid)
        end = _parse_ts(r["end_date"])
        if pools is None or end is None:
            continue
        left = (end - now).total_seconds()
        slot_list = pools.get(pool_for_activity(int(r["activity_id"] or 0)))
        if left <= 0 or not slot_list:
            continue
        i = min(range(len(slot_list)), key=lambda k: slot_list[k])
        slot_list[i] = max(slot_list[i], left)
        used += 1
    return used


def apply_slot_limits(avail: dict[int, dict[str, list[float]]], cfg: Config) -> None:
    """Оставить Forge не больше лимита слотов (``[planner] slot_limits``, вкладка «Персонажи») в
    каждом пуле персонажа. Вызывать ПОСЛЕ ``occupy_running_jobs``.

    Семантика: слоты по скиллам уже заняты идущими джобами (любыми — и своими, и «чужими»:
    ESI их не различает) до их окончания; Forge достаются ``лимит`` САМЫХ РАНО освобождающихся
    из них — то есть в любой момент Forge занимает min(лимит, свободно по скиллам). Пример:
    свободно 3 из 11 при лимите 5 → 3 слота сразу и ещё 2, как только закончатся два самых ранних
    идущих джоба; остальные 6 слотов — под ресёрч/свои джобы. Лимит 0 — пул пуст: персонаж в нём
    не участвует. Нет лимита (не задан/отрицательный) — все слоты по скиллам."""
    for cid, pools in avail.items():
        for pool, slot_list in pools.items():
            lim = cfg.planner.slot_limit(cid, pool)
            if lim is not None and lim < len(slot_list):
                pools[pool] = sorted(slot_list)[:lim]


OWNER_POLICIES = ("any", "prefer_owner", "owner_only")


def pick_candidate(cands: list[tuple], owners: set[int], policy: str, slack_s: float) -> tuple[tuple, bool]:
    """Выбрать кандидата ``(end, start, cid, name, slot, te)`` под политику владельца чертежа.

    ``any`` — самое раннее окончание (при равенстве — первый в порядке персонажей).
    ``prefer_owner`` — лучший из владельцев, если он заканчивает не позже самого раннего на
    ``slack_s`` секунд, иначе самый ранний. ``owner_only`` — лучший из владельцев всегда.
    Владельцев среди кандидатов нет (или их нет вообще) — самый ранний. Возвращает
    ``(кандидат, owner_missing)`` — второе True, если политика требует владельца, а его нет
    среди назначенных в роли (для явного предупреждения при ``owner_only``)."""
    best = min(cands, key=lambda c: c[0])
    if policy not in ("prefer_owner", "owner_only") or not owners:
        return best, False
    owned = [c for c in cands if c[2] in owners]
    if not owned:
        return best, policy == "owner_only"
    best_owner = min(owned, key=lambda c: c[0])
    if policy == "owner_only" or best_owner[0] <= best[0] + slack_s:
        return best_owner, False
    return best, False


# «Нет назначенных чаров <роли> со слотами для «джоб»» — по пулу (роль в родительном падеже).
_NO_ROLE_CHARS = {
    "manufacturing": N_("Нет назначенных чаров производства со слотами для «{job}»{why}."),
    "reaction": N_("Нет назначенных чаров реакций со слотами для «{job}»{why}."),
    "science": N_("Нет назначенных чаров науки (инвента/копи) со слотами для «{job}»{why}."),
}


def _short_list(names: set[str], limit: int = 6) -> str:
    """Первые ``limit`` имён через запятую + «и ещё N»."""
    head = ", ".join(sorted(names)[:limit])
    if len(names) > limit:
        return tr("{head} и ещё {n}", head=head, n=len(names) - limit)
    return head


def schedule_jobs(conn: sqlite3.Connection, cfg: Config, jobs: list[Job]) -> Schedule:
    """Разложить джобы по слотам персонажей во времени (жадно, с учётом зависимостей).

    Кандидаты — персонажи роли со слотами; выбор — по ``[planner] owner_policy`` (см.
    ``pick_candidate``): любой с самым ранним окончанием (по умолчанию), предпочтительно владелец
    чертежа (в пределах ``owner_slack_hours``) или только владелец (нет его в роли — любой, с
    предупреждением). Владелец — у кого ``job.blueprint_type_id`` (для инвенты — T1-источник,
    для реакций — формула) лежит в «Где искать чертежи»; не владельцу — «нужна передача»."""
    profiles = slots.all_profiles(conn)
    policy = cfg.planner.owner_policy if cfg.planner.owner_policy in OWNER_POLICIES else "any"
    slack_s = max(0.0, float(cfg.planner.owner_slack_hours or 0.0)) * 3600.0
    mfg_set = set(cfg.manufacturing_character_ids)
    rx_set = set(cfg.reaction_character_ids)
    sci_set = set(cfg.science_character_ids)
    any_selection = bool(mfg_set or rx_set or sci_set)

    def role_allowed(cid: int, pool: str) -> bool:
        # Без выбора — участвуют все. С выбором — только из списка своего пула. Наука без
        # своего списка — те же, кто отмечен на производство.
        if not any_selection:
            return True
        if pool == "reaction":
            return cid in rx_set
        if pool == "science":
            return cid in (sci_set or mfg_set)
        return cid in mfg_set

    sched = Schedule()
    if not profiles:
        sched.warnings.append(tr("Нет персонажей в БД — нечего планировать (forge sync character)."))
        return sched

    avail: dict[int, dict[str, list[float]]] = {
        p.character_id: {
            "manufacturing": [0.0] * p.manufacturing,
            "reaction": [0.0] * p.reaction,
            "science": [0.0] * p.science,
        }
        for p in profiles
    }
    if cfg.planner.account_running_jobs:
        busy = occupy_running_jobs(conn, avail)
        if busy:
            sched.warnings.append(tr(RUNNING_JOBS_NOTE, busy=busy))
    # Лимиты слотов Forge — после запущенных джобов (см. apply_slot_limits: Forge получает
    # min(лимит, свободно по скиллам)).
    apply_slot_limits(avail, cfg)
    ind = {p.character_id: slots.skill_level(conn, p.character_id, "Industry") for p in profiles}
    advind = {
        p.character_id: slots.skill_level(conn, p.character_id, "Advanced Industry")
        for p in profiles
    }
    reactind = {
        p.character_id: slots.skill_level(conn, p.character_id, "Reactions") for p in profiles
    }
    science = {p.character_id: slots.skill_level(conn, p.character_id, "Science") for p in profiles}
    params = build_params_from_config(cfg, conn)

    end_of: dict[int, float] = {}
    owners_cache: dict[int, set[int]] = {}
    transfers: set[str] = set()
    no_owner_in_role: set[str] = set()
    names = {p.character_id: p.name for p in profiles}
    # Копи-джобы: один BPO одновременно — только в одном джобе → по T1-чертежу столько
    # «слотов BPO», сколько своих оригиналов (в «Где искать чертежи»); время их освобождения.
    bpo_free: dict[int, list[float]] = {}

    for job in jobs:  # пост-порядок ⇒ зависимости уже посчитаны
        # Инвента и копирование идут в пул "science" (слоты Laboratory Operation) — отдельный от
        # manufacturing/reaction, как в реальной EVE (та же карта, что для запущенных джобов ESI).
        pool = pool_for_activity(job.activity_id)
        est_start = max((end_of[d] for d in job.deps), default=0.0)
        bpo_slots: list[float] | None = None
        if job.activity_id == COPYING:
            bpo_slots = bpo_free.setdefault(job.blueprint_type_id, [0.0] * max(1, owned_bpo_count(
                conn, job.blueprint_type_id, params.blueprint_location_ids)))
            est_start = max(est_start, min(bpo_slots))

        # Распределяем по ВСЕМ чарам роли с слотами (балансировка), не только по владельцу
        # чертежа — формулы/BP при необходимости передаются (см. заметку о передаче).
        eligible = [p for p in profiles if avail[p.character_id][pool] and role_allowed(p.character_id, pool)]
        if not eligible:
            limited = [p.name for p in profiles
                       if role_allowed(p.character_id, pool) and cfg.planner.slot_limit(p.character_id, pool) == 0]
            why = (tr(" — лимит слотов Forge 0 у: {names} (вкладка «Персонажи»)", names=", ".join(limited))
                   if limited else "")
            sched.warnings.append(tr(_NO_ROLE_CHARS.get(pool, _NO_ROLE_CHARS["manufacturing"]),
                                     job=job.name, why=why))
            continue

        owners = owners_cache.setdefault(
            job.blueprint_type_id, _owners(conn, job.blueprint_type_id, params.blueprint_location_ids))
        base = timing.base_time_per_run(conn, job.blueprint_type_id, job.activity_id)
        time_mult = _facility_time_mult(params, conn, job.activity_id, job.product_type_id)
        cands: list[tuple] = []
        for p in eligible:
            cid = p.character_id
            # Инвента не имеет своей TE (см. timing.job_seconds) — не показываем чужой TE
            # T1-источника (введёт в заблуждение, будто он ускоряет джоб инвенты).
            # ``resulting_te`` (см. sourcing.NodeResult) — если задан, ЭТО и есть настоящий TE
            # узла (инвента: TE итоговой BPC от декриптора; нет чертежа вовсе: default_te) —
            # он ОДИНАКОВ для любого кандидата-чара (копии-то ещё нет, владением её не найти).
            # None — обычный случай (чертёж во владении): TE у КАЖДОГО кандидата свой, ищем
            # его лучшую физическую копию.
            # Копирование TE тоже не ускоряет (TE — только производство, см. timing.job_seconds).
            if job.activity_id in (INVENTION, COPYING):
                te = 0
            elif job.resulting_te is not None:
                te = job.resulting_te
            else:
                te = timing.best_owned_te(conn, cid, job.blueprint_type_id, params.blueprint_location_ids)
            spec = timing.specialization_mult(conn, cid, job.blueprint_type_id, job.activity_id)
            # копи-джоб: время ∝ копий × прогонов на копию
            run_units = job.runs * job.copy_runs if job.activity_id == COPYING else job.runs
            dur = timing.job_seconds(
                base, run_units, job.activity_id, te, ind[cid], advind[cid], time_mult, reactind[cid], spec,
                science_level=science[cid],
            )
            slot_list = avail[cid][pool]
            slot_idx = min(range(len(slot_list)), key=lambda i: slot_list[i])
            start = max(est_start, slot_list[slot_idx])
            end = start + dur
            cands.append((end, start, cid, p.name, slot_idx, te))

        best, owner_missing = pick_candidate(cands, owners, policy, slack_s)
        end, start, cid, cname, slot_idx, te = best
        if owner_missing:
            no_owner_in_role.add(job.name)
        owner_status = ""
        if owners:
            owner_status = "owner" if cid in owners else "transfer"
        if owner_status == "transfer":
            transfers.add(job.name)  # назначенный чар не владеет BP/формулой
        avail[cid][pool][slot_idx] = end
        if bpo_slots is not None:  # BPO занят этим копи-джобом до его окончания
            i = min(range(len(bpo_slots)), key=lambda k: bpo_slots[k])
            bpo_slots[i] = end
        end_of[job.id] = end
        sched.items.append(
            Scheduled(job.id, job.product_type_id, job.name, job.activity_id, job.runs,
                      cid, cname, pool, slot_idx, start, end, te, owner_status,
                      tuple(sorted(names.get(o, str(o)) for o in owners)), job.copy_runs)
        )

    if no_owner_in_role:
        sched.warnings.append(tr(
            "«Только владелец»: владельца чертежа нет среди назначенных в роли — поставлено любому "
            "(нужна передача): {jobs}. Назначь владельца в роль (вкладка «Персонажи»).",
            jobs=_short_list(no_owner_in_role)))
    if transfers:
        sched.warnings.append(tr("Нужна передача чертежей/формул между чарами: {jobs}.",
                                 jobs=_short_list(transfers)))
    sched.makespan = max((s.end for s in sched.items), default=0.0)
    return sched
