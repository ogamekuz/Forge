"""Общие фикстуры тестов. Живых сетевых вызовов нет — всё мокается."""

from __future__ import annotations

import sqlite3

import pytest

from forge import i18n, storage


@pytest.fixture(autouse=True)
def _russian_by_default():
    """Язык интерфейса — на весь процесс: каждый тест начинает по-русски (язык по умолчанию) и не
    оставляет английский следующему."""
    i18n.set_lang("ru")
    yield
    i18n.set_lang("ru")


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory БД с инициализированной схемой."""
    c = storage.connect(":memory:")
    storage.init_db(c)
    yield c
    c.close()
