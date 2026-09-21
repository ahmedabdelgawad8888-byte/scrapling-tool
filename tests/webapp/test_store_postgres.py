"""Postgres-backed store tests — the same contract as the SQLite suite.

Skipped unless SUPABASE_DB_URL (or DATABASE_URL) points at a real database,
because these hit the network. Run them once after configuring the DSN to prove
the Supabase backend is live:

    SUPABASE_DB_URL='postgresql://...' pytest tests/webapp/test_store_postgres.py

Unlike the SQLite suite these share a database with real data, so every
assertion is a delta and every row created is cleaned up again.
"""
from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DSN = (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "").strip()

pytestmark = pytest.mark.skipif(
    not _DSN, reason="no SUPABASE_DB_URL/DATABASE_URL configured"
)


@pytest.fixture()
def store():
    """The store module, forced onto the Postgres backend."""
    import webapp.store as store_mod

    importlib.reload(store_mod)
    store_mod.init()
    # A silent fallback to SQLite would make every assertion below pass while
    # testing nothing, so fail loudly instead.
    assert store_mod.BACKEND == "postgres", (
        "expected the Postgres backend; the store fell back to SQLite — "
        "check the DSN and that psycopg is installed"
    )
    return store_mod


@pytest.fixture()
def cleanup(store):
    """Delete whatever the test created, even if it fails part way."""
    runs: list[str] = []
    scheds: list[str] = []
    yield runs, scheds
    for run_id in runs:
        store.delete_run(run_id)
    for sched_id in scheds:
        store.delete_schedule(sched_id)


def test_run_round_trips_with_results(store, cleanup) -> None:
    runs, _ = cleanup
    run_id = store.create_run("scrape", {"urls": ["https://example.com"]}, label="nightly")
    runs.append(run_id)
    store.finish_run(run_id, "done", [{"status": 200, "username": "nasa", "followers": "104M"}])

    run = store.get_run(run_id)
    assert run["kind"] == "scrape"
    assert run["label"] == "nightly"
    assert run["status"] == "done"
    # jsonb must come back as a dict, not a string.
    assert run["params"] == {"urls": ["https://example.com"]}
    assert [r["username"] for r in run["results"]] == ["nasa"]


def test_counts_separate_blocked_from_ok(store, cleanup) -> None:
    """A soft-blocked row is HTTP 200 — it must not inflate the ok count."""
    runs, _ = cleanup
    run_id = store.create_run("scrape", {})
    runs.append(run_id)
    store.finish_run(run_id, "done", [
        {"status": 200, "blocked": "empty_profile", "error": "blocked: ..."},
        {"status": 200, "followers": "5"},
        {"status": 0, "error": "timeout"},
    ])
    run = store.get_run(run_id)
    assert (run["total"], run["ok"], run["blocked"], run["errors"]) == (3, 1, 1, 1)


def test_missing_run_is_none(store) -> None:
    assert store.get_run("nope") is None


def test_delete_cascades_to_results(store) -> None:
    """delete_run drops its results via ON DELETE CASCADE, leaving no orphans."""
    before = store.stats()["results"]
    run_id = store.create_run("scrape", {})
    store.finish_run(run_id, "done", [{"status": 200}, {"status": 200}])
    assert store.stats()["results"] == before + 2

    store.delete_run(run_id)
    assert store.get_run(run_id) is None
    assert store.stats()["results"] == before


def test_runs_are_listed_newest_first(store, cleanup) -> None:
    runs, _ = cleanup
    first = store.create_run("scrape", {})
    runs.append(first)
    store.finish_run(first, "done", [])
    time.sleep(0.01)
    second = store.create_run("discover", {})
    runs.append(second)
    store.finish_run(second, "done", [])
    assert [r["id"] for r in store.list_runs()][:2] == [second, first]


def test_large_result_set_batches(store, cleanup) -> None:
    """finish_run writes the whole set in one pipelined round trip."""
    runs, _ = cleanup
    run_id = store.create_run("scrape", {})
    runs.append(run_id)
    store.finish_run(run_id, "done", [{"status": 200, "i": i} for i in range(500)])
    run = store.get_run(run_id)
    assert run["total"] == 500
    # Order matters: the dashboard replays results by index.
    assert [r["i"] for r in run["results"]][:5] == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
def test_schedule_is_not_due_before_its_interval(store, cleanup) -> None:
    _, scheds = cleanup
    sched = store.create_schedule("nightly", "scrape", {"urls": []}, interval_min=60)
    scheds.append(sched["id"])
    assert sched["id"] not in [s["id"] for s in store.due_schedules()]


def test_schedule_becomes_due_once_the_interval_passes(store, cleanup) -> None:
    _, scheds = cleanup
    sched = store.create_schedule("nightly", "scrape", {"urls": []}, interval_min=60)
    scheds.append(sched["id"])
    later = time.time() + 61 * 60
    assert sched["id"] in [s["id"] for s in store.due_schedules(now=later)]


def test_marking_a_run_pushes_the_next_slot_out(store, cleanup) -> None:
    _, scheds = cleanup
    sched = store.create_schedule("nightly", "scrape", {}, interval_min=30)
    scheds.append(sched["id"])
    store.mark_schedule_ran(sched["id"], "run123")
    updated = store.get_schedule(sched["id"])
    assert updated["last_run_id"] == "run123"
    assert updated["next_run_at"] > time.time() + 29 * 60


def test_paused_schedules_never_come_due(store, cleanup) -> None:
    _, scheds = cleanup
    sched = store.create_schedule("nightly", "scrape", {}, interval_min=1)
    scheds.append(sched["id"])
    store.set_schedule_enabled(sched["id"], False)
    # `enabled` is a real boolean in Postgres, not SQLite's 0/1.
    assert store.get_schedule(sched["id"])["enabled"] is False
    assert sched["id"] not in [
        s["id"] for s in store.due_schedules(now=time.time() + 86400)
    ]


def test_interval_is_floored_at_one_minute(store, cleanup) -> None:
    """A zero interval would make the scheduler fire on every tick forever."""
    _, scheds = cleanup
    sched = store.create_schedule("hot", "scrape", {}, interval_min=0)
    scheds.append(sched["id"])
    assert sched["interval_min"] == 1


def test_delete_schedule(store) -> None:
    sched = store.create_schedule("nightly", "scrape", {}, interval_min=60)
    store.delete_schedule(sched["id"])
    assert store.get_schedule(sched["id"]) is None
