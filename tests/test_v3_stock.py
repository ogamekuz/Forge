"""Forge 3.0: склад по выбору (системы/локации/исключения/персонажи/флаги/«не трогать»),
справочник локаций и резолв структур через ESI."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from forge import config as config_mod
from forge.core import locations, stock
from forge.ingest.character import structures as structures_ingest
from forge.ingest.esi import ESI_BASE, EsiClient
from forge.web import report

GPLB, CJ, JITA_SYS, OTHER = 30000552, 30000772, 30000142, 30000001
EC, KEEPSTAR, NPC_JITA = 1030000000003, 1030000000002, 60003760

BASE_TOML = """
db_path = "x.db"
manufacturing_character_ids = [7]
[locations.jita]
name = "Jita"
system_id = 30000142
region_id = 10000002
[locations.c_j6mt]
name = "C-J6MT"
system_id = 30000772
region_id = 10000009
[locations.gplb_c]
name = "GPLB-C"
system_id = 30000552
region_id = 10000006
[structures]
gplb_engineering_complex_id = 1030000000003
[[freight_routes]]
from = "c_j6mt"
to = "gplb_c"
mode = "per_m3"
isk_per_m3 = 2.0
[[freight_routes]]
from = "jita"
to = "c_j6mt"
mode = "per_m3"
isk_per_m3 = 1.0
"""


def cfg_with(extra: str = "") -> config_mod.Config:
    return config_mod.loads(BASE_TOML + extra)


def _seed(conn):
    conn.executescript(
        f"""
        INSERT INTO sde_systems(system_id,name,region_id,security) VALUES
            ({GPLB},'GPLB-C',10000006,-0.3),({CJ},'C-J6MT',10000009,-0.2),
            ({JITA_SYS},'Jita',10000002,0.9),({OTHER},'Tanoo',10000001,0.8);
        INSERT INTO sde_stations(station_id,name,system_id) VALUES ({NPC_JITA},'Jita IV - Moon 4',{JITA_SYS});
        INSERT INTO universe_structures(structure_id,name,solar_system_id,type_id,status,updated_at) VALUES
            ({EC},'GPLB-C - Engineering',{GPLB},35827,'ok','2026-09-28'),
            ({KEEPSTAR},'C-J6MT - 1st Taj Mahgoon',{CJ},35834,'ok','2026-09-28');
        INSERT INTO sde_categories(category_id,name) VALUES (4,'Material'),(6,'Ship'),(7,'Module'),(2,'Celestial');
        INSERT INTO sde_groups(group_id,category_id,name) VALUES
            (18,4,'Mineral'),(25,6,'Frigate'),(77,7,'Shield Hardener'),(448,2,'Audit Log Container');
        INSERT INTO sde_types(type_id,name,group_id,category_id,volume) VALUES
            (34,'Tritanium',18,4,0.01),(35,'Pyerite',18,4,0.01),(3465,'Large Secure Container',448,2,65.0),
            (587,'Rifter',25,6,27289.0),(2281,'Multispectrum Shield Hardener II',77,7,5.0);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor'),(8,'Alt');
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (900,7,34,{EC},100),
            (901,7,3465,{EC},1),
            (902,7,35,901,50),
            (903,8,34,{KEEPSTAR},40),
            (904,7,34,{NPC_JITA},1000),
            (905,7,587,{EC},1),
            (906,7,2281,905,1),
            (907,7,35,905,5);
        INSERT INTO character_asset_flags(item_id,character_id,location_flag) VALUES
            (900,7,'Hangar'),(901,7,'Hangar'),(902,7,'Unlocked'),(903,8,'Hangar'),(904,7,'Hangar'),
            (905,7,'Hangar'),(906,7,'HiSlot0'),(907,7,'Cargo');
        """
    )
    conn.commit()


CUSTOM = f"""
[stock]
mode = "custom"
system_ids = [{GPLB}]
location_ids = [{KEEPSTAR}]
"""


def test_custom_stock_by_system_and_location(conn):
    _seed(conn)
    view = stock.stock_view(conn, cfg_with(CUSTOM))
    assert view.totals[34] == 140          # 100 в GPLB-C (вся система) + 40 в Keepstar; Jita не выбрана
    assert view.totals[35] == 55           # 50 в контейнере + 5 в трюме корабля
    assert view.totals[587] == 1           # корпус в ангаре — склад (по умолчанию)
    assert 2281 not in view.totals         # модуль в слоте фита — не склад
    lots = view.lots[34]
    assert [lot.hub for lot in lots] == [stock.HUB_BUILD, stock.HUB_MARKET]  # сначала местное
    assert lots[1].root_id == KEEPSTAR


@pytest.mark.parametrize(("extra", "tid", "expected"), [
    ("exclude_location_ids = [901]\n", 35, 5),          # исключили контейнер — и его содержимое
    ("character_ids = [8]\n", 34, 40),                  # только ассеты персонажа 8
    ('exclude_flags = ["Cargo"]\n', 35, 50),            # трюм кораблей — не склад
    ("exclude_type_ids = [35]\n", 35, None),            # «запрет» на тип
    ("exclude_group_ids = [18]\n", 34, None),           # «запрет» на группу
    ("exclude_assembled_ships = true\n", 587, None),    # собранный Rifter (с фитом внутри)
    ("exclude_fitted = false\n", 2281, 1),              # фит можно разрешить явно
])
def test_custom_stock_exclusions(conn, extra, tid, expected):
    _seed(conn)
    view = stock.stock_view(conn, cfg_with(CUSTOM + extra))
    assert view.totals.get(tid) == expected


def test_keep_reserve_comes_from_local_lots_first(conn):
    _seed(conn)
    view = stock.stock_view(conn, cfg_with(CUSTOM + "keep = [{type_id = 34, quantity = 120}]\n"))
    assert view.totals[34] == 20
    assert view.kept[34] == 120
    assert [(lot.root_id, lot.quantity) for lot in view.lots[34]] == [(KEEPSTAR, 20)]


def test_auto_mode_matches_structures_and_nested_items(conn):
    _seed(conn)
    view = stock.stock_view(conn, cfg_with())
    assert view.totals[34] == 100          # только EC из [structures]; Keepstar/Jita — нет
    assert view.totals[35] == 55
    assert stock.on_hand(conn, cfg_with()) == view.totals


def test_empty_selection_means_empty_stock(conn):
    _seed(conn)
    assert stock.on_hand(conn, cfg_with('[stock]\nmode = "custom"\n')) == {}


def test_subtract_reserved():
    assert stock.subtract_reserved({34: 10, 35: 5}, {34: 4, 99: 1}) == {34: 6, 35: 5}


def test_location_resolver_describes_everything(conn):
    _seed(conn)
    cfg = cfg_with()
    res = locations.LocationResolver(conn, cfg)
    st = res.describe(NPC_JITA)
    assert (st.kind, st.system_name) == ("station", "Jita")
    ks = res.describe(KEEPSTAR)
    assert (ks.kind, ks.name, ks.system_id) == ("structure", "C-J6MT - 1st Taj Mahgoon", CJ)
    box = res.describe(901)
    assert box.kind == "container" and box.label == "Large Secure Container · GPLB-C - Engineering"
    assert box.system_id == GPLB and box.root_id == EC
    unknown = res.describe(1049999999999)
    assert unknown.kind == "unknown" and not unknown.resolved


def test_config_hint_labels_unresolved_structure(conn):
    _seed(conn)
    cfg = cfg_with("[[facilities]]\nname = \"Моя Азбель\"\nrole = \"manufacturing\"\nlocation_id = 1049000000001\n")
    info = locations.LocationResolver(conn, cfg).describe(1049000000001)
    assert info.name == "Моя Азбель" and info.system_id == GPLB   # facility = место стройки


def test_expand_with_containers_finds_nested_blueprint_locations(conn):
    _seed(conn)
    ids = locations.expand_with_containers(conn, [EC])
    assert {EC, 901, 905}.issubset(ids)
    assert NPC_JITA not in ids


# --------------------------------------------------------------------- ESI: структуры

def _esi(handler) -> EsiClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE)
    return EsiClient(client=http, sleep=lambda _s: None)


def test_sync_structures_prefers_owner_token_and_marks_forbidden(conn):
    conn.executescript(
        """
        INSERT INTO characters(character_id,name) VALUES (7,'Igor'),(8,'Alt');
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (1,8,34,1049000000111,10),
            (2,7,34,1049000000222,10);
        """
    )
    conn.commit()
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("Authorization", "")
        calls.append((request.url.path, token))
        if request.url.path.endswith("/1049000000111/") and token == "Bearer tB":
            return httpx.Response(200, json={"name": "Azbel", "solar_system_id": 30000552,
                                             "type_id": 35826, "owner_id": 99})
        return httpx.Response(403, json={"error": "forbidden"})

    counts = structures_ingest.sync_structures(conn, _esi(handler), {7: "tA", 8: "tB"})
    assert counts["resolved"] == 1 and counts["forbidden"] == 1
    # первая попытка по 111 — токеном владельца ассетов (чар 8), без лишних 403
    first_111 = [t for p, t in calls if p.endswith("/1049000000111/")]
    assert first_111 == ["Bearer tB"]
    row = conn.execute("SELECT * FROM universe_structures WHERE structure_id = 1049000000111").fetchone()
    assert (row["name"], row["solar_system_id"], row["status"]) == ("Azbel", 30000552, "ok")
    bad = conn.execute("SELECT status FROM universe_structures WHERE structure_id = 1049000000222").fetchone()
    assert bad["status"] == "forbidden"
    # повторный синк: резолвленная — не трогаем, «нет доступа» — не дёргаем ESI неделю
    calls.clear()
    again = structures_ingest.sync_structures(conn, _esi(handler), {7: "tA", 8: "tB"})
    assert calls == [] and again["skipped"] == 1


def test_candidate_structure_ids_skip_stations_systems_items(conn):
    _seed(conn)
    ids = structures_ingest.candidate_structure_ids(conn)
    assert EC in ids and KEEPSTAR in ids
    assert NPC_JITA not in ids and 901 not in ids


# --------------------------------------------------------------------- корабли вне ассетов

GHOST, LOST = 1030000000001, 1049000000999   # корабль, которого нет в ассетах; структура без доступа


def _seed_ghost(conn):
    """Реальный кейс: у персонажа есть риги корабля (RigSlot*), а самого корабля в ассетах нет —
    ESI отдаёт только модули (напр. корабль в контракте). Рядом — настоящая структура без доступа."""
    conn.executescript(
        f"""
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity) VALUES
            (950,8,31526,{GHOST},1),(951,8,31538,{GHOST},1),(952,8,34,{LOST},7);
        INSERT INTO character_asset_flags(item_id,character_id,location_flag) VALUES
            (950,8,'RigSlot0'),(951,8,'RigSlot1'),(952,8,'Hangar');
        """
    )
    conn.commit()


def test_ghost_ship_is_not_a_structure(conn):
    _seed(conn)
    _seed_ghost(conn)
    res = locations.LocationResolver(conn, cfg_with())
    ghost = res.describe(GHOST)
    assert ghost.kind == "ghost" and ghost.system_id is None and "вне ассетов" in ghost.name
    assert res.describe(LOST).kind == "unknown"              # в ангаре («Hangar») — это структура
    assert res.describe(905).kind == "container"             # корабль, который В ассетах, — контейнер
    assert locations.unresolved_structure_ids(conn) == [LOST]
    ids = structures_ingest.candidate_structure_ids(conn)    # ESI не спрашиваем (403 жгут error limit)
    assert GHOST not in ids and LOST in ids
    assert stock.stock_view(conn, cfg_with(CUSTOM)).totals.get(31526) is None   # в склад не входит


def _svc(tmp_path):
    from forge import storage
    from forge.web.service import ForgeService
    (tmp_path / "forge.toml").write_text(BASE_TOML.replace('db_path = "x.db"', 'db_path = "forge.db"'),
                                         encoding="utf-8")
    c = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(c)
    _seed(c)
    _seed_ghost(c)
    c.close()
    return ForgeService(tmp_path / "forge.toml")


def test_service_stock_tree_filters_characters_and_hides_ghosts(tmp_path):
    svc = _svc(tmp_path)

    def roots(data):
        return {loc["location_id"] for s in data["systems"] for loc in s["locations"]}
    everyone = svc.stock_locations()
    assert GHOST not in roots(everyone) and LOST in roots(everyone)
    assert everyone["ghosts"] == {"ships": 1, "items": 2} and everyone["unresolved"] == 1
    assert roots(svc.stock_locations([8])) == {KEEPSTAR, LOST}      # как галки «Чьи ассеты считать»
    only_igor = svc.stock_locations([7])
    assert roots(only_igor) == {EC, NPC_JITA} and only_igor["ghosts"]["ships"] == 0


def test_service_stock_tree_shows_what_filters_cut(tmp_path):
    """Сценарий: Impairor отмечен в дереве, хотя стоит галка «Собранные корабли». Галка
    дерева — место; фильтры — предметы. Дерево получает, сколько в узле проходит фильтры (по
    несохранённым галкам) и почему остальное отсечено; корабль (905: модуль в слоте + трюм)."""
    svc = _svc(tmp_path)

    def node(data, loc_id):
        for s in data["systems"]:
            for loc in s["locations"]:
                if loc["location_id"] == loc_id:
                    return loc
                for ch in loc["children"]:
                    if ch["location_id"] == loc_id:
                        return ch
        raise AssertionError(loc_id)
    soft = svc.stock_locations(filters={"exclude_fitted": True, "exclude_assembled_ships": False,
                                        "exclude_flags": []})
    rifter = node(soft, 905)
    assert rifter["ship"] and rifter["self_passes"] and (rifter["items"], rifter["passing"]) == (2, 1)
    assert rifter["reasons"] == [[stock.REASON_FITTED, 1]]           # модуль в слоте; трюм — склад
    assert not node(soft, 901)["ship"] and node(soft, 901)["passing"] == 1
    cut = svc.stock_locations(filters={"exclude_fitted": True, "exclude_assembled_ships": True,
                                       "exclude_flags": ["Cargo"], "character_ids": [8]})   # лишний ключ — мимо
    rifter = node(cut, 905)
    assert rifter["passing"] == 0 and not rifter["self_passes"]      # целиком «не склад»
    assert {r for r, _n in rifter["reasons"]} == {stock.REASON_SHIP, stock.REASON_FITTED, "флаг Cargo"}
    ec = node(cut, EC)
    assert (ec["items"], ec["passing"]) == (6, 3)                    # 900, контейнер 901 и 902 — склад
    assert node(cut, KEEPSTAR)["passing"] == 1                       # фильтр персонажей — не через filters


# --------------------------------------------------------------------- отчёт: откуда брать

def _seed_widget(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (2000,'Widget',1.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,10),(1000,1,35,10);
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,1030000000003,0,0,-1,-1,0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume)
            VALUES (34,10000002,5.0,1000000),(35,10000009,3.0,1000000);
        """
    )
    conn.commit()


