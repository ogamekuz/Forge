"""web — локальный HTTP-слой Forge: сервисный фасад (``service``), отчёты по стройкам
(``report``) и FastAPI-сервер отчётов (``app``). Сеть наружу не делает.

``create_app`` импортируется лениво: пульт (``forge.desktop``) пользуется ``service`` и
``report`` без FastAPI в памяти, пока не поднят сервер отчётов.
"""

from __future__ import annotations

from typing import Any


def create_app(*args: Any, **kwargs: Any):
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = ["create_app"]
