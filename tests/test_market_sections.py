"""«Что строю»: дерево разделов рынка (sde_market_groups), фильтр снятых разделов со всеми
подразделами во всех расчётах и ТОП с разбивкой по разделам."""

from __future__ import annotations

from forge import config as config_mod
from forge import recommend as rec
from forge import storage
from forge.recommend.engine import buy_cheaper, candidate_products
from forge.recommend.sections import SectionTree
from forge.web.service import ForgeService

TOML = """
db_path = "forge.db"
[industry]
[recommend]
w_roi = 1.0
w_isk_hour = 1.0
w_liquidity = 0.0
{recommend}
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
"""


def cfg(recommend: str = "") -> config_mod.Config:
    return config_mod.loads(TOML.format(recommend=recommend))


# Ships(4) › Frigates(1361) › Standard Frigates(5) › Minmatar(61)
#                           › Faction Frigates(1362)
#                           › Advanced Frigates(1364) › Assault Frigates(400)
#          › Cruisers(1367) › Standard Cruisers(6)
# Ammunition & Charges(11) › Hybrid Charges(100) › Standard Charges(848)
MARKET_GROUPS = """
INSERT INTO sde_market_groups(market_group_id,parent_group_id,name,has_types) VALUES
    (4,NULL,'Ships',0),(1361,4,'Frigates',0),(5,1361,'Standard Frigates',0),(61,5,'Minmatar',1),
    (1362,1361,'Faction Frigates',1),(1364,1361,'Advanced Frigates',0),(400,1364,'Assault Frigates',1),
    (1367,4,'Cruisers',0),(6,1367,'Standard Cruisers',1),
    (11,NULL,'Ammunition & Charges',0),(100,11,'Hybrid Charges',0),(848,100,'Standard Charges',1),
    (999,NULL,'Apparel',1);
"""


def _seed(conn, market_groups: bool = True):
    """Rifter (T1), Firetail (фракция), Wolf (T2), Rupture (T1 крейсер), Antimatter (патроны), Junk
    (без раздела). Все строятся из Tritanium и продаются в C-J6MT; свои чертежи — у всех, кроме Wolf
    и Rupture."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (34,'Tritanium',0.01);
        INSERT INTO sde_types(type_id,name,market_group_id) VALUES
            (2000,'Rifter',61),(2001,'Firetail',1362),(2002,'Wolf',400),(2003,'Antimatter',848),
            (2004,'Junk',NULL),(2005,'Rupture',6);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0),(1001,1,2001,1,1.0),(1002,1,2002,1,1.0),(1003,1,2003,1,1.0),
                   (1004,1,2004,1,1.0),(1005,1,2005,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,10),(1001,1,34,20),(1002,1,34,30),(1003,1,34,5),(1004,1,34,5),(1005,1,34,40);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds)
            VALUES (1000,1,600),(1001,1,600),(1002,1,600),(1003,1,600),(1004,1,600),(1005,1,600);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0);
        INSERT INTO system_cost_indices(system_id,activity_id,cost_index) VALUES (30000552,1,0.05);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES
            (34,10000009,5.0,4.0,1000000),
            (2000,10000009,1000.0,900.0,5000),(2001,10000009,1000.0,900.0,5000),
            (2002,10000009,1000.0,900.0,5000),(2003,10000009,1000.0,900.0,5000),
            (2004,10000009,1000.0,900.0,5000),(2005,10000009,1000.0,900.0,5000);
        INSERT INTO characters(character_id,name) VALUES (7,'Igor');
        INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,0,0,-1,-1,0),(2,7,1001,0,0,-1,-1,0),(3,7,1003,0,0,-1,-1,0),
                   (4,7,1004,0,0,-1,-1,0);
        """
    )
    if market_groups:
        conn.executescript(MARKET_GROUPS)
    conn.commit()


# ---------------------------------------------------------------------------- дерево
def test_tree_only_buildable_sections_with_counts_and_glued_chains(conn):
    _seed(conn)
    tree = SectionTree.load(conn, own_ids=[2000, 2001, 2003, 2004])
    assert tree.ready
    # корни по имени, «без раздела» — последним; пустой раздел (Apparel) в дерево не попал
    names = [r.name for r in tree.roots]
    assert names == ["Ammunition & Charges › Hybrid Charges › Standard Charges", "Ships", "Без раздела маркета"]
    ammo, ships, nomg = tree.roots
    assert ammo.id == 848 and ammo.chain == [11, 100, 848] and (ammo.own, ammo.total) == (1, 1)
    assert (ships.own, ships.total) == (2, 4) and (nomg.id, nomg.own, nomg.total) == (0, 1, 1)
    cru, frig = ships.children
    assert (cru.name, cru.id, cru.chain) == ("Cruisers › Standard Cruisers", 6, [1367, 6])
    assert frig.name == "Frigates"
    assert [c.name for c in frig.children] == ["Advanced Frigates › Assault Frigates", "Faction Frigates",
                                               "Standard Frigates › Minmatar"]
    adv = frig.children[0]
    assert adv.id == 400 and adv.chain == [1364, 400] and (adv.own, adv.total) == (0, 1)


