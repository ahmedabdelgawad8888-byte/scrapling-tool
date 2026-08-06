"""Tests for the FastAPI web layer — no network, no Playwright."""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from scrapling_tool.web.app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(cache_dir=tmp_path)
    return TestClient(app)


def test_index_serves_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "scrapling" in body.lower()


def test_list_providers_endpoint(client: TestClient) -> None:
    r = client.get("/api/providers")
    assert r.status_code == 200
    data = r.json()
    assert "providers" in data
    names = {p["name"] for p in data["providers"]}
    assert "direct" in names
    assert "scrapling" in names


def test_sitemap_endpoint_with_fixture(tmp_path: Path, client: TestClient) -> None:
    """Point the server at a fake sitemap file and confirm parsing."""
    fake = tmp_path / "sitemap.xml"
    fake.write_text(
        '<?xml version="1.0"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://a/</loc></url>'
        '<url><loc>https://b/</loc></url>'
        '</urlset>',
        encoding="utf-8",
    )
    r = client.post("/api/sitemap/parse", json={"text": fake.read_text()})
    assert r.status_code == 200
    data = r.json()
    assert data["urls"] == ["https://a/", "https://b/"]


def test_scrape_endpoint_rejects_bad_url(client: TestClient) -> None:
    # Empty URL is rejected by Pydantic's HttpUrl validation as 422
    r = client.post("/api/scrape", json={"url": ""})
    assert r.status_code in (400, 422)


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_legacy_health_endpoint(client: TestClient) -> None:
    """The pre-1.1 dashboard polls /api/health for its connection pill."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["status"] == "online"


def test_legacy_stats_endpoint(client: TestClient, tmp_path) -> None:
    """The pre-1.1 dashboard polls /api/stats for dashboard numbers."""
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    # Shape the old panel expects
    for key in ("total_scrapes", "history_count", "db_size_bytes", "playwright_installed"):
        assert key in data


def test_extract_endpoint_with_jsonld(client: TestClient) -> None:
    """The /api/extract endpoint parses a profile from a JSON-LD body."""
    html = """<html><head>
<script type="application/ld+json">
{"@type":"Person","name":"Ada","alternateName":"ada","description":"Writes code.","hasPart":[{"@type":"Article","headline":"Post 1","url":"https://example.com/1"}]}
</script></head><body></body></html>"""
    r = client.post("/api/extract", json={"text": html, "url": "https://example.com/ada"})
    assert r.status_code == 200
    profile = r.json()["profile"]
    assert profile["main_caption"] == "Writes code."
    assert profile["handle"] == "ada"
    assert profile["display_name"] == "Ada"
    assert len(profile["recent_posts"]) == 1
    assert profile["recent_posts"][0]["caption"] == "Post 1"


def test_extract_endpoint_caps_recent_posts(client: TestClient) -> None:
    parts = ",".join(f'{{"@type":"Article","headline":"P{i}"}}' for i in range(20))
    html = f'<html><script type="application/ld+json">{{"@type":"Person","name":"X","description":"d","hasPart":[{parts}]}}</script></html>'
    r = client.post("/api/extract", json={"text": html, "url": "https://example.com/x", "max_recent": 3})
    assert r.status_code == 200
    assert len(r.json()["profile"]["recent_posts"]) == 3
