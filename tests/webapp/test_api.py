"""API tests for the dashboard backend.

The scraping engine is stubbed out: these cover the job/stream/history wiring,
not Scrapling itself, so they stay fast and never touch the network.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPLING_DATA_DIR", str(tmp_path))
    import webapp.store as store_mod

    importlib.reload(store_mod)
    import webapp.server as server_mod

    importlib.reload(server_mod)

    # A stand-in for the real fetch: one canned profile per URL.
    async def fake_scrape_one(url, mode, timeout, sem, **kwargs):
        await asyncio.sleep(0)
        return {
            "url": url, "profile_url": url, "platform": "tiktok",
            "username": url.rstrip("/").split("/")[-1].lstrip("@"),
            "full_name": "Test User", "followers": "1000",
            "status": 200, "error": "", "response_time": 0.1,
        }

    monkeypatch.setattr(server_mod.U, "_scrape_one", fake_scrape_one)

    # Patch the probe, not the cached flag: the startup event runs the probe
    # and would overwrite a pre-set flag on a machine that has Chromium.
    async def no_browser() -> bool:
        server_mod._browser_ok = False
        return False

    monkeypatch.setattr(server_mod, "detect_browser", no_browser)
    with TestClient(server_mod.create_app()) as test_client:
        yield test_client


def _drain(client, job_id) -> list[dict]:
    """Read the SSE stream to completion and return the decoded events."""
    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event)
            if event["type"] == "finished":
                break
    return events


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------
def test_health_reports_capabilities(client) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "tiktok" in body["platforms"]


def test_health_hides_browser_modes_when_chromium_is_absent(client) -> None:
    """Offering a mode the host cannot run would fail at scrape time instead."""
    assert client.get("/api/health").json()["modes"] == ["http"]


def test_index_is_served(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Scrapling" in response.text


def test_unknown_api_path_stays_json(client) -> None:
    """The SPA fallback must not swallow API 404s and return HTML."""
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
def test_scrape_job_streams_one_event_per_url(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape",
        "params": {"urls": ["https://www.tiktok.com/@a", "https://www.tiktok.com/@b"]},
    }).json()
    events = _drain(client, job["job_id"])

    progress = [e for e in events if e["type"] == "progress"]
    assert len(progress) == 2
    assert {e["result"]["username"] for e in progress} == {"a", "b"}

    finished = events[-1]
    assert finished["status"] == "done"
    assert finished["ok"] == 2
    assert finished["blocked"] == 0


def test_unknown_job_kind_is_rejected(client) -> None:
    response = client.post("/api/jobs", json={"kind": "nonsense", "params": {}})
    assert response.status_code == 400


def test_job_with_no_usable_urls_errors_rather_than_hanging(client) -> None:
    """Empty and whitespace-only entries must not leave the job running forever.

    Note bare words like "not-a-url" are *not* rejected: the engine's
    canonicaliser prepends https://, so they become real (if unreachable)
    URLs and fail at fetch time instead.
    """
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["", "   "]},
    }).json()
    finished = _drain(client, job["job_id"])[-1]
    assert finished["status"] == "error"


def test_events_for_unknown_job_are_404(client) -> None:
    assert client.get("/api/jobs/deadbeef/events").status_code == 404


def test_cancelling_a_finished_job_conflicts(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    _drain(client, job["job_id"])
    assert client.post(f"/api/jobs/{job['job_id']}/cancel").status_code == 409


# --------------------------------------------------------------------------
# History and export
# --------------------------------------------------------------------------
def test_finished_job_appears_in_history_with_its_results(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    finished = _drain(client, job["job_id"])[-1]

    runs = client.get("/api/runs").json()["runs"]
    assert [r["id"] for r in runs] == [finished["run_id"]]

    run = client.get(f"/api/runs/{finished['run_id']}").json()
    assert run["results"][0]["username"] == "a"


@pytest.mark.parametrize("fmt", ["csv", "json", "jsonl", "md", "html", "xlsx"])
def test_every_export_format_returns_a_body(client, fmt: str) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    finished = _drain(client, job["job_id"])[-1]
    response = client.get(f"/api/runs/{finished['run_id']}/export?format={fmt}")
    assert response.status_code == 200
    assert len(response.content) > 0


def test_unknown_export_format_is_rejected(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    finished = _drain(client, job["job_id"])[-1]
    assert client.get(f"/api/runs/{finished['run_id']}/export?format=pdf").status_code == 400


def test_run_can_be_deleted(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    finished = _drain(client, job["job_id"])[-1]
    client.delete(f"/api/runs/{finished['run_id']}")
    assert client.get(f"/api/runs/{finished['run_id']}").status_code == 404


# --------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------
def test_schedule_crud(client) -> None:
    created = client.post("/api/schedules", json={
        "name": "nightly", "kind": "scrape", "interval_min": 120,
        "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    assert created["name"] == "nightly"
    assert client.get("/api/schedules").json()["schedules"][0]["enabled"] is True

    paused = client.post(f"/api/schedules/{created['id']}/toggle?enabled=false").json()
    assert paused["enabled"] is False

    client.delete(f"/api/schedules/{created['id']}")
    assert client.get("/api/schedules").json()["schedules"] == []


def test_schedule_rejects_unknown_kind(client) -> None:
    response = client.post("/api/schedules", json={
        "name": "bad", "kind": "nonsense", "interval_min": 60,
    })
    assert response.status_code == 400
