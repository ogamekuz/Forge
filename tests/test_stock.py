"""core.stock — чистая проверка is_fitted_slot (сквозной тест gplb_on_hand — в test_report.py)."""

from __future__ import annotations

from forge.core.stock import is_fitted_slot


def test_is_fitted_slot_recognizes_fitting_slots():
    assert is_fitted_slot("HiSlot0")
    assert is_fitted_slot("MedSlot3")
    assert is_fitted_slot("LoSlot7")
    assert is_fitted_slot("RigSlot0")
    assert is_fitted_slot("SubSystemSlot1")
    assert is_fitted_slot("ServiceSlot2")


def test_is_fitted_slot_allows_free_storage():
    assert not is_fitted_slot("Cargo")
    assert not is_fitted_slot("Hangar")
    assert not is_fitted_slot("DroneBay")
    assert not is_fitted_slot("FighterBay")
    assert not is_fitted_slot("SpecializedOreHold")
    assert not is_fitted_slot("ExpeditionHold")


def test_is_fitted_slot_treats_unknown_flag_as_available():
    """None/пусто (флаг ещё не пересинкан после этой фичи) — не исключаем предмет молча."""
    assert not is_fitted_slot(None)
    assert not is_fitted_slot("")
