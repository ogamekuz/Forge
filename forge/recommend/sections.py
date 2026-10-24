"""Разделы рынка EVE для вкладки «Что строить» — разбивка «как на маркете» (без сети).

Дерево строится из ``sde_market_groups`` и содержит ТОЛЬКО разделы, в которых есть производимые
предметы (продукты производства и реакций), — со счётчиками «по своим чертежам / всего».
Цепочки из одного подраздела без своих предметов склеиваются в один узел («Precursor Frigates ›
Triglavian»), чтобы не щёлкать пустые уровни. Предметы без раздела маркета — узел ``id = 0``.

Тот же объект раскладывает результаты рекомендаций по разделам нужного уровня (1 — Ships,
2 — Frigates, 3 — Standard Frigates / Advanced Frigates) и даёт короткую подпись «Раздел».
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..i18n import N_, tr

NO_SECTION = 0
NO_SECTION_NAME = N_("Без раздела маркета")


@dataclass
class Section:
    id: int                    # market_group_id (нижний в склеенной цепочке); 0 — без раздела
    name: str                  # имя узла; склеенная цепочка — «A › B»
    chain: list[int]           # все market_group_id, слитые в этот узел, сверху вниз
    parent: int | None         # id родительского Section; None — корень
    depth: int                 # 1 — корень
    order: int = 0             # порядок обхода дерева (дети — по имени, как на маркете)
    own: int = 0               # производимых по своим чертежам (вместе с подразделами)
    total: int = 0             # всего производимых (вместе с подразделами)
    children: list[Section] = field(default_factory=list)


class SectionTree:
    def __init__(self, roots: list[Section], of_group: dict[int, int], ready: bool):
        self.roots = roots
        self.ready = ready                     # дерево рынка в БД есть (SDE перезалит с v7)
        self._of_group = of_group              # market_group_id (любой из цепочки) → Section.id
        self.by_id: dict[int, Section] = {}
        for s in self.walk():
            self.by_id[s.id] = s

    # ---------------------------------------------------------------- построение
    @classmethod
    def load(cls, conn: sqlite3.Connection, own_ids: Iterable[int] = ()) -> SectionTree:
        """Дерево по всем производимым предметам; ``own_ids`` — продукты своих чертежей (для
        счётчика «свои»)."""
        groups = {int(r["market_group_id"]): (r["parent_group_id"], r["name"] or "")
                  for r in conn.execute("SELECT market_group_id, parent_group_id, name FROM sde_market_groups")}
        own = {int(x) for x in own_ids}
        direct_total: dict[int, int] = defaultdict(int)
        direct_own: dict[int, int] = defaultdict(int)
        for r in conn.execute(
            "SELECT DISTINCT p.product_type_id AS tid, t.market_group_id AS mg "
            "FROM sde_blueprint_products p LEFT JOIN sde_types t ON t.type_id = p.product_type_id "
            "WHERE p.activity_id IN (1, 11)"
        ):
            mg = int(r["mg"]) if r["mg"] is not None and int(r["mg"]) in groups else NO_SECTION
            direct_total[mg] += 1
            if int(r["tid"]) in own:
                direct_own[mg] += 1

        def parent_of(gid: int) -> int | None:
            p = groups[gid][0]
            return int(p) if p is not None and int(p) in groups else None

        used: set[int] = set()
        for gid in direct_total:
            cur: int | None = gid if gid != NO_SECTION else None
            while cur is not None and cur not in used:
                used.add(cur)
                cur = parent_of(cur)
        kids: dict[int | None, list[int]] = defaultdict(list)
        for gid in used:
            kids[parent_of(gid)].append(gid)
        for lst in kids.values():
            lst.sort(key=lambda g: groups[g][1].lower())

        of_group: dict[int, int] = {}
        counter = [0]

        def build(gid: int, parent: int | None, depth: int) -> Section:
            chain, names = [gid], [groups[gid][1]]
            cur = gid
            while len(kids.get(cur, ())) == 1 and not direct_total.get(cur):
                cur = kids[cur][0]
                chain.append(cur)
                names.append(groups[cur][1])
            sec = Section(id=cur, name=" › ".join(names), chain=chain, parent=parent, depth=depth,
                          order=counter[0])
            counter[0] += 1
            for g in chain:
                of_group[g] = cur
            sec.own = sum(direct_own.get(g, 0) for g in chain)
            sec.total = sum(direct_total.get(g, 0) for g in chain)
            for k in kids.get(cur, ()):
                child = build(k, cur, depth + 1)
                sec.children.append(child)
                sec.own += child.own
                sec.total += child.total
            return sec

        roots = [build(g, None, 1) for g in kids.get(None, ())]
        if direct_total.get(NO_SECTION):
            roots.append(Section(id=NO_SECTION, name=tr(NO_SECTION_NAME), chain=[NO_SECTION], parent=None,
                                 depth=1, order=counter[0], own=direct_own.get(NO_SECTION, 0),
                                 total=direct_total[NO_SECTION]))
        return cls(roots, of_group, ready=bool(groups))

    # ---------------------------------------------------------------- запросы
    def walk(self) -> Iterable[Section]:
        stack = list(reversed(self.roots))
        while stack:
            s = stack.pop()
            yield s
            stack.extend(reversed(s.children))

    def section_of(self, market_group_id: int | None) -> Section | None:
        """Узел дерева, в котором лежит предмет с этим ``market_group_id``."""
        if market_group_id is None:
            return self.by_id.get(NO_SECTION)
        sid = self._of_group.get(int(market_group_id))
        return self.by_id.get(sid if sid is not None else NO_SECTION)

    def path(self, market_group_id: int | None) -> list[Section]:
        """Путь от корня до узла предмета (пусто — предмета нет в дереве)."""
        out: list[Section] = []
        s = self.section_of(market_group_id)
        while s is not None:
            out.append(s)
            s = self.by_id.get(s.parent) if s.parent is not None else None
        return list(reversed(out))

    def at_level(self, market_group_id: int | None, level: int) -> list[Section]:
        """Путь до раздела уровня ``level`` (короче — если предмет лежит выше)."""
        return self.path(market_group_id)[:max(1, level)]

    def label(self, market_group_id: int | None) -> tuple[str, str]:
        """Подпись колонки «Раздел»: (коротко — 2-й и 3-й уровни, «Frigates › Standard Frigates»;
        полный путь — для подсказки)."""
        p = self.path(market_group_id)
        if not p:
            return "", ""
        full = " › ".join(s.name for s in p)
        short = " › ".join(s.name for s in (p[1:3] if len(p) > 1 else p))
        return short, full


def market_groups_of(conn: sqlite3.Connection, type_ids: Iterable[int]) -> dict[int, int | None]:
    """``type_id → market_group_id`` для подписи/разбивки результатов."""
    ids = sorted({int(t) for t in type_ids})
    out: dict[int, int | None] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
            f"SELECT type_id, market_group_id FROM sde_types WHERE type_id IN ({','.join('?' for _ in chunk)})",
            chunk,
        ):
            out[int(r["type_id"])] = int(r["market_group_id"]) if r["market_group_id"] is not None else None
    return out
