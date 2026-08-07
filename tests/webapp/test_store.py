"""Tests for run/schedule persistence — a temp database, no network."""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A fresh store pointed at a throwaway directory."""
    monkeypatch.setenv("SCRAPLING_DATA_DIR", str(tmp_path))
    import webapp.store as store_mod

    importlib.reload(store_mod)
    store_mod.init()
    return store_mod


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------
def test_run_round_trips_with_results(store) -> None:
    run_id = store.create_run("scrape", {"urls": ["https://example.com"]}, label="nightly")
    store.finish_run(run_id, "done", [{"status": 200, "username": "nasa", "followers": "104M"}])

    run = store.get_run(run_id)
    assert run["kind"] == "scrape"
    assert run["label"] == "nightly"
    assert run["status"] == "done"
    assert run["params"] == {"urls": ["https://example.com"]}
    assert [r["username"] for r in run["results"]] == ["nasa"]


def test_counts_separate_blocked_from_ok(store) -> None:
    """A soft-blocked row is HTTP 200 — it must not inflate the ok count."""
    run_id = store.create_run("scrape", {})
    store.finish_run(run_id, "done", [
        {"status": 200, "blocked": "empty_profile", "error": "blocked: ..."},
        {"status": 200, "followers": "5"},
        {"status": 0, "error": "timeout"},
    ])
    run = store.get_run(run_id)
    assert (run["total"], run["ok"], run["blocked"], run["errors"]) == (3, 1, 1, 1)


def test_cancelled_run_keeps_partial_results(store) -> None:
    run_id = store.create_run("scrape", {})
    store.finish_run(run_id, "cancelled", [{"status": 200, "username": "a"}])
    run = store.get_run(run_id)
    assert run["status"] == "cancelled"
    assert run["total"] == 1


def test_missing_run_is_none(store) -> None:
    assert store.get_run("nope") is None


def test_delete_removes_run_and_results(store) -> None:
    run_id = store.create_run("scrape", {})
    store.finish_run(run_id, "done", [{"status": 200}])
    store.delete_run(run_id)
    assert store.get_run(run_id) is None
    assert store.stats()["results"] == 0


def test_runs_are_listed_newest_first(store) -> None:
    first = store.create_run("scrape", {})
    store.finish_run(first, "done", [])
    time.sleep(0.01)
    second = store.create_run("discover", {})
    store.finish_run(second, "done", [])
    assert [r["id"] for r in store.list_runs()][:2] == [second, first]


# --------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------
def test_schedule_is_not_due_before_its_interval(store) -> None:
    store.create_schedule("nightly", "scrape", {"urls": []}, interval_min=60)
    assert store.due_schedules() == []


def test_schedule_becomes_due_once_the_interval_passes(store) -> None:
    sched = store.create_schedule("nightly", "scrape", {"urls": []}, interval_min=60)
    later = time.time() + 61 * 60
    assert [s["id"] for s in store.due_schedules(now=later)] == [sched["id"]]


def test_marking_a_run_pushes_the_next_slot_out(store) -> None:
    sched = store.create_schedule("nightly", "scrape", {}, interval_min=30)
    store.mark_schedule_ran(sched["id"], "run123")
    updated = store.get_schedule(sched["id"])
    assert updated["last_run_id"] == "run123"
    assert updated["next_run_at"] > time.time() + 29 * 60


def test_paused_schedules_never_come_due(store) -> None:
    sched = store.create_schedule("nightly", "scrape", {}, interval_min=1)
    store.set_schedule_enabled(sched["id"], False)
    assert store.due_schedules(now=time.time() + 86400) == []


def test_interval_is_floored_at_one_minute(store) -> None:
    """A zero interval would make the scheduler fire on every tick forever."""
    sched = store.create_schedule("hot", "scrape", {}, interval_min=0)
    assert sched["interval_min"] == 1


def test_delete_schedule(store) -> None:
    sched = store.create_schedule("nightly", "scrape", {}, interval_min=60)
    store.delete_schedule(sched["id"])
    assert store.get_schedule(sched["id"]) is None
