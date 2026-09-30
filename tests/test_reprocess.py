"""forge.core.reprocess — переработка как источник материала (не через чертёж).

Фикстура смоделирована по реальному кейсу (найденному вживую): «Unrefined Dysporite»
перерабатывается в Mercury + Dysporite — этот путь учитывается при выборе
строить/купить, если он дешевле прямой постройки/покупки.
"""

from __future__ import annotations

from forge.core import reprocess


def _seed_dysporite(conn):
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name,volume) VALUES
            (29660,'Unrefined Dysporite',1.0),(16668,'Dysporite',0.2),(16646,'Mercury',0.1);
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity) VALUES
            (29660,1,16646,173),(29660,1,16668,73);
        """
    )
    conn.commit()


def test_reprocessing_sources_finds_precursor_with_main_and_byproduct(conn):
    _seed_dysporite(conn)
    sources = reprocess.reprocessing_sources(conn, 16668)  # ищем прекурсоры для Dysporite
    assert len(sources) == 1
    src = sources[0]
    assert src.source_type_id == 29660
    assert src.source_name == "Unrefined Dysporite"
    assert src.portion_size == 1
    assert src.main == reprocess.ReprocessYield(16668, "Dysporite", 73)
    assert src.byproducts == (reprocess.ReprocessYield(16646, "Mercury", 173),)


def test_reprocessing_sources_finds_precursor_when_target_is_the_byproduct(conn):
    """Один и тот же прекурсор может быть источником ЛЮБОГО из своих выходов — если ищем
    Mercury (а не Dysporite), Unrefined Dysporite тоже найдётся, но роли main/byproducts
    меняются местами под конкретную цель."""
    _seed_dysporite(conn)
    sources = reprocess.reprocessing_sources(conn, 16646)
    assert len(sources) == 1
    assert sources[0].main == reprocess.ReprocessYield(16646, "Mercury", 173)
    assert sources[0].byproducts == (reprocess.ReprocessYield(16668, "Dysporite", 73),)


def test_reprocessing_sources_empty_when_nothing_yields_target(conn):
    _seed_dysporite(conn)
    conn.execute("INSERT INTO sde_types(type_id,name) VALUES (99999,'Unrelated Item')")
    conn.commit()
    assert reprocess.reprocessing_sources(conn, 99999) == []  # вообще нет строк-выходов


def test_reprocessing_sources_excludes_precursor_missing_from_own_output(conn):
    """Защита от мусора: если у прекурсора нет строки с material_type_id==target вообще
    (искали DISTINCT type_id по обратному индексу, но могла быть гонка/неконсистентные данные) —
    не включать его."""
    conn.executescript(
        """
        INSERT INTO sde_types(type_id,name) VALUES (999,'Junk Precursor');
        INSERT INTO sde_reprocessing_materials(type_id,portion_size,material_type_id,quantity)
            VALUES (999,1,16646,10);
        """
    )
    conn.commit()
    _seed_dysporite(conn)
    sources = reprocess.reprocessing_sources(conn, 16668)
    assert all(s.source_type_id != 999 for s in sources)


def test_portions_needed_matches_real_eve_floor_formula():
    # 73 Dysporite/порция, эффективность 75%: floor(73*0.75)=54 за 1 порцию.
    assert reprocess.portions_needed(54, 73, 0.75) == 1
    assert reprocess.portions_needed(55, 73, 0.75) == 2  # 1 порция не хватает (54<55)
    assert reprocess.portions_needed(108, 73, 0.75) == 2  # floor(2*73*0.75)=109 >= 108
    assert reprocess.portions_needed(0, 73, 0.75) == 0
    assert reprocess.portions_needed(10, 73, 0.0) == 0  # эффективность 0 = выключено


def test_portions_needed_rounds_once_on_total_not_per_portion():
    # yield_per_portion=1, efficiency=0.5: округление НА ИТОГ (floor(portions*0.5)), не на
    # каждую порцию отдельно (иначе floor(1*0.5)=0 никогда бы не набрало нужное количество).
    assert reprocess.portions_needed(5, 1, 0.5) == 10  # floor(10*0.5)=5


def test_reprocess_output_scales_main_and_byproducts_together(conn):
    _seed_dysporite(conn)
    src = reprocess.reprocessing_sources(conn, 16668)[0]
    main_qty, byproducts = reprocess.reprocess_output(src, num_portions=2, efficiency=0.75)
    assert main_qty == 109  # floor(2*73*0.75)
    assert byproducts == [(reprocess.ReprocessYield(16646, "Mercury", 173), 259)]  # floor(2*173*0.75)


def test_reprocess_output_zero_efficiency_yields_nothing(conn):
    _seed_dysporite(conn)
    src = reprocess.reprocessing_sources(conn, 16668)[0]
    main_qty, byproducts = reprocess.reprocess_output(src, num_portions=5, efficiency=0.0)
    assert main_qty == 0
    assert byproducts == [(reprocess.ReprocessYield(16646, "Mercury", 173), 0)]
