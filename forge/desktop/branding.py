"""Подпись автора: альянс и корпорация (логотипы в шапке пульта) и персонаж для ISK-донатов.

Это не «решения игрока» из правила 8 (хабы, склад, роли — те в ``forge.toml``), а автор Forge:
чей флаг в шапке и куда слать ISK. Меняется здесь, в одном месте. Картинки — с Image Server CCP
(``icons.EveImages``), как иконки предметов.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import __version__

# Версия на вывеске (шапка, заголовок окна): «3.1» из «3.1.0», а у исправлений — целиком («3.1.2»).
VERSION = ".".join(__version__.split(".")[:2]) if __version__.endswith(".0") else __version__


@dataclass(frozen=True)
class Entity:
    id: int
    name: str
    ticker: str = ""


ALLIANCE = Entity(1354830081, "Goonswarm Federation", "CONDI")
CORPORATION = Entity(98366055, "Ministry of Offense", "M4O")
DONATE_TO = Entity(95281516, "Kalaratri Omanid")
DONATE_REASON = "Forge"  # что вписать в назначение перевода — чтобы донат не спутать с другими
