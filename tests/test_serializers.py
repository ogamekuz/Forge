"""forge.web.serializers — сериализация NodeResult/BuildEstimate в JSON для /api/*."""

from __future__ import annotations

import dataclasses

from forge.core.sourcing import NodeResult
from forge.web.serializers import node_to_dict


def test_node_to_dict_includes_every_noderesult_field():
    """Новое поле ``NodeResult`` (напр. ``bpc_runs_*`` — «недостача ранов BPC»,
    ``reprocess_*`` — опция переработки) легко забыть добавить в сериализатор: датакласс
    несёт правильные данные, тесты core/report.py зелёные (они читают ``NodeResult``
    напрямую), а клиент ``/api/cost`` получает ``undefined``/``None`` — такое видно ТОЛЬКО
    живой проверкой, не в юнит-тестах. Поэтому сериализатор обязан знать про КАЖДОЕ поле
    датакласса (кроме ``lines``/``missing_prices`` — те сериализуются рекурсивно, не 1-в-1 по имени)."""
    node = NodeResult(1, "X", 1, 100, 1, 1, 1, 0.0, 0.0, 0.0, 0.0)
    out = node_to_dict(node)
    dataclass_fields = {f.name for f in dataclasses.fields(NodeResult)} - {"lines", "missing_prices"}
    missing = dataclass_fields - set(out.keys())
    assert not missing, f"node_to_dict() не сериализует поля NodeResult: {missing}"
