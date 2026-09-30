"""Forge — локальный помощник для индустрии в EVE Online.

Слои строго однонаправленные (импорт только «вниз»):

    sync → ingest → storage ← core ← (recommend, planner) ← interface
                              ↑
                           config

``core``/``recommend``/``planner`` НИКОГДА не импортируют ``ingest``, ``sync`` или сеть.
"""

__version__ = "0.1.0"
