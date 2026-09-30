"""Cost indices: маппинг активностей + синк через MockTransport."""

from __future__ import annotations

import httpx

from forge.ingest import industry
from forge.ingest.esi import ESI_BASE, EsiClient
from forge.storage import repositories as repo

SAMPLE = [
    {
        "solar_system_id": 30000142,
        "cost_indices": [
            {"activity": "manufacturing", "cost_index": 0.05},
            {"activity": "invention", "cost_index": 0.02},
            {"activity": "reaction", "cost_index": 0.01},
            {"activity": "some_future_activity", "cost_index": 0.99},
        ],
    }
]


def test_parse_cost_indices_maps_activities():
    rows = industry.parse_cost_indices(SAMPLE)
    by_activity = {r.activity_id: r.cost_index for r in rows}
    assert by_activity[1] == 0.05   # manufacturing
    assert by_activity[8] == 0.02   # invention
    assert by_activity[11] == 0.01  # reaction
    assert 0.99 not in by_activity.values()  # неизвестная активность пропущена


def test_sync_writes_indices(conn):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE, headers={"X-Pages": "1"})

    esi = EsiClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url=ESI_BASE))
    n, _expires = industry.sync(conn, esi)
    conn.commit()
    assert n == 3  # три известные активности
    assert repo.system_cost_indices(conn).get(system_id=30000142, activity_id=1)["cost_index"] == 0.05
