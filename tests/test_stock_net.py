"""web.stock_net: чистые структурные функции (Фаза 1) — key_of/build_key_dag/compute_llc.

Никакого sqlite — деревья собраны вручную из NodeResult/MaterialLine, тем же стилем, что и
tests/test_planner.py/tests/test_consolidate.py (прямой конструктор, без БД)."""

from __future__ import annotations

from forge.core.sourcing import MaterialLine, NodeResult
from forge.web import stock_net


def _node(type_id, name, activity_id, bp_id, runs, streams, produced, lines=None):
    return NodeResult(type_id, name, activity_id, bp_id, runs, streams, produced,
                       0.0, 0.0, 0.0, 0.0, lines=lines or [])


# ------------------------------------------------------------------------------- key_of


def test_key_of_consolidate_true_groups_by_type():
    a = _node(3000, "SharedMat", 1, 4004, 2, 1, 200)
    b = _node(3000, "SharedMat", 1, 4004, 3, 1, 300)  # тот же тип, другой объект
    assert stock_net.key_of(a, True) == stock_net.key_of(b, True)


def test_key_of_consolidate_false_keeps_objects_distinct():
    a = _node(3000, "SharedMat", 1, 4004, 2, 1, 200)
    b = _node(3000, "SharedMat", 1, 4004, 3, 1, 300)  # тот же тип, другой объект
    assert stock_net.key_of(a, False) != stock_net.key_of(b, False)
    assert stock_net.key_of(a, False) == stock_net.key_of(a, False)  # тот же объект — тот же ключ


# ------------------------------------------------------------------------------- build_key_dag


def _linear_chain():
    """Ship -> ComponentA -> SharedMat (без общности вообще)."""
    shared = _node(3000, "SharedMat", 1, 4004, 2, 1, 200)
    comp_a = _node(2500, "ComponentA", 1, 4002, 1, 1, 1,
                    lines=[MaterialLine(3000, "SharedMat", 200, "build", 1.0, 200.0, child=shared)])
    ship = _node(4000, "Ship", 1, 4001, 1, 1, 1,
                 lines=[MaterialLine(2500, "ComponentA", 1, "build", 200.0, 200.0, child=comp_a)])
    return ship, comp_a, shared


def _diamond():
    """Ship -> {ComponentA, ComponentB} -> ОБА тянут ОДИН И ТОТ ЖЕ объект SharedMat."""
    shared = _node(3000, "SharedMat", 1, 4004, 5, 1, 500)
    comp_a = _node(2500, "ComponentA", 1, 4002, 1, 1, 1,
                    lines=[MaterialLine(3000, "SharedMat", 350, "build", 1.0, 350.0, child=shared)])
    comp_b = _node(2501, "ComponentB", 1, 4003, 1, 1, 1,
                    lines=[MaterialLine(3000, "SharedMat", 120, "build", 1.0, 120.0, child=shared)])
    ship = _node(4000, "Ship", 1, 4001, 1, 1, 1,
                 lines=[MaterialLine(2500, "ComponentA", 1, "build", 470.0, 470.0, child=comp_a),
                        MaterialLine(2501, "ComponentB", 1, "build", 120.0, 120.0, child=comp_b)])
    return ship, comp_a, comp_b, shared


def test_build_key_dag_linear_chain():
    ship, comp_a, shared = _linear_chain()
    canonical, parent_keys_of, children_keys_of = stock_net.build_key_dag([ship], True)
    ship_k, a_k, shared_k = (stock_net.key_of(n, True) for n in (ship, comp_a, shared))
    assert canonical[ship_k] is ship
    assert canonical[a_k] is comp_a
    assert canonical[shared_k] is shared
    assert parent_keys_of[ship_k] == set()
    assert parent_keys_of[a_k] == {ship_k}
    assert parent_keys_of[shared_k] == {a_k}
    assert children_keys_of[ship_k] == {a_k}
    assert children_keys_of[a_k] == {shared_k}


def test_build_key_dag_diamond_shares_one_canonical_node_with_two_parents():
    ship, comp_a, comp_b, shared = _diamond()
    canonical, parent_keys_of, _ = stock_net.build_key_dag([ship], True)
    _ship_k, a_k, b_k, shared_k = (stock_net.key_of(n, True) for n in (ship, comp_a, comp_b, shared))
    assert canonical[shared_k] is shared          # один представитель, не два
    assert parent_keys_of[shared_k] == {a_k, b_k}  # оба родителя учтены


