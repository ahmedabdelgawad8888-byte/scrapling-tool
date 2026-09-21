"""Tests for the platform-grade additions: dashboard, saved jobs, retries,
webhooks/notifications, settings, and audit logging.

Like test_api.py, the scrape engine is stubbed so these stay fast and offline.
"""
from __future__ import annotations

import asyncio
import importlib
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

    # Canned fetch: the URL bottom segment is the username; "@fail" errors.
    async def fake_scrape_one(url, mode, timeout, sem, **kwargs):
        await asyncio.sleep(0)
        username = url.rstrip("/").split("/")[-1].lstrip("@")
        if username.startswith("fail"):
            return {
                "url": url, "profile_url": url, "platform": "tiktok",
                "username": username, "status": 0, "error": "timeout",
                "blocked": "", "response_time": 0.1,
            }
        return {
            "url": url, "profile_url": url, "platform": "tiktok",
            "username": username, "full_name": "Test User", "followers": "1000",
            "status": 200, "error": "", "response_time": 0.1,
        }

    monkeypatch.setattr(server_mod.U, "_scrape_one", fake_scrape_one)

    async def no_browser() -> bool:
        server_mod._browser_ok = False
        return False

    monkeypatch.setattr(server_mod, "detect_browser", no_browser)
    with TestClient(server_mod.create_app()) as test_client:
        yield test_client


def _drain(client, job_id) -> list[dict]:
    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = __import__("json").loads(line[6:])
            events.append(event)
            if event["type"] == "finished":
                break
    return events


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
def test_dashboard_is_built_from_real_data(client) -> None:
    assert client.get("/api/dashboard").json()["stats"]["total_runs"] == 0

    job = client.post("/api/jobs", json={
        "kind": "scrape",
        "params": {"urls": ["https://www.tiktok.com/@a", "https://www.tiktok.com/@b"]},
    }).json()
    _drain(client, job["job_id"])

    data = client.get("/api/dashboard").json()
    assert data["stats"]["total_runs"] == 1
    assert data["stats"]["runs_today"] == 1
    assert data["stats"]["total_rows"] == 2
    assert data["stats"]["error_runs"] == 0
    assert data["timeseries"][-1]["runs"] == 1
    assert data["activity"][0]["status"] in ("done", "error")


# --------------------------------------------------------------------------
# Saved jobs
# --------------------------------------------------------------------------
def test_saved_job_crud_and_run(client) -> None:
    created = client.post("/api/jobs/saved", json={
        "name": "My scraper", "kind": "scrape", "pinned": True,
        "params": {"urls": ["https://www.tiktok.com/@nasa"]},
        "options": {"mode": "http", "concurrency": 4},
        "tags": ["tiktok"],
    }).json()
    assert created["name"] == "My scraper"
    assert created["pinned"] is True
    assert created["tags"] == ["tiktok"]

    listed = client.get("/api/jobs/saved").json()["jobs"]
    assert [j["id"] for j in listed] == [created["id"]]

    updated = client.patch(f"/api/jobs/saved/{created['id']}", json={
        "description": "daily run", "archived": True,
    }).json()
    assert updated["description"] == "daily run"
    assert updated["archived"] is True

    client.patch(f"/api/jobs/saved/{created['id']}", json={"archived": False})

    run = client.post(f"/api/jobs/saved/{created['id']}/run").json()
    _drain(client, run["job_id"])
    saved = client.get(f"/api/jobs/saved/{created['id']}").json()
    assert saved["run_count"] == 1
    assert saved["last_run_status"] in ("done", "error")

    copy = client.post(f"/api/jobs/saved/{created['id']}/duplicate").json()
    assert copy["id"] != created["id"]
    assert copy["name"].startswith("My scraper")

    client.delete(f"/api/jobs/saved/{created['id']}")
    assert client.get(f"/api/jobs/saved/{created['id']}").status_code == 404


def test_saved_job_rejects_bad_kind(client) -> None:
    resp = client.post("/api/jobs/saved", json={"name": "x", "kind": "nope"})
    assert resp.status_code == 400


