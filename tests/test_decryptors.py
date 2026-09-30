"""Выбор декриптора инвенты вручную ([invention]): none / fixed / allowed / per_product, фолбэк
на авто, если заданный декриптор недоступен (нет цены); по умолчанию — авто-оптимум по стоимости."""

from __future__ import annotations

import pytest

from forge import config as config_mod
from forge import core, storage
from forge.core import blueprint as bp
from forge.core import sourcing
from forge.web import report
from forge.web.service import ForgeService

BASE = """
db_path = "forge.db"
[industry]
invention_use_skills = false
[locations.jita]
name = "Jita"
region_id = 10000002
[locations.c_j6mt]
name = "C-J6MT"
region_id = 10000009
[locations.gplb_c]
name = "GPLB-C"
region_id = 10000006
{extra}
"""

PRODUCT, MANUF_BP = 2185, 2186
ACCELERANT, ATTAINMENT, PROCESS = 34201, 34202, 34205


def cfg(extra: str = "") -> config_mod.Config:
    return config_mod.loads(BASE.format(extra=extra))


def _seed(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES (1001,'DC A',0.0),(1002,'DC B',0.0),
            (2185,'T2 Item',1.0),(40,'Mineral',0.0),
            (34201,'Accelerant Decryptor',0.0),(34202,'Attainment Decryptor',0.0),
            (34205,'Process Decryptor',0.0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,sell_volume) VALUES
            (1001,10000009,100000.0,1000),(1002,10000009,50000.0,1000),(40,10000009,10.0,100000000),
            (34201,10000009,2000.0,100),(34205,10000009,1000.0,100);   -- Attainment без цены
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (2184,8,2186,10,0.3),(2186,1,2185,1,1.0);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (2184,8,1001,2),(2184,8,1002,2),(2186,1,40,100000);
        """
    )
    conn.commit()


def _node(conn, c: config_mod.Config, runs: int = 1):
    return sourcing.build_node_cost(conn, PRODUCT, runs, 1, core.build_params_from_config(c, conn),
                                    allow_build=False)


def test_default_is_auto_cost_same_numbers(conn):
    _seed(conn)
    assert config_mod.Config().invention.decryptor_mode == "auto_cost"
    n = _node(conn, cfg())
    assert n.decryptor == "Accelerant"               # авто-оптимум по умолчанию: ME+2 и шанс ×1.2 окупают цену
    assert not n.decryptor_manual and n.decryptor_fallback is None
    explicit = _node(conn, cfg('[invention]\ndecryptor_mode = "auto_cost"\nallowed_decryptors = []'))
    assert explicit.total_cost == n.total_cost and explicit.decryptor == n.decryptor
    # ровно то же, что min() по всем вариантам
    params = core.build_params_from_config(cfg(), conn)
    opts = bp.invention_options(conn, MANUF_BP, params)
    choice = bp.choose_decryptor(opts, PRODUCT, params, key=lambda o: o.per_run)
    assert choice.option is min(opts, key=lambda o: o.per_run)


def test_mode_none(conn):
    _seed(conn)
    n = _node(conn, cfg('[invention]\ndecryptor_mode = "none"'))
    assert n.decryptor is None and n.decryptor_manual
    assert n.resulting_te == bp.INVENT_BASE_TE
    assert not any(ln.type_id in (ACCELERANT, PROCESS) for ln in n.lines)   # декриптор не закупается
    assert n.invention_breakdown["attempts"] == 4                            # ceil(1/0.3)


def test_mode_fixed(conn):
    _seed(conn)
    n = _node(conn, cfg(f'[invention]\ndecryptor_mode = "fixed"\ndecryptor_type_id = {PROCESS}'))
    assert n.decryptor == "Process" and n.decryptor_manual and n.decryptor_fallback is None
    mineral = next(ln for ln in n.lines if ln.type_id == 40)
    assert mineral.quantity == 95000                     # ME 2 + 3 (Process) = 5%
    assert next(ln for ln in n.lines if ln.type_id == PROCESS).quantity == n.invention_breakdown["attempts"]
    assert n.total_cost > _node(conn, cfg()).total_cost  # вручную — дороже авто-оптимума
    none = _node(conn, cfg('[invention]\ndecryptor_mode = "fixed"\ndecryptor_type_id = 0'))
    assert none.decryptor is None and none.decryptor_manual


def test_fixed_unavailable_falls_back_to_auto(conn):
    _seed(conn)
    n = _node(conn, cfg(f'[invention]\ndecryptor_mode = "fixed"\ndecryptor_type_id = {ATTAINMENT}'))
    auto = _node(conn, cfg())
    assert n.decryptor == auto.decryptor == "Accelerant"
    assert not n.decryptor_manual
    assert n.decryptor_fallback and "Attainment" in n.decryptor_fallback and "нет цены" in n.decryptor_fallback
    assert n.total_cost == auto.total_cost


def test_allowed_limits_auto_choice(conn):
    _seed(conn)
    n = _node(conn, cfg(f"[invention]\nallowed_decryptors = [{PROCESS}]"))
    assert n.decryptor == "Process" and not n.decryptor_manual   # Accelerant дешевле, но не разрешён
    only_unpriced = _node(conn, cfg(f"[invention]\nallowed_decryptors = [{ATTAINMENT}]"))
    assert only_unpriced.decryptor is None               # из разрешённых доступен только «без декриптора»


def test_per_product_overrides_mode(conn):
    _seed(conn)
    n = _node(conn, cfg(f'[invention]\ndecryptor_mode = "none"\n'
                        f'per_product = [{{ type_id = {PRODUCT}, decryptor_type_id = {PROCESS} }}]'))
    assert n.decryptor == "Process" and n.decryptor_manual
    plain = _node(conn, cfg(f'[invention]\ndecryptor_mode = "fixed"\ndecryptor_type_id = {PROCESS}\n'
                            f'per_product = [{{ type_id = {PRODUCT}, decryptor_type_id = 0 }}]'))
    assert plain.decryptor is None and plain.decryptor_manual
    other = _node(conn, cfg('[invention]\nper_product = [{ type_id = 99999, decryptor_type_id = 0 }]'))
    assert other.decryptor == "Accelerant" and not other.decryptor_manual   # чужой оверрайд не влияет


def test_per_product_unavailable_falls_back(conn):
    _seed(conn)
    n = _node(conn, cfg(f'[invention]\nper_product = [{{ type_id = {PRODUCT}, decryptor_type_id = {ATTAINMENT} }}]'))
    assert n.decryptor == "Accelerant" and n.decryptor_fallback and "Attainment" in n.decryptor_fallback


def test_shortfall_branch_uses_same_choice(conn):
    _seed(conn)
    conn.execute("INSERT INTO characters(character_id,name) VALUES (7,'I')")
    conn.execute("INSERT INTO character_blueprints(item_id,character_id,type_id,me,te,quantity,runs,is_copy) "
                 "VALUES (1,7,2186,0,0,1,5,1)")   # копия на 5 прогонов, нужно 10
    conn.commit()
    auto = _node(conn, cfg(), runs=10)
    assert auto.blueprint_source == "owned_bpc_insufficient" and not auto.decryptor_manual
    fixed = _node(conn, cfg(f'[invention]\ndecryptor_mode = "fixed"\ndecryptor_type_id = {PROCESS}'), runs=10)
    assert fixed.bpc_shortfall_decryptor == "Process" and fixed.decryptor_manual
    assert any(ln.type_id == PROCESS for ln in fixed.lines)
    none = _node(conn, cfg('[invention]\ndecryptor_mode = "none"'), runs=10)
    assert none.bpc_shortfall_decryptor is None and none.decryptor_manual
    fb = _node(conn, cfg(f'[invention]\ndecryptor_mode = "fixed"\ndecryptor_type_id = {ATTAINMENT}'), runs=10)
    assert fb.decryptor_fallback and fb.bpc_shortfall_decryptor == auto.bpc_shortfall_decryptor


def test_choose_decryptor_unknown_type_is_not_a_decryptor(conn):
    _seed(conn)
    params = core.build_params_from_config(cfg("[invention]\ndecryptor_mode = \"fixed\"\ndecryptor_type_id = 40"), conn)
    opts = bp.invention_options(conn, MANUF_BP, params)
    choice = bp.choose_decryptor(opts, PRODUCT, params, key=lambda o: o.per_run)
    assert choice.fallback and "не декриптор" in choice.fallback


def test_report_prep_shows_manual_and_fallback():
    p = {"places": {"jita": "Jita", "cj": "C-J6MT", "build": "GPLB-C"},
         "prep": {"transfers": []},
         "shopping": {"jita": [], "cj": [], "on_hand": [], "own_build": [],
                      "invention": [{"type_id": PRODUCT, "name": "T2 Item", "produced": 1, "attempts": 3,
                                     "decryptor": "Accelerant", "decryptor_manual": True, "probability": 0.36}],
                      "issues": [{"type_id": PRODUCT, "name": "T2 Item", "kind": "decryptor_fallback",
                                  "note": "декриптор «Attainment» задан вручную, но недоступен"}],
                      "totals": {"jita_isk": 0.0, "cj_isk": 0.0, "on_hand_isk": 0.0,
                                 "freight_inbound": 0.0, "stock_haul_isk": 0.0}}}
    html = report._shopping_html(p)
    assert "декриптор «Accelerant» (задано вручную)" in html
    assert "Инвента T2 Item: декриптор «Attainment» задан вручную, но недоступен" in html


def test_service_decryptors_list(tmp_path):
    (tmp_path / "forge.toml").write_text(BASE.format(extra=""), encoding="utf-8")
    c = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(c)
    _seed(c)
    c.close()
    rows = {r["type_id"]: r for r in ForgeService(tmp_path / "forge.toml").decryptors()}
    assert len(rows) == 8
    assert rows[PROCESS]["name"] == "Process" and rows[PROCESS]["price"] == pytest.approx(1000.0)
    assert rows[ATTAINMENT]["price"] is None