def test_tree_paths_labels_and_levels(conn):
    _seed(conn)
    tree = SectionTree.load(conn)
    assert [s.name for s in tree.path(61)] == ["Ships", "Frigates", "Standard Frigates › Minmatar"]
    assert tree.label(61) == ("Frigates › Standard Frigates › Minmatar",
                              "Ships › Frigates › Standard Frigates › Minmatar")
    assert tree.label(1364) == tree.label(400)          # любой id склеенной цепочки — тот же узел
    assert [s.id for s in tree.at_level(400, 2)] == [4, 1361]
    assert [s.id for s in tree.at_level(848, 3)] == [848]  # лежит выше уровня — свой раздел
    assert tree.label(None) == ("Без раздела маркета", "Без раздела маркета")


def test_tree_without_market_groups_is_not_ready(conn):
    _seed(conn, market_groups=False)
    tree = SectionTree.load(conn)
    assert not tree.ready
    assert [r.id for r in tree.roots] == [0] and tree.roots[0].total == 6


# ---------------------------------------------------------------------------- фильтр
def test_unticked_section_excludes_whole_subtree_everywhere(conn):
    _seed(conn)
    frig_off = cfg("exclude_market_group_ids = [1361]")
    assert set(candidate_products(conn, owned=False, cfg=frig_off)) == {2003, 2004, 2005}
    assert set(candidate_products(conn, owned=True, cfg=frig_off)) == {2003, 2004}
    # верх склеенной цепочки «Advanced Frigates › Assault Frigates» снимает и низ
    assert set(candidate_products(conn, owned=False, cfg=cfg("exclude_market_group_ids = [1364]"))) == \
        {2000, 2001, 2003, 2004, 2005}
    # 0 — «без раздела маркета»
    assert set(candidate_products(conn, owned=False, cfg=cfg("exclude_market_group_ids = [0, 848]"))) == \
        {2000, 2001, 2002, 2005}
    recs = rec.recommend(conn, frig_off, owned=False, runs=1, top=10)
    assert {r.product_type_id for r in recs} == {2003, 2004, 2005}
    deals = buy_cheaper(conn, frig_off, owned=False, max_savings_pct=1.0)
    assert not {d.product_type_id for d in deals} & {2000, 2001, 2002}


def test_no_section_exclusion_is_safe_without_tree(conn):
    """SDE ещё не перезалит (дерева нет): «без раздела» снимает только предметы без раздела, а не всё."""
    _seed(conn, market_groups=False)
    assert set(candidate_products(conn, owned=False, cfg=cfg("exclude_market_group_ids = [0]"))) == \
        {2000, 2001, 2002, 2003, 2005}


def test_rank_does_not_touch_inputs(conn):
    _seed(conn)
    c = cfg()
    recs = rec.evaluate(conn, c, owned=False, runs=1)
    assert recs and all(r.score == 0.0 for r in recs)
    top2 = rec.rank(recs, c, 2)
    assert len(top2) == 2 and all(r.score == 0.0 for r in recs)
    assert [r.product_type_id for r in rec.rank(recs, c)] == \
        [r.product_type_id for r in rec.recommend(conn, c, owned=False, runs=1, top=10)]


# ---------------------------------------------------------------------------- сервис
def _svc(tmp_path, recommend: str = "") -> ForgeService:
    (tmp_path / "forge.toml").write_text(TOML.format(recommend=recommend), encoding="utf-8")
    c = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(c)
    _seed(c)
    c.close()
    return ForgeService(tmp_path / "forge.toml")


def test_service_top_split_by_market_level(tmp_path):
    svc = _svc(tmp_path)
    res = svc.recommend_top(owned=False, top=10, runs=1, split=0)
    assert res["sections"] == [] and len(res["flat"]) == 6
    rifter = next(r for r in res["flat"] if r["name"] == "Rifter")
    assert rifter["section"] == "Frigates › Standard Frigates › Minmatar"
    assert rifter["section_path"].startswith("Ships › Frigates")

    res = svc.recommend_top(owned=False, top=10, runs=1, split=1)
    by = {s["name"]: {r["name"] for r in s["items"]} for s in res["sections"]}
    assert list(by) == ["Ammunition & Charges › Hybrid Charges › Standard Charges", "Ships",
                        "Без раздела маркета"]
    assert by["Ships"] == {"Rifter", "Firetail", "Wolf", "Rupture"}

    res = svc.recommend_top(owned=False, top=10, runs=1, split=3)   # T1 / фракция / T2 — отдельно
    names = [s["name"] for s in res["sections"]]
    assert names == ["Ammunition & Charges › Hybrid Charges › Standard Charges",
                     "Ships › Cruisers › Standard Cruisers",
                     "Ships › Frigates › Advanced Frigates › Assault Frigates",
                     "Ships › Frigates › Faction Frigates", "Ships › Frigates › Standard Frigates › Minmatar",
                     "Без раздела маркета"]
    assert all(len(s["items"]) == 1 for s in res["sections"])


def test_service_market_tree_and_saved_ticks(tmp_path):
    svc = _svc(tmp_path)
    tree = svc.market_tree()
    assert tree["ready"] and tree["excluded"] == []
    ships = next(r for r in tree["roots"] if r["name"] == "Ships")
    assert (ships["own"], ships["total"]) == (2, 4)
    svc.put_config({"recommend": {"exclude_market_group_ids": [1362, 0]}})
    assert svc.market_tree()["excluded"] == [0, 1362]
    flat = svc.recommend_top(owned=False, top=10, runs=1)["flat"]
    assert {r["name"] for r in flat} == {"Rifter", "Wolf", "Antimatter", "Rupture"}
    # Разделы — и у «Из остатков»/«Дешевле купить»
    assert all("section" in r for r in svc.buy_cheaper(owned=False))
