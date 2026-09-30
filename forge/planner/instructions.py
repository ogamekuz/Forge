"""Пошаговые инструкции по персонажам из расписания (без сети).

Превращает ``Schedule`` (раскладку джобов по чарам/слотам/времени) в упорядоченный по
каждому персонажу чек-лист: что и когда запускать. Это презентационная надстройка над
``planner.schedule`` — числа уже посчитаны, тут только формулировки, группировка и
проставление плановых дат от точки старта. Чистая функция, БД/сеть не трогает.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..core.bom import REACTION
from ..core.cost import COPYING, INVENTION
from ..i18n import N_, tr
from .schedule import Schedule, Scheduled, job_resource_name

# Те же цвета, что лэйны Gantt в пульте (forge/desktop — палитра EVE: cyan «Photon», золото
# Omega, зелень Gallente, пурпур Sansha, ржавчина Minmatar, …) — чтобы отчёт совпадал.
GANTT_COLORS = ["#3fb8e0", "#e8a93a", "#4cc38a", "#a68bf0", "#e0706a", "#39c2b4", "#d6c75a", "#6f95e8"]


@dataclass
class Step:
    step_id: int          # = job_id (стабилен для чекбоксов и связи с таблицей контроля)
    order: int
    pool: str             # 'manufacturing' | 'reaction' | 'science'
    slot_label: str       # 'пр#1' / 'рк#1' / 'нк#1'
    text: str
    item_name: str
    runs: int
    te: int
    activity_id: int
    start_s: float
    end_s: float
    duration_s: float
    planned_start: str    # ISO
    planned_end: str      # ISO


@dataclass
class CharacterPlan:
    character_id: int
    name: str
    color: str
    steps: list[Step] = field(default_factory=list)


def _verb(activity_id: int) -> str:
    if activity_id == REACTION:
        return tr("Запусти реакцию")
    if activity_id == INVENTION:
        return tr("Запусти инвенту")
    if activity_id == COPYING:
        return tr("Запусти копирование")
    return tr("Запусти производство")


def _what(it: Scheduled) -> str:
    """«Имя ×прогонов»; для копи-джоба — «T1-чертёж — N коп. × M прог.» (копий и ранов на копию)."""
    if it.activity_id == COPYING:
        return tr("{name} — {copies} коп. × {runs} прог.", name=job_resource_name(it.name),
                  copies=it.runs, runs=it.copy_runs)
    return f"{it.name} ×{it.runs}"


# Короткие метки пулов слотов: производство / реакции / наука.
_POOL_SHORT = {"manufacturing": N_("пр"), "reaction": N_("рк"), "science": N_("нк")}


def _slot_label(pool: str, slot: int) -> str:
    label = tr(_POOL_SHORT.get(pool, _POOL_SHORT["manufacturing"]))
    return f"{label}#{slot + 1}"


def build_character_instructions(
    schedule: Schedule, start_dt: datetime, site: str = "GPLB-C"
) -> list[CharacterPlan]:
    """Сгруппировать джобы по персонажам и упорядочить по времени старта в чек-лист шагов.

    ``start_dt`` — момент, от которого отсчитываются плановые даты (обычно «сейчас»).
    ``site`` — где стройка (имя локации стройки из конфига, ``[locations.gplb_c] name``).
    Порядок персонажей и цвет совпадают с лэйнами Gantt (по первому появлению в расписании).
    """
    order_names: list[str] = []
    for it in schedule.items:
        if it.character_name not in order_names:
            order_names.append(it.character_name)

    plans: dict[int, CharacterPlan] = {}
    for it in schedule.items:
        cp = plans.get(it.character_id)
        if cp is None:
            color = GANTT_COLORS[order_names.index(it.character_name) % len(GANTT_COLORS)]
            cp = CharacterPlan(it.character_id, it.character_name, color)
            plans[it.character_id] = cp
        slot_label = _slot_label(it.pool, it.slot)
        start = start_dt + timedelta(seconds=it.start)
        end = start_dt + timedelta(seconds=it.end)
        cp.steps.append(Step(
            step_id=it.job_id, order=0, pool=it.pool, slot_label=slot_label,
            text=tr("{verb}: {what} (слот {slot}, TE {te}, {site})", verb=_verb(it.activity_id),
                    what=_what(it), slot=slot_label, te=it.te, site=site),
            item_name=it.name, runs=it.runs, te=it.te, activity_id=it.activity_id,
            start_s=it.start, end_s=it.end, duration_s=it.end - it.start,
            planned_start=start.isoformat(timespec="minutes"),
            planned_end=end.isoformat(timespec="minutes"),
        ))

    out: list[CharacterPlan] = []
    for name in order_names:
        cp = next((p for p in plans.values() if p.name == name), None)
        if cp is None:
            continue
        cp.steps.sort(key=lambda s: (s.start_s, s.step_id))
        for i, st in enumerate(cp.steps, 1):
            st.order = i
        out.append(cp)
    return out
