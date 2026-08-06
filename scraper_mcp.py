#!/usr/bin/env python3
"""
Social Scraper MCP Server
=========================
Exposes TikTok, Snapchat, Instagram, YouTube, and Twitter scraping as MCP tools
that any MCP-compatible client (Claude Desktop, Cursor, etc.) can call.

Usage:
    python scraper_mcp.py          # stdio transport (for Claude Desktop, Cursor)
    python scraper_mcp.py --sse    # SSE transport (for web clients)
    python scraper_mcp.py --port 8911  # SSE on custom port
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# Logging
_logger = logging.getLogger("scraper_mcp")
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)

# Scrapling fetchers
try:
    from scrapling import AsyncFetcher, DynamicFetcher, StealthyFetcher
    from scrapling.fetchers import AsyncStealthySession, FetcherSession  # noqa: F401  (availability probe)
    HAS_SCRAPLING = True
except ImportError as e:
    HAS_SCRAPLING = False
    _logger.error(f"Scrapling import failed: {e}")

# Import parsers from ultra_scraper.py
sys.path.insert(0, str(Path(__file__).resolve().parent))
_parsers_loaded = False
_load_error = ""
parse_tiktok_fn = None
parse_snapchat_fn = None
parse_instagram_fn = None
parse_generic_fn = None
_platform_of_fn = None
_fetch_http_fn = None
_fetch_browser_fn = None
_fetch_stealth_fn = None


def _load_parsers():
    global _parsers_loaded, _load_error
    global parse_tiktok_fn, parse_snapchat_fn, parse_instagram_fn
    global parse_generic_fn, _platform_of_fn
    global _fetch_http_fn, _fetch_browser_fn, _fetch_stealth_fn
    if _parsers_loaded:
        return True
    try:
        import importlib
        mod = importlib.import_module("ultra_scraper")
        parse_tiktok_fn = getattr(mod, "parse_tiktok", None)
        parse_snapchat_fn = getattr(mod, "parse_snapchat", None)
        parse_instagram_fn = getattr(mod, "parse_instagram", None)
        parse_generic_fn = getattr(mod, "parse_generic", None)
        _platform_of_fn = getattr(mod, "_platform_of", None)
        _fetch_http_fn = getattr(mod, "fetch_http", None)
        _fetch_browser_fn = getattr(mod, "fetch_browser", None)
        _fetch_stealth_fn = getattr(mod, "fetch_stealth", None)
        _parsers_loaded = True
        _logger.info("Parsers loaded successfully from ultra_scraper")
        return True
    except Exception as e:
        _load_error = str(e)
        _logger.error(f"Failed to load parsers from ultra_scraper: {e}")
        return False


server = FastMCP("Social Scraper")


# --- Models ---

class ScrapeResult(BaseModel):
    url: str = Field(description="The profile URL")
    username: str = Field(description="Extracted username")
    platform: str = Field(description="tiktok / snapchat / instagram / youtube / twitter / other / unknown")
    status: int = Field(description="HTTP status code")
    data: dict = Field(description="Extracted profile data (followers, bio, etc.)")
    error: str = Field(default="", description="Error message if any")


# --- URL helpers ---

def _validate_url(url: str) -> tuple[bool, str]:
    """Validate and return (is_valid, normalized_url_or_error)."""
    if not url or not url.strip():
        return False, "Empty URL"
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url.lstrip("/")
    try:
        parsed = urlparse(url)
    except Exception:
        return False, f"Invalid URL: {url}"
    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme: {parsed.scheme}"
    if not parsed.netloc:
        return False, f"No hostname in URL: {url}"
    return True, url


def _platform_of(url: str) -> str:
    """Detect platform from URL."""
    if _platform_of_fn:
        return _platform_of_fn(url)
    u = (url or "").lower()
    if "tiktok.com" in u:
        return "tiktok"
    if "instagram.com" in u:
        return "instagram"
    if "snapchat.com" in u:
        return "snapchat"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    return "other"


def _username_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1].split("?")[0]


# --- Fetcher helpers ---

async def _fetch_url(url: str, mode: str = "http", timeout: int = 30) -> Any:
    """Fetch a URL using Scrapling fetchers."""
    if not HAS_SCRAPLING:
        raise RuntimeError("Scrapling is not installed. Run: pip install scrapling[all]")

    if mode == "browser":
        resp = await DynamicFetcher.async_fetch(url, timeout=timeout * 1000, headless=True)
        return resp
    elif mode == "stealth":
        resp = await StealthyFetcher.async_fetch(url, timeout=timeout * 1000, headless=True)
        return resp
    else:
        resp = await AsyncFetcher.get(url, timeout=timeout, follow_redirects=True)
        return resp


def _parser_for_platform(platform: str):
    """Return the appropriate parser function for a platform."""
    parsers = {
        "tiktok": parse_tiktok_fn,
        "snapchat": parse_snapchat_fn,
        "instagram": parse_instagram_fn,
        "other": parse_generic_fn,
    }
    return parsers.get(platform, parse_generic_fn)


def _build_result(url: str, platform: str, resp: Any, data: dict) -> ScrapeResult:
    """Build a ScrapeResult from response data."""
    username = data.get("username") or _username_from_url(url)
    return ScrapeResult(
        url=url,
        username=username,
        platform=data.get("platform", platform),
        status=getattr(resp, "status", data.get("status", 200)),
        data={k: v for k, v in data.items() if k not in ("url", "platform", "status")},
        error=data.get("error", ""),
    )


async def _scrape_platform(url: str, mode: str = "http", timeout: int = 30) -> ScrapeResult:
    """Universal scrape function: validate, detect platform, fetch, parse."""
    # Validate URL
    valid, result = _validate_url(url)
    if not valid:
        return ScrapeResult(url=url, username="", platform="unknown", status=0, data={}, error=result)

    url = result
    platform = _platform_of(url)
    username = _username_from_url(url)

    _load_parsers()
    if not HAS_SCRAPLING:
        return ScrapeResult(url=url, username=username, platform=platform, status=0, data={},
                            error="Scrapling not installed. Run: pip install scrapling[all]")

    try:
        resp = await _fetch_url(url, mode, timeout)
    except Exception as e:
        _logger.warning(f"Fetch failed for {url}: {e}")
        return ScrapeResult(url=url, username=username, platform=platform, status=0, data={},
                            error=str(e)[:500])

    parser = _parser_for_platform(platform)
    if parser:
        try:
            data = parser(resp, url)
        except Exception as e:
            _logger.warning(f"Parse failed for {url}: {e}")
            data = {"username": username, "platform": platform, "error": f"parse: {e}"[:300]}
    else:
        data = {"username": username, "platform": platform,
                "error": f"No parser available for {platform}. Install ultra_scraper.py."}

    return _build_result(url, platform, resp, data)


# --- MCP Tools ---

@server.tool(
    name="scrape_tiktok",
    description="Scrape a TikTok profile: followers, likes, bio, videos, verified status"
)
async def scrape_tiktok(
    url: str = Field(description="TikTok profile URL (e.g. https://www.tiktok.com/@username)"),
    mode: str = Field(default="http", description="Fetch mode: http, browser, stealth"),
    timeout: int = Field(default=30, description="Timeout in seconds"),
) -> ScrapeResult:
    return await _scrape_platform(url, mode, timeout)


@server.tool(
    name="scrape_snapchat",
    description="Scrape a Snapchat profile: subscribers, bio, location, profile pic"
)
async def scrape_snapchat(
    url: str = Field(description="Snapchat profile URL (e.g. https://www.snapchat.com/add/username)"),
    mode: str = Field(default="http", description="Fetch mode: http, browser, stealth"),
    timeout: int = Field(default=30, description="Timeout in seconds"),
) -> ScrapeResult:
    return await _scrape_platform(url, mode, timeout)


@server.tool(
    name="scrape_instagram",
    description="Scrape an Instagram profile: followers, posts, bio, verified status (may need --login mode)"
)
async def scrape_instagram(
    url: str = Field(description="Instagram profile URL (e.g. https://www.instagram.com/username)"),
    mode: str = Field(default="http", description="Fetch mode: http, browser, stealth"),
    timeout: int = Field(default=30, description="Timeout in seconds"),
) -> ScrapeResult:
    return await _scrape_platform(url, mode, timeout)


@server.tool(
    name="scrape_profile",
    description="Auto-detect platform from URL and scrape a social media profile (TikTok, Snapchat, Instagram, YouTube, Twitter)"
)
async def scrape_profile(
    url: str = Field(description="Social media profile URL"),
    mode: str = Field(default="http", description="Fetch mode: http, browser, stealth"),
    timeout: int = Field(default=30, description="Timeout in seconds"),
) -> ScrapeResult:
    return await _scrape_platform(url, mode, timeout)


@server.tool(
    name="scrape_bulk",
    description="Scrape multiple social media profiles at once. Auto-detects TikTok, Snapchat, Instagram, YouTube, Twitter."
)
async def scrape_bulk(
    urls: list[str] = Field(description="List of social media profile URLs"),
    mode: str = Field(default="http", description="Fetch mode: http, browser, stealth"),
    concurrency: int = Field(default=10, description="Max concurrent requests (1-50)"),
    timeout: int = Field(default=30, description="Timeout per request in seconds"),
) -> list[ScrapeResult]:
    _load_parsers()
    concurrency = max(1, min(concurrency, 50))
    sem = asyncio.Semaphore(concurrency)

    async def _worker(url: str) -> ScrapeResult:
        async with sem:
            return await _scrape_platform(url, mode, timeout)

    tasks = [_worker(url) for url in urls]
    return list(await asyncio.gather(*tasks))


@server.tool(
    name="scrape_generic",
    description="Scrape any website and extract title, description, text content, and links"
)
async def scrape_generic(
    url: str = Field(description="Any website URL"),
    mode: str = Field(default="http", description="Fetch mode: http, browser, stealth"),
    timeout: int = Field(default=30, description="Timeout in seconds"),
    css_selector: str = Field(default="", description="Optional CSS selector to extract specific content"),
) -> ScrapeResult:
    valid, result = _validate_url(url)
    if not valid:
        return ScrapeResult(url=url, username="", platform="other", status=0, data={}, error=result)

    url = result
    _load_parsers()

    try:
        resp = await _fetch_url(url, mode, timeout)
    except Exception as e:
        return ScrapeResult(url=url, username="", platform="other", status=0, data={}, error=str(e)[:500])

    if parse_generic_fn:
        try:
            data = parse_generic_fn(resp, url)
        except Exception as e:
            data = {"url": url, "error": f"parse: {e}"[:300], "platform": "other"}
    else:
        data = {"url": url, "error": "Generic parser not loaded", "platform": "other"}

    return _build_result(url, "other", resp, data)


@server.tool(
    name="health_check",
    description="Check if the scraper is healthy and which parsers are loaded"
)
async def health_check() -> dict:
    _load_parsers()
    return {
        "status": "ok",
        "scrapling_installed": HAS_SCRAPLING,
        "parsers_loaded": _parsers_loaded,
        "load_error": _load_error,
        "available_parsers": {
            "tiktok": parse_tiktok_fn is not None,
            "snapchat": parse_snapchat_fn is not None,
            "instagram": parse_instagram_fn is not None,
            "generic": parse_generic_fn is not None,
        },
        "supported_platforms": ["tiktok", "instagram", "snapchat", "youtube", "twitter"],
    }


# --- Main entry ---

def _parse_args(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Social Scraper MCP Server")
    parser.add_argument("--sse", action="store_true", help="Use SSE transport instead of stdio")
    parser.add_argument("--port", type=int, default=8911, help="Port for SSE transport")
    parser.add_argument("--host", default="127.0.0.1", help="Host for SSE transport")
    parser.add_argument("--install", action="store_true", help="Print install/config guide")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)

    if args.verbose:
        _logger.setLevel(logging.DEBUG)

    if args.install:
        print(_INSTALL_GUIDE)
    elif args.sse:
        print(f"Starting MCP server on http://{args.host}:{args.port}", file=sys.stderr)
        server.run(transport="sse", host=args.host, port=args.port)
    else:
        server.run(transport="stdio")


_INSTALL_GUIDE = r"""
=== CONFIGURING THIS MCP SERVER ===