def test_saved_job_unknown_run_is_404(client) -> None:
    assert client.post("/api/jobs/saved/nope/run").status_code == 404


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------
def test_retry_only_reprocesses_failed_urls(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape",
        "params": {"urls": [
            "https://www.tiktok.com/@good",
            "https://www.tiktok.com/@fail1",
        ]},
    }).json()
    finished = _drain(client, job["job_id"])[-1]
    assert finished["ok"] == 1
    assert finished["errors"] == 1

    retried = client.post(f"/api/runs/{finished['run_id']}/retry").json()
    assert retried["kind"] == "scrape"
    assert retried["retried"] == 1
    retry_events = _drain(client, retried["job_id"])
    progress = [e for e in retry_events if e["type"] == "progress"]
    assert len(progress) == 1
    assert progress[0]["result"]["username"] == "fail1"


def test_retry_with_nothing_failed_is_rejected(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape",
        "params": {"urls": ["https://www.tiktok.com/@good"]},
    }).json()
    finished = _drain(client, job["job_id"])[-1]
    assert client.post(f"/api/runs/{finished['run_id']}/retry").status_code == 400


def test_retry_bad_scope_is_rejected(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@good"]},
    }).json()
    finished = _drain(client, job["job_id"])[-1]
    resp = client.post(f"/api/runs/{finished['run_id']}/retry", json={"scope": "noop"})
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# Notifications + webhooks
# --------------------------------------------------------------------------
def test_job_completion_creates_notification(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    _drain(client, job["job_id"])

    data = client.get("/api/notifications").json()
    assert data["unread"] >= 1
    assert any("finished" in n["message"] for n in data["notifications"])

    ids = [n["id"] for n in data["notifications"]]
    client.post("/api/notifications/read", json={"ids": ids})
    assert client.get("/api/notifications").json()["unread"] == 0


def test_webhooks_are_fired_on_completion(client, monkeypatch) -> None:
    import httpx

    import webapp.store as store_mod

    deliveries = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a, **k):
            pass

        async def post(self, url, json=None):
            deliveries.append((url, json))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    store_mod.set_setting("webhooks", ["http://hook.example/in"])

    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    _drain(client, job["job_id"])

    # Delivery happens in fire-and-forget tasks; wait for them to land.
    import time

    deadline = time.time() + 3
    while time.time() < deadline and not deliveries:
        time.sleep(0.05)
    assert deliveries, "expected a human-lived webhook delivery"
    url, payload = deliveries[-1]
    assert url == "http://hook.example/in"
    assert payload["event"] in ("job.completed",)
    assert payload["job_id"] == job["job_id"]

    store_mod.set_setting("webhooks", [])


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def test_settings_get_and_patch(client) -> None:
    body = client.get("/api/settings").json()["settings"]
    assert body["default_export"] == "csv"

    patched = client.patch("/api/settings", json={"default_export": "xlsx"}).json()
    assert patched["settings"]["default_export"] == "xlsx"

    unknown = client.patch("/api/settings", json={"surprise": True})
    assert unknown.status_code == 400

    client.patch("/api/settings", json={"default_export": "csv"})


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------
def test_audit_records_key_actions(client) -> None:
    created = client.post("/api/jobs/saved", json={"name": "n", "kind": "scrape"}).json()
    client.delete(f"/api/jobs/saved/{created['id']}")

    entries = client.get("/api/audit").json()["entries"]
    actions = [e["action"] for e in entries]
    assert "job.create" in actions
    assert "job.delete" in actions


def test_runs_can_be_filtered(client) -> None:
    job = client.post("/api/jobs", json={
        "kind": "scrape", "params": {"urls": ["https://www.tiktok.com/@a"]},
    }).json()
    _drain(client, job["job_id"])
    filtered = client.get("/api/runs?kind=scrape&status=done").json()
    assert len(filtered["runs"]) == 1
    assert client.get("/api/runs?kind=discover").json()["runs"] == []
