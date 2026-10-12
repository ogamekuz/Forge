"""Forge — локальный помощник для индустрии в EVE Online.

Слои строго однонаправленные (импорт только «вниз»):

    sync → ingest → storage ← core ← (recommend, planner) ← interface
                              ↑
                           config

``core``/``recommend``/``planner`` НИКОГДА не импортируют ``ingest``, ``sync`` или сеть.
"""

# Версия Forge — здесь и в pyproject.toml (``tests/test_version.py`` следит, чтобы совпадали).
# Пульт показывает её на вывеске (``desktop/branding.VERSION``), сборка кладёт в имя архива.
__version__ = "3.1.0"
