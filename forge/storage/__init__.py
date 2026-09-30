"""storage — модели, репозитории, миграции (SQLite). Единственная точка доступа к БД.

Без бизнес-логики. ``core``/``recommend``/``planner`` читают данные только отсюда.
"""

from . import models, repositories, sync_state
from .db import connect, transaction
from .migrations import init_db
from .schema import SCHEMA_VERSION

__all__ = [
    "SCHEMA_VERSION",
    "connect",
    "init_db",
    "models",
    "repositories",
    "sync_state",
    "transaction",
]