def test_report_hauls_own_stock_from_market_hub(conn):
    """Остаток в C-J6MT берётся в стройку, но его надо довезти: объём добавляется в плечо
    C-J6MT→GPLB-C (per_m3 2 ISK/м³), а в таблице видно, откуда брать."""
    _seed(conn)
    conn.execute("DELETE FROM character_assets WHERE item_id IN (900, 902, 904, 907)")  # Trit — только в Keepstar
    conn.commit()
    _seed_widget(conn)
    cfg = cfg_with(CUSTOM)
    p = report.build_report_payload(conn, cfg, [(2000, 1, 1)], now=datetime(2026, 9, 28, 12, 0))
    sh = p["shopping"]
    trit = next(r for r in sh["on_hand"] if r["type_id"] == 34)
    assert trit["covered"] == 10
    assert trit["sources"] == [{"root_id": KEEPSTAR, "label": "C-J6MT - 1st Taj Mahgoon",
                                "hub": "c_j6mt", "quantity": 10}]
    assert sh["totals"]["stock_haul_isk"] == pytest.approx(10 * 0.01 * 2.0)
    freight = next(b for b in p["cost_control"]["buckets"] if b["key"] == "freight")
    assert freight["plan"] >= sh["totals"]["stock_haul_isk"]
    assert p["places"] == {"jita": "Jita", "cj": "C-J6MT", "build": "GPLB-C"}


