"""sync_state: реконсиляция зависшего 'running' и чистка expires в mark_running."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from forge.storage import sync_state


def _set(conn, source, status, last_run, expires=None):
    conn.execute(
        "INSERT INTO sync_state(source, last_run, status, expires) VALUES (?,?,?,?)",
        (source, last_run, status, expires),
    )
    conn.commit()


def test_reap_stale_marks_old_running_as_error(conn):
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _set(conn, "market", "running", old)
    reaped = sync_state.reap_stale(conn, max_age_minutes=30)
    assert reaped == ["market"]
    row = sync_state.get(conn, "market")
    assert row["status"] == "error"
    assert "прервано" in row["note"]


def test_reap_stale_leaves_recent_running(conn):
    recent = datetime.now(UTC).isoformat()
    _set(conn, "market", "running", recent)
    assert sync_state.reap_stale(conn, max_age_minutes=30) == []
    assert sync_state.get(conn, "market")["status"] == "running"


def test_reap_stale_zero_age_resets_any_running(conn):
    """На старте нового синка (max_age_minutes=0) сбрасывается любой висящий running."""
    recent = datetime.now(UTC).isoformat()
    _set(conn, "market", "running", recent)
    assert sync_state.reap_stale(conn, max_age_minutes=0) == ["market"]
    assert sync_state.get(conn, "market")["status"] == "error"


def test_reap_stale_ignores_non_running(conn):
    _set(conn, "industry", "ok", datetime.now(UTC).isoformat())
    assert sync_state.reap_stale(conn, max_age_minutes=0) == []
    assert sync_state.get(conn, "industry")["status"] == "ok"


def test_mark_running_clears_expires(conn):
    """Решили обновлять → старое окно кэша не должно прятать незавершённый run."""
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    sync_state.mark_success(conn, "market", rows=10, expires=future)
    assert sync_state.is_cache_fresh(conn, "market") is True

    sync_state.mark_running(conn, "market")
    assert sync_state.get(conn, "market")["expires"] is None
    assert sync_state.is_cache_fresh(conn, "market") is False
