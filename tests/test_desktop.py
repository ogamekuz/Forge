"""Пульт (forge.desktop, PySide6): окно строится, вкладки открываются, расчёты идут через сервис.

Окна создаются по-настоящему, но без экрана (QT_QPA_PLATFORM=offscreen). Сети нет: иконки
предметов не качаются (FORGE_NO_ICON_NET), сервер отчётов не поднимается (start_server=False).
Конфиг и БД — во временной папке. Без PySide6 файл пропускается.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FORGE_NO_ICON_NET"] = "1"
pytest.importorskip("PySide6")

from forge import config as config_mod
from forge import storage

TOML = """
db_path = "forge.db"
manufacturing_character_ids = [7]
[industry]
broker_fee = 0.0
sales_tax = 0.0
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
"""


def _seed(conn):
    conn.executescript(
        """
        INSERT INTO sde_systems(system_id,name,region_id,security) VALUES
            (30000552,'GPLB-C',10000006,-0.3),(30000772,'C-J6MT',10000009,-0.2),(30000142,'Jita',10000002,0.9);
        INSERT INTO universe_structures(structure_id,name,solar_system_id,type_id,status,updated_at)
            VALUES (1030000000003,'GPLB-C - Engineering',30000552,35827,'ok','2026-09-28');
        INSERT INTO sde_types(type_id,name,volume) VALUES (34,'Tritanium',0.01),(35,'Pyerite',0.01),(2000,'Widget',5.0);
        INSERT INTO sde_blueprint_products(blueprint_type_id,activity_id,product_type_id,quantity,probability)
            VALUES (1000,1,2000,1,1.0);
        INSERT INTO sde_blueprint_activities(blueprint_type_id,activity_id,time_seconds) VALUES (1000,1,600);
        INSERT INTO sde_blueprint_materials(blueprint_type_id,activity_id,material_type_id,quantity)
            VALUES (1000,1,34,100),(1000,1,35,50);
        INSERT INTO characters(character_id,name,wallet_balance) VALUES (7,'Igor',1000000),(8,'Alt',5);
        INSERT INTO character_blueprints(item_id,character_id,type_id,location_id,me,te,quantity,runs,is_copy)
            VALUES (1,7,1000,1030000000003,0,0,-1,-1,0);
        INSERT INTO character_assets(item_id,character_id,type_id,location_id,quantity)
            VALUES (900,7,34,1030000000003,40);
        INSERT INTO market_adjusted_prices(type_id,adjusted_price) VALUES (34,3.0),(35,18.0);
        INSERT INTO market_snapshot(type_id,region_id,sell_min,buy_max,sell_volume) VALUES
            (34,10000002,5.0,4.0,100000),(35,10000002,19.0,18.0,100000),(35,10000009,18.0,17.0,100000),
            (2000,10000009,5000.0,4500.0,50);
        """
    )
    conn.commit()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    from forge.desktop import theme
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


def wait_until(app, cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return cond()


@pytest.fixture
def win(tmp_path, qapp):
    (tmp_path / "forge.toml").write_text(TOML, encoding="utf-8")
    conn = storage.connect(str(tmp_path / "forge.db"))
    storage.init_db(conn)
    _seed(conn)
    conn.close()
    from forge.desktop.main_window import MainWindow
    w = MainWindow(str(tmp_path / "forge.toml"), start_server=False)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()


def test_all_tabs_open(win, qapp):
    from forge.desktop.main_window import TABS
    for key, _t, _i in TABS:
        win.go(key)
        wait_until(qapp, lambda: False, 0.3)
        assert win.stack.currentWidget() is win.pages[key]
    assert wait_until(qapp, lambda: bool(win.hub.snapshot), 5)


def test_calculator_computes_basket(win, qapp):
    win.basket.clear()
    win.basket.add(2000, "Widget", 3, True)
    win.go("calc")
    calc = win.pages["calc"]
    calc.run()
    assert wait_until(qapp, lambda: calc._data is not None, 20)
    item = calc._data["items"][0]
    assert item["node"]["produced"] == 3
    assert calc._data["totals"]["total_cost"] > 0


def test_planner_builds_gantt(win, qapp):
    win.basket.clear()
    win.basket.add(2000, "Widget", 2, True)
    win.go("plan")
    plan = win.pages["plan"]
    plan.run()
    assert wait_until(qapp, lambda: plan.gantt_card.isVisible(), 20)
    assert plan.gantt._items and plan.gantt._items[0]["character_name"] == "Igor"
    assert plan.gantt._items[0]["owner_status"] == "owner"          # Igor — владелец BPO
    assert plan.s_transfers.value.text() == "0"


def test_gantt_owner_line():
    from forge.desktop.pages.planner import owner_line
    assert "владелец" in owner_line({"owner_status": "owner"})
    assert "передача чертежа от: Igor" in owner_line({"owner_status": "transfer", "owners": ["Igor"]})
    assert owner_line({"owner_status": ""}) == ""


def test_settings_owner_policy_roundtrip(win, qapp, tmp_path):
    win.go("settings")
    page = win.pages["settings"]
    assert wait_until(qapp, lambda: bool(page.cfg), 15)
    assert page.pl_owner.value() == "any" and not page.pl_slack.isEnabled()
    page.pl_owner.set_value("prefer_owner", emit=True)
    assert page.pl_slack.isEnabled()
    page.pl_slack.setValue(6)
    page.save()
    path = str(tmp_path / "forge.toml")
    assert wait_until(qapp, lambda: config_mod.load(path).planner.owner_policy == "prefer_owner", 10)
    assert config_mod.load(path).planner.owner_slack_hours == 6.0


def test_settings_copy_jobs_toggle(win, qapp, tmp_path):
    win.go("settings")
    page = win.pages["settings"]
    assert wait_until(qapp, lambda: bool(page.cfg), 15)
    assert not page.pl_copy.isChecked()                             # по умолчанию — без копий
    page.pl_copy.setChecked(True)
    page.save()
    path = str(tmp_path / "forge.toml")
    assert wait_until(qapp, lambda: config_mod.load(path).planner.schedule_copy_jobs is True, 10)


def test_gantt_copy_job_title():
    from forge.desktop.pages.planner import job_title
    assert job_title({"activity_id": 5, "name": "Копия: Vexor Blueprint", "runs": 6, "copy_runs": 1}) == \
        "Копия: Vexor Blueprint — 6 коп. × 1 прог."
    assert job_title({"activity_id": 8, "name": "Инвента: Ishtar", "runs": 1}) == "Инвента: Ishtar ×1"


def test_header_branding_and_donate(win, qapp):
    """Шапка — без мест (стройка/рынок меняются в настройках); флаг автора (корпорация, альянс) и
    «Поддержать» → окно с персонажем для ISK и копированием имени."""
    from PySide6.QtGui import QGuiApplication

    from forge.desktop import branding
    from forge.desktop.donate import DonateDialog
    assert win.tagline.text() == "индустрия EVE Online"
    assert "Ministry of Offense [M4O]" in win.logo_corp.toolTip()
    assert "Goonswarm Federation <CONDI>" in win.logo_alliance.toolTip()

    def dialog():
        return next((w for w in qapp.topLevelWidgets() if isinstance(w, DonateDialog) and w.isVisible()), None)
    win.pill_donate.clicked.emit()
    assert wait_until(qapp, lambda: dialog() is not None, 5)
    dlg = dialog()
    assert dlg.name.text() == branding.DONATE_TO.name == "Kalaratri Omanid"
    dlg.copy.click()
    assert QGuiApplication.clipboard().text() == "Kalaratri Omanid" and dlg.copy.text() == "Скопировано"
    dlg.close()
    assert wait_until(qapp, lambda: dialog() is None, 5)


def test_eve_images_offline_and_disk_cache(qapp, tmp_path):
    from PySide6.QtGui import QColor, QPixmap

    from forge.desktop.icons import EveImages
    imgs = EveImages()
    imgs._dir = tmp_path
    assert imgs.pixmap("alliance", 1354830081) is None and not imgs._pending   # сети в тестах нет
    pm = QPixmap(8, 8)
    pm.fill(QColor("red"))
    assert pm.save(str(tmp_path / "corporation_98366055_128.png"))
    assert imgs.pixmap("corporation", 98366055) is not None                  # из дискового кэша


def test_stock_page_saves_custom_selection(win, qapp, tmp_path):
    win.go("stock")
    page = win.pages["stock"]
    assert wait_until(qapp, lambda: page.tree.topLevelItemCount() > 0, 15)
    assert "серым «не склад»" in page.mode_note.text()
    assert page.mode.button("auto").text().strip() == "Авто"
    page.mode.set_value("custom", emit=True)
    page.sel_systems = {30000552}
    page.save()
    assert wait_until(qapp, lambda: config_mod.load(str(tmp_path / "forge.toml")).stock.mode == "custom", 10)
    cfg = config_mod.load(str(tmp_path / "forge.toml"))
    assert cfg.stock.system_ids == [30000552]
    assert wait_until(qapp, lambda: page.cont_table.rowCount() >= 1, 10)


def test_stock_tree_follows_character_checks(win, qapp, tmp_path):
    """Галки «Чьи ассеты считать» сразу (до сохранения) перестраивают дерево «Где что лежит»;
    раскрытая система остаётся раскрытой. Ассеты в сиде — только у Igor (GPLB-C)."""
    win.go("stock")
    page = win.pages["stock"]
    assert wait_until(qapp, lambda: page.tree.topLevelItemCount() == 1, 15)
    page.tree.topLevelItem(0).setExpanded(True)
    page._char_checks[7].setChecked(False)                  # остался Alt — у него ничего нет
    assert wait_until(qapp, lambda: page.tree.topLevelItemCount() == 0, 10)
    page._char_checks[7].setChecked(True)                   # все снова отмечены = все персонажи
    assert wait_until(qapp, lambda: page.tree.topLevelItemCount() == 1, 10)
    assert page.tree.topLevelItem(0).isExpanded()
    assert config_mod.load(str(tmp_path / "forge.toml")).stock.character_ids == []   # не сохранялось


def test_stock_tree_greys_out_what_filters_cut(win, qapp):
    """Галка дерева — место; что фильтры справа отсекают целиком (корабль с фитом при «Собранных
    кораблях») — серым «не склад» без галки; частично — подсказка у числа предметов."""
    from PySide6.QtCore import Qt

    from forge.desktop.pages.stock import FILTERED_ROLE
    win.go("stock")
    page = win.pages["stock"]
    assert wait_until(qapp, lambda: page.tree.topLevelItemCount() > 0, 15)
    ec = 1030000000003
    page._data = {"mode": "auto", "auto_location_ids": [ec], "unresolved": 0, "ghosts": {"ships": 0, "items": 0},
                  "systems": [{"system_id": 30000552, "name": "GPLB-C", "security": -0.3, "hub": "gplb_c",
                               "items": 6, "value": 1.0, "passing": 3, "selected": False, "locations": [
                                   {"location_id": ec, "kind": "structure", "name": "EC", "resolved": True,
                                    "items": 6, "value": 1.0, "passing": 3, "reasons": [], "characters": [7],
                                    "selected": True, "excluded": False, "children": [
                                        {"location_id": 905, "name": "Impairor", "ship": True, "items": 3,
                                         "value": 0.0, "passing": 0, "self_passes": False,
                                         "reasons": [["модули в слотах фита", 3], ["собранный корабль", 1]],
                                         "excluded": False, "selected": False},
                                        {"location_id": 901, "name": "Box", "ship": False, "items": 2,
                                         "value": 0.0, "passing": 1, "self_passes": True,
                                         "reasons": [["флаг Cargo", 1]], "excluded": False, "selected": False}]}]}]}
    page._build_tree()
    loc = page.tree.topLevelItem(0).child(0)
    ship, box = loc.child(0), loc.child(1)
    assert loc.checkState(0) == Qt.CheckState.Checked                 # место в складе («Авто»)
    assert ship.text(3) == FILTERED_ROLE and ship.checkState(0) == Qt.CheckState.Unchecked
    assert not ship.flags() & Qt.ItemFlag.ItemIsUserCheckable and "собранный корабль ×1" in ship.toolTip(0)
    assert box.checkState(0) == Qt.CheckState.Checked and box.text(3) == "" and "1 из 2" in box.toolTip(1)


def test_stock_filter_checks_rebuild_tree(win, qapp):
    """Галка фильтра («Собранные корабли») сразу перестраивает дерево с НЕсохранёнными фильтрами."""
    win.go("stock")
    page = win.pages["stock"]
    assert wait_until(qapp, lambda: page.tree.topLevelItemCount() > 0, 15)
    calls: list = []
    orig = page.svc.stock_locations
    page.svc.stock_locations = lambda chars=None, filters=None: (calls.append(filters), orig(chars, filters))[1]
    try:
        page.ex_ships.setChecked(not page.ex_ships.isChecked())
        assert wait_until(qapp, lambda: bool(calls), 10)
        assert calls[-1]["exclude_assembled_ships"] is page.ex_ships.isChecked()
    finally:
        del page.svc.stock_locations


def test_characters_roles_roundtrip(win, qapp, tmp_path):
    win.go("chars")
    page = win.pages["chars"]
    assert wait_until(qapp, lambda: bool(page._checks.get("science_character_ids")), 10)
    page._checks["science_character_ids"][8].setChecked(True)
    page.save()
    assert wait_until(qapp, lambda: config_mod.load(str(tmp_path / "forge.toml")).science_character_ids == [8], 10)


def test_characters_slot_limits_roundtrip(win, qapp, tmp_path):
    win.go("chars")
    page = win.pages["chars"]
    assert wait_until(qapp, lambda: bool(page._limits), 10)
    assert all(e.value() is None for eds in page._limits.values() for e in eds.values())  # пусто = все
    page._limits[7]["manufacturing"].setValue(2)
    page._limits[8]["science"].setValue(0)
    page.save()
    path = str(tmp_path / "forge.toml")
    assert wait_until(qapp, lambda: len(config_mod.load(path).planner.slot_limits) == 2, 10)
    pl = config_mod.load(path).planner
    assert pl.slot_limit(7, "manufacturing") == 2 and pl.slot_limit(7, "reaction") is None
    assert pl.slot_limit(8, "science") == 0
    assert config_mod.load(path).manufacturing_character_ids == [7]   # роли сохранены вместе с лимитами


def test_settings_save_roundtrip(win, qapp, tmp_path):
    win.go("settings")
    page = win.pages["settings"]
    assert wait_until(qapp, lambda: bool(page.cfg), 15)
    page.hub_jita.setChecked(False)
    page.pl_days.setText("4, 1.5")
    page.save()
    assert wait_until(qapp, lambda: config_mod.load(str(tmp_path / "forge.toml")).planner.compare_max_days == [4.0, 1.5], 10)
    cfg = config_mod.load(str(tmp_path / "forge.toml"))
    assert cfg.market.buy_hubs == ["cj"]
    assert cfg.stock.mode == "auto"  # чужие секции не затёрты


def test_settings_invention_skills_toggle(win, qapp, tmp_path):
    win.go("settings")
    page = win.pages["settings"]
    assert wait_until(qapp, lambda: bool(page.cfg), 15)
    assert page.inv_skills.isChecked()                  # по умолчанию — со скиллами
    page.inv_skills.setChecked(False)
    page.save()
    assert wait_until(qapp, lambda: config_mod.load(str(tmp_path / "forge.toml")).industry.invention_use_skills
                      is False, 10)
    assert config_mod.load(str(tmp_path / "forge.toml")).manufacturing_character_ids == [7]  # не затёрто


def test_settings_decryptor_choice_roundtrip(win, qapp, tmp_path):
    win.go("settings")
    page = win.pages["settings"]
    assert wait_until(qapp, lambda: bool(page.cfg), 15)
    assert page.dec_mode.value() == "auto_cost" and len(page.dec_allowed) == 8
    assert all(cb.isChecked() for cb in page.dec_allowed.values())   # пусто в конфиге = все разрешены
    assert not page.dec_fixed.isEnabled()
    for cb in page.dec_allowed.values():
        cb.setChecked(False)
    page.save()                                                        # авто без единого — ошибка, не сохраняем
    assert "не отмечен ни один" in page.status.text()
    page.dec_allowed[34205].setChecked(True)
    page.dec_mode.set_value("fixed", emit=True)
    assert page.dec_fixed.isEnabled()
    page.dec_fixed.setCurrentIndex(page.dec_fixed.findData(34207))
    page._add_dec_override(2000, "Widget", 0)
    page.dec_rows[2000].setCurrentIndex(page.dec_rows[2000].findData(34201))
    page.save()
    path = str(tmp_path / "forge.toml")
    assert wait_until(qapp, lambda: config_mod.load(path).invention.decryptor_mode == "fixed", 10)
    inv = config_mod.load(path).invention
    assert inv.decryptor_type_id == 34207 and inv.allowed_decryptors == [34205]
    assert [(o.type_id, o.decryptor_type_id) for o in inv.per_product] == [(2000, 34201)]


def test_settings_liquidity_source_roundtrip(win, qapp, tmp_path):
    win.go("settings")
    page = win.pages["settings"]
    assert wait_until(qapp, lambda: bool(page.cfg), 15)
    assert page.liq_src.currentData() == "sell_region"
    page.liq_src.setCurrentIndex(page.liq_src.findData("sell_region_history"))
    page.liq_days.setValue(14)
    page.save()
    path = str(tmp_path / "forge.toml")
    assert wait_until(qapp, lambda: config_mod.load(path).recommend.liquidity_source == "sell_region_history", 10)
    assert config_mod.load(path).recommend.liquidity_days == 14


def test_settings_market_structures_roundtrip(win, qapp, tmp_path):
    from PySide6.QtWidgets import QLabel
    win.go("settings")
    page = win.pages["settings"]
    assert wait_until(qapp, lambda: bool(page.cfg), 15)
    assert not page.mkt_rows and "Список пуст" in page.mkt_empty.text()
    assert page.mkt_pick.findData(1030000000003) > 0            # известная структура — в выпадающем списке
    page._add_market(1030000000003)                             # стоит в GPLB-C, хаб — C-J6MT
    texts = " ".join(lb.text() for lb in page.mkt_rows[1030000000003].findChildren(QLabel))
    assert "не в системе хаба" in texts
    page.mkt_id.setValue(123456789012)
    page._add_market(page.mkt_id.int_value(0))                  # по id — система не известна
    texts = " ".join(lb.text() for lb in page.mkt_rows[123456789012].findChildren(QLabel))
    assert "не известна" in texts
    page.save()
    path = str(tmp_path / "forge.toml")
    assert wait_until(qapp, lambda: config_mod.load(path).structures.market_structures
                      == [1030000000003, 123456789012], 10)


def test_settings_facility_system_roundtrip(win, qapp, tmp_path):
    win.go("settings")
    page = win.pages["settings"]
    assert wait_until(qapp, lambda: bool(page.cfg), 15)
    page._add_fac({"name": "Копирка", "role": "copy", "location_id": 1030000000003})
    ed = page.fac_editors[-1]
    assert ed.system_id == 0 and "авто" in ed.system_label.text()
    ed._set_system(30000772, "C-J6MT", -0.2)
    assert "выбрана вручную: C-J6MT" in ed.system_label.text()
    page.save()
    path = str(tmp_path / "forge.toml")
    assert wait_until(qapp, lambda: bool(config_mod.load(path).facilities), 10)
    f = config_mod.load(path).facilities[0]
    assert f.role == "copy" and f.system_id == 30000772 and f.location_id == 1030000000003
    # после перезагрузки раздела — редактор показывает ручной выбор, сервис — реально используемую
    assert wait_until(qapp, lambda: bool(page.fac_editors) and page.fac_editors[0].system_id == 30000772, 10)
    assert "выбрана вручную" in page.fac_editors[0].system_label.text()


def test_relogin_shown_in_dashboard_and_characters(win, qapp, tmp_path):
    """EVE SSO не принял вход персонажа (пометка после синка) — «Обзор» перечисляет, кого
    переавторизовать, «Персонажи» — «нужен вход» в колонке «Обновлён»."""
    from forge.ingest.character import tokens
    conn = storage.connect(str(tmp_path / "forge.db"))
    conn.execute("UPDATE characters SET refresh_token = ? WHERE character_id = 8", (tokens.MARKER_RELOGIN,))
    conn.commit()
    conn.close()
    dash = win.pages["dash"]
    win.hub.poll()
    assert wait_until(qapp, lambda: "Alt" in dash.relogin_status.text(), 10)
    assert "Нужно войти заново (1)" in dash.relogin_status.text() and "Igor" not in dash.relogin_status.text()

    win.go("chars")
    page = win.pages["chars"]
    page.load()

    def updated(name):
        for r in range(page.table.rowCount()):
            it = page.table.item(r, 3)
            if it is not None and it.text() == name:
                return page.table.item(r, 10).text()
        return ""
    assert wait_until(qapp, lambda: updated("Alt").startswith("нужен вход"), 10)
    assert not updated("Igor").startswith("нужен вход")


def test_recommend_page_explains_daily_volume(win, qapp):
    win.go("recommend")
    page = win.pages["recommend"]
    assert wait_until(qapp, lambda: "Объём/сут" in page.t_liq.text(), 10)
    assert "по записям" in page.t_liq.text()
    assert "хаб покупки" in page.c_liq.text()


def test_calculator_invention_chance_rows():
    from forge.desktop.pages.calculator import _chance_rows
    rows = _chance_rows({"base_probability": 0.3, "skill_mult": 1.225, "skills_enabled": True,
                         "inventor_name": "Igor", "decryptor_mult": 1.1})
    assert rows == [("Базовый шанс (SDE)", "30.0%"), ("× скиллы, лучший инвентор: Igor", "×1.225"),
                    ("× декриптор", "×1.10")]
    off = _chance_rows({"base_probability": 0.3, "skills_enabled": False, "decryptor_mult": 1.0})
    assert len(off) == 2 and "не учитываются" in off[1][0]
    nobody = _chance_rows({"base_probability": 0.3, "skills_enabled": True, "inventor_name": None})
    assert "нет назначенных" in nobody[1][0]
    assert _chance_rows({"probability": 0.3}) == []      # payload без составляющих шанса


def test_parse_fit_text():
    from forge.desktop.pages.basket_panel import parse_fit_text
    fit = "[Ishtar, мой]\nDrone Damage Amplifier II\nDrone Damage Amplifier II\n\nHammerhead II x5\n"
    assert parse_fit_text(fit) == [("Ishtar", 1), ("Drone Damage Amplifier II", 2), ("Hammerhead II", 5)]
    cargo = "Tritanium\t1,134\tMineral\tMaterial\nPyerite\t20\tMineral\tMaterial\n"
    assert parse_fit_text(cargo) == [("Tritanium", 1134), ("Pyerite", 20)]
