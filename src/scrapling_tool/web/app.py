"""Small, production-friendly FastAPI layer around the provider pipeline.

The dashboard and API intentionally share the same provider registry. This
keeps deployments lightweight while exposing useful automation endpoints:
health, provider discovery, scraping, JSON-LD/profile extraction, and sitemap
parsing. Network work is performed in a thread so the async server stays
responsive.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl

from scrapling_tool.parsers import extract_profile
from scrapling_tool.providers import list_providers, pick_provider
from scrapling_tool.providers.sitemap import SitemapProvider


class ScrapeRequest(BaseModel):
    url: HttpUrl
    provider: str | None = None
    timeout: float = Field(default=30, ge=1, le=120)


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000_000)
    url: str = ""
    max_recent: int = Field(default=5, ge=0, le=5)


class SitemapRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000_000)


def _profile_dict(profile: Any) -> dict[str, Any]:
    result = {
        "main_caption": profile.main_caption,
        "handle": profile.handle,
        "display_name": profile.display_name,
        "avatar": profile.avatar,
        "source": profile.source,
        "recent_posts": [],
    }
    result["recent_posts"] = [
        {
            "kind": post.kind,
            "caption": post.caption,
            "url": post.url,
            "posted_at": post.posted_at,
        }
        for post in profile.recent_posts
    ]
    return result


def create_app(*, cache_dir: str | Path | None = None) -> FastAPI:
    """Create an isolated application instance, suitable for tests and ASGI."""
    cache_path = Path(cache_dir or Path.home() / ".cache" / "scrapling-tool")
    cache_path.mkdir(parents=True, exist_ok=True)
    app = FastAPI(
        title="Scrapling Tool API",
        version="1.3.1",
        description="Free-first web scraping and structured-data extraction API.",
    )
    app.state.cache_dir = cache_path
    app.state.scrape_count = 0

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        candidates = [
            Path(__file__).resolve().parents[3] / "index.html",
            Path(__file__).resolve().parent / "static" / "index.html",
        ]
        for path in candidates:
            if path.is_file():
                return FileResponse(path, media_type="text/html")
        # A valid response is more useful than a deployment-time 500 when the
        # package was built without the optional dashboard asset.
        raise HTTPException(status_code=404, detail="dashboard asset not packaged")

    @app.get("/healthz")
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "status": "online",
            "version": app.version,
            "providers": len(list_providers()),
        }

    @app.get("/api/providers")
    async def providers() -> dict[str, Any]:
        return {
            "providers": [
                {
                    "name": provider.name,
                    "priority": provider.priority,
                    "free": provider.free,
                    "available": provider.available(),
                }
                for provider in list_providers()
            ]
        }

    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        size = sum(p.stat().st_size for p in cache_path.rglob("*") if p.is_file())
        return {
            "total_scrapes": app.state.scrape_count,
            "history_count": app.state.scrape_count,
            "db_size_bytes": size,
            "playwright_installed": importlib.util.find_spec("playwright") is not None,
        }

    @app.post("/api/sitemap/parse")
    async def parse_sitemap(request: SitemapRequest) -> dict[str, Any]:
        urls = SitemapProvider.parse_xml(request.text)
        return {"urls": urls, "count": len(urls)}

    @app.post("/api/extract")
    async def extract(request: ExtractRequest) -> dict[str, Any]:
        profile = extract_profile(
            request.text,
            url=request.url,
            max_recent=request.max_recent,
        )
        return {"profile": _profile_dict(profile)}

    @app.post("/api/scrape")
    async def scrape(request: ScrapeRequest) -> dict[str, Any]:
        url = str(request.url)
        try:
            provider = pick_provider(url, hint=request.provider)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            result = await asyncio.to_thread(provider.fetch, url, timeout=request.timeout)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{provider.name}: {exc}") from exc
        app.state.scrape_count += 1
        profile = extract_profile(result.body, url=result.final_url)
        return {
            "url": result.url,
            "final_url": result.final_url,
            "status": result.status,
            "provider": result.provider or provider.name,
            "from_cache": result.from_cache,
            "bytes": len(result.body),
            "profile": _profile_dict(profile),
            "meta": result.meta,
        }

    return app


app = create_app()