def test_report_without_stock_buys_everything(conn):
    _seed(conn)
    _seed_widget(conn)
    p = report.build_report_payload(conn, cfg_with(CUSTOM), [(2000, 1, 1)], use_stock=False,
                                    now=datetime(2026, 9, 28, 12, 0))
    assert p["shopping"]["on_hand"] == []
    assert sum(r["quantity"] for r in p["shopping"]["jita"]) == 10
    assert p["shopping"]["stock_used"] is False


def test_report_flags_stock_in_unknown_system(conn):
    _seed(conn)
    conn.executescript(
        f"""
        INSERT INTO universe_structures(structure_id,name,solar_system_id,type_id,status,updated_at)
            VALUES (1049000000777,'Tanoo - склад',{OTHER},35832,'ok','2026-09-28');
        DELETE FROM character_assets WHERE type_id = 35;
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity)
            VALUES (990,7,35,1049000000777,10);
        """
    )
    conn.commit()
    _seed_widget(conn)
    cfg = cfg_with(CUSTOM.replace(f"location_ids = [{KEEPSTAR}]", f"location_ids = [{KEEPSTAR}, 1049000000777]"))
    p = report.build_report_payload(conn, cfg, [(2000, 1, 1)], now=datetime(2026, 9, 28, 12, 0))
    other = p["shopping"]["stock_other"]
    assert other and other[0]["type_id"] == 35 and other[0]["label"] == "Tanoo - склад"
    html = report.render_report_html(p)
    assert "Довезти свой остаток" in html