def test_build_key_dag_consolidate_false_treats_same_type_as_two_independent_keys():
    """Два РАЗНЫХ объекта одного типа (сценарий: каждая ветка независимо пересобрала
    «свой» SharedMat) при consolidate=False остаются двумя отдельными ключами — ничего не
    сливаем, если юзер явно выключил объединение."""
    shared_a = _node(3000, "SharedMat", 1, 4004, 4, 1, 400)
    shared_b = _node(3000, "SharedMat", 1, 4004, 2, 1, 200)  # тот же тип, другой объект
    comp_a = _node(2500, "ComponentA", 1, 4002, 1, 1, 1,
                    lines=[MaterialLine(3000, "SharedMat", 390, "build", 1.0, 390.0, child=shared_a)])
    comp_b = _node(2501, "ComponentB", 1, 4003, 1, 1, 1,
                    lines=[MaterialLine(3000, "SharedMat", 195, "build", 1.0, 195.0, child=shared_b)])
    ship = _node(4000, "Ship", 1, 4001, 1, 1, 1,
                 lines=[MaterialLine(2500, "ComponentA", 1, "build", 0.0, 0.0, child=comp_a),
                        MaterialLine(2501, "ComponentB", 1, "build", 0.0, 0.0, child=comp_b)])
    canonical, _parent_keys_of, _ = stock_net.build_key_dag([ship], False)
    shared_a_k = stock_net.key_of(shared_a, False)
    shared_b_k = stock_net.key_of(shared_b, False)
    assert shared_a_k != shared_b_k
    assert canonical[shared_a_k] is shared_a
    assert canonical[shared_b_k] is shared_b


# ------------------------------------------------------------------------------- compute_llc


def test_compute_llc_linear_chain():
    ship, comp_a, shared = _linear_chain()
    _, parent_keys_of, _ = stock_net.build_key_dag([ship], True)
    llc = stock_net.compute_llc(parent_keys_of)
    ship_k, a_k, shared_k = (stock_net.key_of(n, True) for n in (ship, comp_a, shared))
    assert llc[ship_k] == 0
    assert llc[a_k] == 1
    assert llc[shared_k] == 2


def test_compute_llc_diamond_shared_node_gets_single_level_below_its_parents():
    ship, comp_a, comp_b, shared = _diamond()
    _, parent_keys_of, _ = stock_net.build_key_dag([ship], True)
    llc = stock_net.compute_llc(parent_keys_of)
    ship_k, a_k, b_k, shared_k = (stock_net.key_of(n, True) for n in (ship, comp_a, comp_b, shared))
    assert llc[ship_k] == 0
    assert llc[a_k] == 1 and llc[b_k] == 1
    assert llc[shared_k] == 2  # один уровень ниже ОБОИХ родителей, не задвоен


def test_compute_llc_picks_deepest_occurrence_not_shallowest():
    """SharedMat нужен ship'у И НАПРЯМУЮ (был бы LLC=1), И через ComponentA (LLC=2) — низкоуровневый
    код обязан взять САМЫЙ ГЛУБОКИЙ уровень встречи, иначе к моменту его обработки спрос от
    ComponentA (обработанного позже) ещё не был бы известен."""
    shared = _node(3000, "SharedMat", 1, 4004, 5, 1, 500)
    comp_a = _node(2500, "ComponentA", 1, 4002, 1, 1, 1,
                    lines=[MaterialLine(3000, "SharedMat", 300, "build", 1.0, 300.0, child=shared)])
    ship = _node(4000, "Ship", 1, 4001, 1, 1, 1,
                 lines=[MaterialLine(2500, "ComponentA", 1, "build", 0.0, 0.0, child=comp_a),
                        MaterialLine(3000, "SharedMat", 50, "build", 1.0, 50.0, child=shared)])
    _, parent_keys_of, _ = stock_net.build_key_dag([ship], True)
    llc = stock_net.compute_llc(parent_keys_of)
    ship_k, a_k, shared_k = (stock_net.key_of(n, True) for n in (ship, comp_a, shared))
    assert llc[ship_k] == 0
    assert llc[a_k] == 1
    assert llc[shared_k] == 2  # не 1 — самый глубокий путь (через ComponentA) выигрывает