--- Claude Desktop (claude_desktop_config.json) ---
{
  "mcpServers": {
    "social-scraper": {
      "command": "python",
      "args": ["PATH_TO\scraper_mcp.py"],
      "env": {}
    }
  }
}

--- Cursor (MCP settings) ---
Name: Social Scraper
Type:  command
Command: python
Args: PATH_TO/scraper_mcp.py

--- TryGC AI Studio (via /mcp json) ---
Paste in chat:
/mcp json {"mcpServers":{"social-scraper":{"command":"python","args":["PATH_TO\\scraper_mcp.py"]}}}

--- Direct CLI test ---
python scraper_mcp.py
  (runs as stdio - then send JSON-RPC messages)

--- SSE transport (for web clients) ---
python scraper_mcp.py --sse --port 8911

Available tools:
  scrape_profile(url, mode='http', timeout=30)     - Auto-detect platform
  scrape_tiktok(url, mode='http', timeout=30)       - TikTok profiles
  scrape_snapchat(url, mode='http', timeout=30)     - Snapchat profiles
  scrape_instagram(url, mode='http', timeout=30)    - Instagram profiles
  scrape_bulk(urls, mode='http', concurrency=10, timeout=30) - Bulk scrape
  scrape_generic(url, mode='http', timeout=30, css_selector='') - Any website
  health_check()                                    - Server health
"""


if __name__ == "__main__":
    if not HAS_SCRAPLING:
        print("Error: Scrapling is required. Install with: pip install scrapling[all]", file=sys.stderr)
        sys.exit(1)
    _load_parsers()
    if not _parsers_loaded:
        print(f"Warning: Could not load parsers from ultra_scraper.py: {_load_error}", file=sys.stderr)
    main()
