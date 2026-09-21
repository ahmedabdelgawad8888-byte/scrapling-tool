"""FastAPI backend for the Scrapling Tool dashboard.

Every scraping path routes through ``ultra_scraper``, which is itself built on
Scrapling's fetchers (``AsyncFetcher`` for impersonated HTTP, ``DynamicFetcher``
for Playwright, ``StealthyFetcher`` for anti-detection). This module adds the
things a batch API can't express: per-result streaming, cancellation, run
history, page capture, and recurring schedules.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ultra_scraper and the scrapling_tool package both live outside this folder.
_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# .env has to be applied before ultra_scraper is imported: that module reads
# configuration at import time, so loading later would leave it looking at an
# environment that never had the operator's keys in it.
from scrapling_tool.envfile import load_env_file  # noqa: E402

load_env_file()

import ultra_scraper as U  # noqa: E402
from scrapling_tool import credentials  # noqa: E402
from scrapling_tool.discovery import SUPPORTED_PLATFORMS  # noqa: E402
from scrapling_tool.netpolicy import (  # noqa: E402
    DomainRateLimiter,
    ProxyEntry,
    ProxyPool,
    check_proxy,
    domain_of,
)
from scrapling_tool import providers as U_providers  # noqa: E402
from scrapling_tool.providers import chain as provider_chain  # noqa: E402
from webapp import crm, store  # noqa: E402
from webapp.jobs import Job, JobManager  # noqa: E402

_log = logging.getLogger("scrapling.web")

STATIC_DIR = Path(__file__).resolve().parent / "static"
JOB_KINDS = ("scrape", "discover", "lookalike", "posts", "mentions")
POST_PLATFORMS = ("tiktok", "instagram", "youtube", "twitter", "snapchat")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class FetchOptions(BaseModel):
    mode: str = "http"
    timeout: int = 30
    concurrency: int = 10
    # 3 rather than 1 because TikTok serves a fully-hydrated page only about one
    # request in three; each attempt is an independent draw, so four tries lifts
    # the odds from ~55% to ~80%. Retries only fire when a page comes back
    # blocked or empty, so pages that work first time cost nothing extra.
    retries: int = 3
    auto_escalate: bool = True
    use_cache: bool = True
    proxy: str = ""
    # On by default because TikTok (and any SPA that hydrates late) serves a bare
    # ~16KB shell on first paint and only injects __UNIVERSAL_DATA_FOR_REHYDRATION__
    # once its XHRs settle. Sampling the DOM before then yields a page that parses
    # to an empty profile, which reads as a block but is really a timing problem.
    # Costs a few seconds per page in browser/stealth; ignored by http mode.
    network_idle: bool = True


class JobRequest(BaseModel):
    kind: str
    options: FetchOptions = Field(default_factory=FetchOptions)
    # Per-kind payload; validated inside each runner.
    params: dict[str, Any] = Field(default_factory=dict)
    label: str = ""


class CaptureRequest(BaseModel):
    url: str
    mode: str = "browser"
    timeout: int = 40
    full_page: bool = True
    proxy: str = ""
    screenshot: bool = True


class ScheduleRequest(BaseModel):
    name: str
    kind: str
    interval_min: int = 60
    options: FetchOptions = Field(default_factory=FetchOptions)
    params: dict[str, Any] = Field(default_factory=dict)


class SavedJobRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    pinned: bool = False


class SavedJobPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    kind: str | None = None
    params: dict[str, Any] | None = None
    options: dict[str, Any] | None = None
    tags: list[str] | None = None
    pinned: bool | None = None
    archived: bool | None = None


class RunRetryRequest(BaseModel):
    # Which failure classes to retry. "failed" covers both blocked and errored.
    scope: str = "failed"  # failed (blocked+error) | blocked | errors
    mode: str = ""        # empty = use the original run's options


class NotificationsReadRequest(BaseModel):
    ids: list[str] | None = None  # None = mark all read


class AudienceStartRequest(BaseModel):
    url: str
    limit: int = 0


# -- creators, lists, providers, proxies ------------------------------------
class CreatorPatch(BaseModel):
    notes: str | None = None
    status: str | None = None


class CreatorBulkRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    action: str                       # tag | untag | list_add | list_remove | delete
    tags: list[str] = Field(default_factory=list)
    list_id: str = ""


class ListRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    color: str = "slate"


class ListPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None


class ProviderKeyRequest(BaseModel):
    api_key: str = ""
    enabled: bool = True
    order_index: int = 100


class ProxyRequest(BaseModel):
    url: str = Field(min_length=3, max_length=500)
    label: str = ""


# ---------------------------------------------------------------------------
# Browser availability
# ---------------------------------------------------------------------------
_browser_ok: bool | None = None


async def detect_browser() -> bool:
    """Probe once at startup whether Chromium is actually present.

    Must use the async API: Playwright's sync API refuses to run inside a
    running asyncio loop, so probing from a request handler would always fail
    and permanently advertise HTTP-only even where Chromium is installed.
    ``_HAS_PLAYWRIGHT`` alone only proves the pip package imports.
    """
    global _browser_ok
    if not U._HAS_PLAYWRIGHT:
        _browser_ok = False
        return False
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            _browser_ok = Path(p.chromium.executable_path).exists()
    except Exception as exc:
        _log.warning("browser probe failed: %s", exc)
        _browser_ok = False
    return bool(_browser_ok)


def browser_available() -> bool:
    """Cached result of :func:`detect_browser`; False until the probe runs."""
    return bool(_browser_ok)


def _effective_mode(mode: str) -> str:
    """Never hand the engine a browser mode the host cannot run."""
    if mode in ("browser", "stealth") and not browser_available():
        return "http"
    return mode if mode in ("http", "browser", "stealth") else "http"


def _fetch_opts(opt: FetchOptions, proxy: str = "") -> dict:
    return {
        "proxy": proxy or opt.proxy,
        "headless": True,
        "network_idle": opt.network_idle,
    }


# ---------------------------------------------------------------------------
# Outbound network policy
# ---------------------------------------------------------------------------
# Module-level rather than owned by the app factory because the job runners
# below are module functions: a per-app instance would be invisible to them,
# and the whole point of a rate limiter is that every outbound request in the
# process goes through the same one.
_LIMITER = DomainRateLimiter(
    rate_per_sec=float(os.getenv("SCRAPLING_RATE_LIMIT", "2.0") or 2.0)
)
_PROXY_POOL = ProxyPool()


def _outcome_of(result: dict) -> str:
    if result.get("blocked"):
        return "blocked"
    if result.get("error") or result.get("status") not in (200, None):
        return "error"
    return "ok"


async def _scrape_one_managed(url: str, mode: str, opt: FetchOptions,
                              sem: asyncio.Semaphore) -> dict:
    """``U._scrape_one`` with rate limiting, proxy rotation and telemetry.

    The limiter runs outside the semaphore's critical section conceptually but
    before the fetch, so twenty concurrent workers all targeting one host queue
    behind that host's bucket instead of arriving together.
    """
    await _LIMITER.acquire(url)

    # An explicit per-run proxy always wins; the pool only fills the gap.
    proxy = opt.proxy or _PROXY_POOL.next_proxy()
    started = time.monotonic()

    result = await U._scrape_one(
        url, mode, opt.timeout, sem,
        retries=opt.retries,
        auto_escalate=opt.auto_escalate,
        use_cache=opt.use_cache,
        options=_fetch_opts(opt, proxy),
    )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    outcome = _outcome_of(result)

    if proxy and not opt.proxy:
        _PROXY_POOL.record(proxy, outcome == "ok", str(result.get("error") or ""))
        result.setdefault("proxy_used", proxy)

    # Telemetry is best-effort: a stats write must never fail a scrape.
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            crm.record_fetch, domain_of(url) or "unknown", mode, outcome, elapsed_ms
        )

    return result


# ---------------------------------------------------------------------------
# Job runners — one per kind
# ---------------------------------------------------------------------------
async def _run_scrape(job: Job, opt: FetchOptions) -> list[dict]:
    raw = job.params.get("urls") or []
    valid: list[str] = []
    for candidate in raw:
        canonical = U._canonical_social_url(str(candidate))
        ok, result = U._validate_url(canonical)
        if ok:
            valid.append(result)
        else:
            job.emit({"type": "log", "level": "warn", "message": f"Skipped {candidate}: {result}"})
    if not valid:
        raise ValueError("No valid URLs supplied.")

    job.total = len(valid)
    job.emit({"type": "started", "total": job.total})

    mode = _effective_mode(opt.mode)
    sem = asyncio.Semaphore(max(1, opt.concurrency))
    tasks = [_scrape_one_managed(url, mode, opt, sem) for url in valid]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        job.results.append(result)
        job.done += 1
        job.emit({
            "type": "progress", "done": job.done, "total": job.total, "result": result,
        })
    return job.results


async def _run_discover(job: Job, opt: FetchOptions) -> list[dict]:
    keywords = U.split_keywords(job.params.get("keywords", ""), limit=12)
    platforms = job.params.get("platforms") or ["tiktok", "instagram"]
    if not keywords:
        raise ValueError("Enter at least one keyword.")

    target = job.params.get("target", "accounts")
    job.emit({"type": "log", "message": f"Searching {len(platforms)} platform(s) for {', '.join(keywords)}…"})

    found = await U._discover(
        keywords, platforms,
        location=job.params.get("location", ""),
        per_platform=int(job.params.get("per_platform", 15)),
        timeout=opt.timeout,
        target=target,
        search_providers=None,
    )
    candidates = [(plat, hit) for plat, lst in found.items() for hit in lst]
    job.total = len(candidates)
    job.emit({
        "type": "started", "total": job.total,
        "discovered": {k: len(v) for k, v in found.items()},
    })
    if not candidates:
        return []

    mode = _effective_mode(opt.mode)
    sem = asyncio.Semaphore(max(1, opt.concurrency))

    async def _enrich(plat: str, hit: dict) -> dict:
        data = await U._scrape_one(
            hit["url"], mode, opt.timeout, sem,
            retries=opt.retries,
            auto_escalate=opt.auto_escalate,
            use_cache=opt.use_cache,
            options=_fetch_opts(opt),
        )
        data.setdefault("platform", plat)
        data.setdefault("target", target)
        return data

    collected: list[dict] = []
    for coro in asyncio.as_completed([_enrich(p, h) for p, h in candidates]):
        data = await coro
        job.done += 1
        keep = U._search_passes(
            data,
            min_f=int(job.params.get("min_followers", 0)),
            max_f=int(job.params.get("max_followers", 0)),
            verified_only=bool(job.params.get("verified_only", False)),
            bio_kw=job.params.get("bio_keyword", ""),
            target=target,
        )
        if keep:
            collected.append(data)
        job.emit({
            "type": "progress", "done": job.done, "total": job.total,
            "result": data if keep else None, "filtered": not keep,
        })

    collected.sort(
        key=lambda d: (
            float(d.get("discovery_score", 0) or 0),
            U._to_int_count(d.get("followers")),
            1 if d.get("is_verified") else 0,
            0 if d.get("error") else 1,
        ),
        reverse=True,
    )
    limit = int(job.params.get("limit", 100))
    job.results = collected[:limit]
    return job.results


async def _run_lookalike(job: Job, opt: FetchOptions) -> list[dict]:
    seeds_raw = job.params.get("seeds") or []
    seeds: list[str] = []
    for seed in seeds_raw:
        ok, result = U._validate_url(U._canonical_social_url(str(seed)))
        if ok:
            seeds.append(result)
    if not seeds:
        raise ValueError("Enter at least one valid seed profile URL.")

    job.emit({"type": "log", "message": f"Analysing {len(seeds)} seed profile(s)…"})
    payload = await U._run_lookalike({
        "seeds": seeds,
        "platforms": job.params.get("platforms") or list(SUPPORTED_PLATFORMS),
        "signals": U.split_keywords(job.params.get("signals", ""), limit=12),
        "location": job.params.get("location", ""),
        "search_providers": None,
        "perPlatform": int(job.params.get("per_platform", 12)),
        "limit": int(job.params.get("limit", 100)),
        "minScore": float(job.params.get("min_score", 20)),
        "mode": _effective_mode(opt.mode),
        "concurrency": opt.concurrency,
        "timeout": opt.timeout,
        "retries": opt.retries,
        "auto_escalate": opt.auto_escalate,
        "use_cache": opt.use_cache,
    })
    if payload.get("error"):
        raise ValueError(str(payload["error"]))
    results = payload.get("results", [])
    signals = payload.get("signals", [])
    job.total = job.done = len(results)
    job.emit({"type": "log", "message": "Derived signals: " + (", ".join(signals) or "none")})
    job.emit({"type": "started", "total": job.total, "signals": signals})
    job.results = results
    for i, row in enumerate(results, 1):
        job.emit({"type": "progress", "done": i, "total": job.total, "result": row})
    return results


async def _run_posts(job: Job, opt: FetchOptions) -> list[dict]:
    users = [u.strip().lstrip("@") for u in str(job.params.get("usernames", "")).split(",") if u.strip()]
    terms = U._parse_terms(job.params.get("terms", ""))
    platforms = job.params.get("platforms") or ["tiktok", "instagram"]
    if not users or not terms:
        raise ValueError("Enter at least one username and one hashtag/mention.")

    job.total = len(users)
    job.emit({"type": "started", "total": job.total})
    mode = _effective_mode(opt.mode)
    sem = asyncio.Semaphore(max(1, opt.concurrency))

    async def _one(username: str) -> list[dict]:
        async with sem:
            found = await U._find_user_posts(
                username, terms, platforms,
                per_platform=int(job.params.get("per_platform", 25)),
                timeout=opt.timeout,
                deep=bool(job.params.get("deep", True)),
                engines=True,
                mode=mode,
                proxy=opt.proxy,
                search_providers=None,
            )
        rows: list[dict] = []
        for lst in found.values():
            rows.extend(lst)
        return rows

    for coro in asyncio.as_completed([_one(u) for u in users]):
        try:
            rows = await coro
        except Exception as exc:  # one bad handle shouldn't sink the batch
            job.emit({"type": "log", "level": "warn", "message": f"User lookup failed: {exc}"})
            rows = []
        job.done += 1
        job.results.extend(rows)
        for row in rows:
            job.emit({
                "type": "progress", "done": job.done, "total": job.total, "result": row,
            })
        job.emit({"type": "log", "message": f"{job.done}/{job.total} users searched"})
    return job.results


async def _run_mentions(job: Job, opt: FetchOptions) -> list[dict]:
    seeds = [s.strip() for s in str(job.params.get("seeds", "")).split(",") if s.strip()]
    terms = U._parse_terms(job.params.get("terms", ""))
    platforms = job.params.get("platforms") or list(POST_PLATFORMS)
    if not terms:
        raise ValueError("Enter at least one mention or hashtag.")

    job.emit({"type": "log", "message": "Searching for brand mentions…"})
    payload = await U._find_brand_mentions(
        terms, platforms,
        per_platform=int(job.params.get("per_platform", 25)),
        timeout=opt.timeout,
        proxy=opt.proxy,
        mode=_effective_mode(opt.mode),
        search_providers=None,
        enrich_creators=True,
        concurrency=opt.concurrency,
        recency=U.normalize_recency(job.params.get("recency", "any")),
        aliases=U.expand_brand_aliases(seeds, [U._term_label(t) for t in terms]),
    )
    creators = payload.get("creators", [])
    posts = payload.get("posts", [])
    # Creators first: they are the actionable rows, posts are the evidence.
    rows = [{**c, "_row": "creator"} for c in creators] + [{**p, "_row": "post"} for p in posts]
    job.total = job.done = len(rows)
    job.emit({
        "type": "started", "total": job.total,
        "creators": len(creators), "posts": len(posts),
    })
    job.results = rows
    for i, row in enumerate(rows, 1):
        job.emit({"type": "progress", "done": i, "total": job.total, "result": row})
    return rows


_RUNNERS = {
    "scrape": _run_scrape,
    "discover": _run_discover,
    "lookalike": _run_lookalike,
    "posts": _run_posts,
    "mentions": _run_mentions,
}


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------
def _flatten(rows: list[dict]) -> Any:
    import pandas as pd

    flat = []
    for r in rows:
        flat.append({
            "platform": r.get("platform", ""),
            "username": r.get("username", ""),
            "full_name": r.get("full_name", ""),
            "followers": r.get("followers", ""),
            "following": r.get("following", ""),
            "likes": r.get("likes", ""),
            "avg_views": r.get("avg_views", ""),
            "verified": bool(r.get("is_verified")),
            "private": bool(r.get("is_private")),
            "biography": r.get("biography", ""),
            "emails": ", ".join(r.get("emails", []) or []),
            "country": r.get("country", ""),
            "city": r.get("city", ""),
            "url": r.get("profile_url") or r.get("url", ""),
            "status": r.get("status", ""),
            "blocked": r.get("blocked", ""),
            "error": r.get("error", ""),
            "response_time": r.get("response_time", ""),
        })
    return pd.DataFrame(flat)


def _export(rows: list[dict], fmt: str, basename: str) -> Response:
    fmt = (fmt or "csv").lower()
    if fmt == "json":
        body = json.dumps(rows, ensure_ascii=False, indent=2, default=str).encode()
        return Response(body, media_type="application/json", headers=_dl(basename, "json"))
    if fmt == "jsonl":
        body = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows).encode()
        return Response(body, media_type="application/x-ndjson", headers=_dl(basename, "jsonl"))

    df = _flatten(rows)
    if fmt == "csv":
        return Response(
            df.to_csv(index=False).encode("utf-8-sig"),
            media_type="text/csv", headers=_dl(basename, "csv"),
        )
    if fmt == "md":
        flat = _flatten(rows)
        columns = list(flat.columns)
        header = "| " + " | ".join(str(c).replace("|", r"\|") for c in columns) + " |"
        sep = "|" + "|".join(" --- " for _ in columns) + "|"
        lines = [header, sep]
        for _, r in flat.iterrows():
            lines.append(
                "| "
                + " | ".join(
                    str(v).replace("|", r"\|").replace("\n", " ").strip()
                    for v in r
                )
                + " |"
            )
        return Response(
            "\n".join(lines).encode("utf-8"),
            media_type="text/markdown", headers=_dl(basename, "md"),
        )
    if fmt == "html":
        doc = (
            "<!doctype html><meta charset='utf-8'><title>Scrapling results</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:2rem}"
            "table{border-collapse:collapse;width:100%}"
            "th,td{border:1px solid #ddd;padding:6px 9px;text-align:left;font-size:14px}"
            "th{background:#f3f0fa}tr:nth-child(even){background:#fafafa}</style>"
            + df.to_html(index=False, escape=True)
        )
        return Response(doc.encode(), media_type="text/html", headers=_dl(basename, "html"))
    if fmt == "xlsx":
        buf = io.BytesIO()
        with __import__("pandas").ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Results")
        return Response(
            buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=_dl(basename, "xlsx"),
        )
    raise HTTPException(400, f"Unsupported format: {fmt}")


def _dl(basename: str, ext: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{basename}.{ext}"'}


def _export_records(rows: list[dict], fmt: str, basename: str) -> Response:
    """Export already-flat dict rows.

    Separate from :func:`_export` because that one runs results through
    ``_flatten``, which projects a scrape row onto a fixed set of profile
    columns. Creator exports carry different columns — tags, notes, quality —
    and would lose them.
    """
    import pandas as pd

    fmt = (fmt or "csv").lower()

    if fmt == "json":
        body = json.dumps(rows, ensure_ascii=False, indent=2, default=str).encode()
        return Response(body, media_type="application/json", headers=_dl(basename, "json"))
    if fmt == "jsonl":
        body = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows).encode()
        return Response(body, media_type="application/x-ndjson", headers=_dl(basename, "jsonl"))

    frame = pd.DataFrame(rows)

    if fmt == "csv":
        # utf-8-sig so Excel opens non-ASCII names correctly rather than as mojibake.
        return Response(
            frame.to_csv(index=False).encode("utf-8-sig"),
            media_type="text/csv", headers=_dl(basename, "csv"),
        )
    if fmt == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            frame.to_excel(writer, index=False, sheet_name="Creators")
        return Response(
            buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=_dl(basename, "xlsx"),
        )
    raise HTTPException(400, f"Unsupported format: {fmt}")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(title="Scrapling Tool", version="2.1.0", docs_url="/api/docs")
    manager = JobManager()
    store.init()
    crm.init()
    U._db_init()

    # -- outbound network policy -------------------------------------------
    # The limiter and pool are module-level (see above) so the job runners share
    # them; the factory only loads their configuration.
    _limiter = _LIMITER
    _proxies = _PROXY_POOL

    def _reload_proxy_pool() -> None:
        _proxies.replace([
            ProxyEntry(
                url=row["url"], label=row["label"], enabled=bool(row["enabled"]),
                status=row["status"], latency_ms=row["latency_ms"],
                ok_count=row["ok_count"], fail_count=row["fail_count"],
                last_error=row["last_error"],
            )
            for row in crm.list_proxies()
        ])

    # Seed the pool from SCRAPLING_PROXY_POOL the first time, so an operator who
    # already keeps proxies in .env does not have to re-enter them in the UI.
    for seed in (os.getenv("SCRAPLING_PROXY_POOL", "") or "").split(","):
        if seed.strip():
            with contextlib.suppress(Exception):
                crm.add_proxy(seed.strip(), label="from .env")
    _reload_proxy_pool()

    # -- provider credentials ----------------------------------------------
    # Stored keys take precedence over .env, so changing one in the UI takes
    # effect immediately and without a restart.
    with contextlib.suppress(Exception):
        credentials.load_overrides({
            name: row["api_key"]
            for name, row in crm.get_provider_keys().items()
            if row.get("enabled") and row.get("api_key")
        })

    # Every provider call now lands in provider_stats, which is what the
    # Providers page charts.
    provider_chain.set_recorder(crm.record_provider_call)

    # -- auth --------------------------------------------------------------
    # Set SCRAPLING_PASSWORD to put the dashboard behind HTTP Basic. It stays off
    # when unset so local runs keep working unchanged, but any deployment that is
    # reachable from outside must set it: every route below can start jobs that
    # scrape from this machine's IP address.
    #
    # Basic rather than a token header because the results stream is an
    # EventSource, which cannot send custom headers — browsers replay Basic
    # credentials on it automatically, so SSE keeps working with no client change.
    _auth_user = os.getenv("SCRAPLING_USER", "admin")
    _auth_password = os.getenv("SCRAPLING_PASSWORD", "")
    _OPEN_PATHS = frozenset({"/healthz"})  # host probes must stay reachable

    if _auth_password:
        @app.middleware("http")
        async def _require_auth(request: Request, call_next):
            if request.url.path in _OPEN_PATHS:
                return await call_next(request)
            supplied = ""
            scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() == "basic":
                with contextlib.suppress(Exception):
                    supplied = base64.b64decode(encoded).decode("utf-8")
            user, _, password = supplied.partition(":")
            # Both halves compared regardless of the first result: a short-circuit
            # would leak whether the username was right via response timing.
            user_ok = secrets.compare_digest(user, _auth_user)
            password_ok = secrets.compare_digest(password, _auth_password)
            if not (user_ok and password_ok):
                return JSONResponse(
                    {"detail": "Unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Scrapling Tool"'},
                )
            return await call_next(request)

        _log.info("auth: HTTP Basic enabled (user %r)", _auth_user)
    else:
        _log.warning(
            "auth: SCRAPLING_PASSWORD is unset — dashboard is OPEN to anyone who "
            "can reach it. Set it before exposing this port."
        )

    # -- job lifecycle -----------------------------------------------------
    async def _notify(level: str, message: str) -> None:
        """Best-effort in-app notification hook."""
        try:
            if store.get_settings().get("notifications"):
                store.add_notification(level, "job", message)
        except Exception:
            _log.debug("notification not stored", exc_info=True)

    async def _webhook(event: str, job: Job, extra: dict | None = None) -> None:
        """Fire-and-forget POSTs to every configured webhook."""
        targets = store.get_settings().get("webhooks") or []
        if not targets:
            return
        payload = {
            "event": event,
            "job_id": job.id,
            "occurred_at": time.time(),
            **job.summary(),
        }
        if extra:
            payload.update(extra)

        async def _send(url: str) -> None:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    await client.post(url, json=payload)
            except Exception:
                _log.warning("webhook %s -> %s failed", event, url)

        try:
            await asyncio.gather(*[_send(u) for u in targets])
        except Exception:
            _log.debug("webhook dispatch failed", exc_info=True)

    async def _execute(job: Job, opt: FetchOptions, label: str, schedule_id: str = "",
                       job_config_id: str = "") -> None:
        job.status = "running"
        job.run_id = store.create_run(job.kind, {"options": opt.model_dump(), **job.params},
                                      label=label, schedule_id=schedule_id)
        job.emit({"type": "log", "message": f"Run started ({label})"})
        asyncio.create_task(_webhook("job.started", job))
        status, error = "done", ""
        try:
            await _RUNNERS[job.kind](job, opt)
        except asyncio.CancelledError:
            status = "cancelled"
            job.emit({"type": "log", "level": "warn", "message": "Run cancelled."})
            raise
        except Exception as exc:
            status, error = "error", str(exc)[:400]
            _log.exception("job %s failed", job.id)
            job.emit({"type": "log", "level": "error", "message": error})
            await _notify("error", f"{label or job.kind} failed: {error}")
        finally:
            job.status = status
            job.error = error
            job.finished_at = time.time()
            # Partial results are still worth keeping when a run is cancelled.
            with contextlib.suppress(Exception):
                store.finish_run(job.run_id, status, job.results, error)

            # Fold whatever came back into the creator database. Done here
            # rather than per-result so one write covers the run, and guarded
            # because a CRM failure must never turn a successful scrape into a
            # failed job — the results are already safely in run history.
            if job.results:
                try:
                    ingest = await asyncio.to_thread(
                        crm.upsert_creators, job.results, job.run_id
                    )
                    job.emit({
                        "type": "log", "level": "info",
                        "message": (
                            f"Creators: {ingest['added']} new, "
                            f"{ingest['updated']} updated, {ingest['skipped']} skipped."
                        ),
                    })
                except Exception as exc:
                    _log.warning("creator ingestion failed for run %s: %s", job.run_id, exc)

            if job_config_id:
                with contextlib.suppress(Exception):
                    store.touch_job(job_config_id, job.run_id, status)
            job.emit({"type": "finished", **job.summary()})
            summary = job.summary()
            if status == "done":
                await _notify(
                    "ok",
                    f"{label or job.kind} finished — {summary['ok']} ok, "
                    f"{summary['blocked']} blocked, {summary['errors']} errors.",
                )
            elif status == "error":
                await _notify("error", f"{label or job.kind} failed: {error}")
            asyncio.create_task(_webhook(
                "job.completed" if status == "done" else "job.failed", job,
                {"error": error} if error else None,
            ))

    @app.post("/api/jobs")
    async def start_job(req: JobRequest) -> dict:
        if req.kind not in _RUNNERS:
            raise HTTPException(400, f"Unknown job kind: {req.kind}")
        job = manager.create(req.kind, req.params)
        job.task = asyncio.create_task(_execute(job, req.options, req.label))
        store.log_audit("job.run", "run", job.id,
                        detail=f"kind={req.kind} label={req.label or ''}",
                        actor=_auth_user)
        return {"job_id": job.id, "kind": job.kind}

    @app.get("/api/jobs")
    async def list_jobs() -> dict:
        return {"jobs": manager.list()}

    # -- saved job configurations -----------------------------------------
    @app.get("/api/jobs/saved")
    async def saved_jobs(include_archived: bool = False) -> dict:
        return {"jobs": store.list_jobs(include_archived)}

    @app.post("/api/jobs/saved")
    async def saved_job_create(req: SavedJobRequest) -> dict:
        if req.kind not in _RUNNERS:
            raise HTTPException(400, f"Unknown job kind: {req.kind}")
        job = store.create_job(
            req.name, req.kind, req.params, req.options,
            description=req.description, tags=req.tags,
            pinned=req.pinned, actor=_auth_user,
        )
        store.log_audit("job.create", "job", job["id"], detail=req.name,
                      actor=_auth_user)
        return job

    @app.get("/api/jobs/saved/{job_id}")
    async def saved_job_detail(job_id: str) -> dict:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "Unknown saved job")
        return job

    @app.patch("/api/jobs/saved/{job_id}")
    async def saved_job_update(job_id: str, req: SavedJobPatch) -> dict:
        fields = req.model_dump(exclude_unset=True)
        if "kind" in fields and fields["kind"] not in _RUNNERS:
            raise HTTPException(400, f"Unknown job kind: {fields['kind']}")
        job = store.update_job(job_id, fields)
        if job is None:
            raise HTTPException(404, "Unknown saved job")
        store.log_audit("job.update", "job", job_id, detail=job.get("name", ""),
                      actor=_auth_user)
        return job

    @app.delete("/api/jobs/saved/{job_id}")
    async def saved_job_delete(job_id: str) -> dict:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "Unknown saved job")
        store.delete_job(job_id)
        store.log_audit("job.delete", "job", job_id, detail=job.get("name", ""),
                      actor=_auth_user)
        return {"deleted": True}

    @app.post("/api/jobs/saved/{job_id}/run")
    async def saved_job_run(job_id: str) -> dict:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "Unknown saved job")
        saved_options = job.get("options") or {}
        opt = FetchOptions(**{
            k: v for k, v in saved_options.items()
            if k in FetchOptions.model_fields
        })
        live = manager.create(job["kind"], job["params"])
        live.task = asyncio.create_task(
            _execute(live, opt, label=job["name"], job_config_id=job_id)
        )
        return {"job_id": live.id, "config_id": job_id}

    @app.post("/api/jobs/saved/{job_id}/duplicate")
    async def saved_job_duplicate(job_id: str) -> dict:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "Unknown saved job")
        copy = store.create_job(
            f"{job['name']} (copy)", job["kind"], job["params"], job["options"],
            description=job.get("description", ""), tags=job.get("tags", []),
            actor=_auth_user,
        )
        store.log_audit("job.duplicate", "job", copy["id"],
                        detail=f"from {job_id}", actor=_auth_user)
        return copy

    @app.get("/api/jobs/{job_id}")
    async def job_detail(job_id: str) -> dict:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        return {**job.summary(), "results": job.results}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict:
        if not manager.cancel(job_id):
            raise HTTPException(409, "Job is not running")
        return {"cancelled": True}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> StreamingResponse:
        """Server-sent events: one message per result as it lands."""
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        queue = job.subscribe()

        async def _gen():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        # Comment frame: keeps proxies from closing an idle stream.
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                    if event.get("type") == "finished":
                        break
            finally:
                job.unsubscribe(queue)

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/{job_id}/export")
    async def export_job(job_id: str, format: str = "csv") -> Response:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        return _export(job.results, format, f"{job.kind}_{job.id}")

    # -- history -----------------------------------------------------------
    @app.get("/api/runs")
    async def runs(limit: int = 60, offset: int = 0, kind: str = "",
                   status: str = "", q: str = "") -> dict:
        items = store.list_runs(limit, offset)
        if kind:
            items = [r for r in items if r.get("kind") == kind]
        if status:
            items = [r for r in items if r.get("status") == status]
        if q:
            ql = q.lower()
            items = [r for r in items if ql in json.dumps(r.get("params", {}), default=str).lower()
                     or ql in r.get("id", "").lower()]
        return {"runs": items, "limit": limit, "offset": offset}

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str) -> dict:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Unknown run")
        return run

    @app.delete("/api/runs/{run_id}")
    async def run_delete(run_id: str) -> dict:
        run = store.get_run(run_id)
        if run is not None:
            store.log_audit("run.delete", "run", run_id, actor=_auth_user)
        store.delete_run(run_id)
        return {"deleted": True}

    @app.get("/api/runs/{run_id}/export")
    async def export_run(run_id: str, format: str = "csv") -> Response:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Unknown run")
        return _export(run["results"], format, f"{run['kind']}_{run_id}")

    @app.post("/api/runs/{run_id}/retry")
    async def retry_run(run_id: str, req: RunRetryRequest | None = None) -> dict:
        """Re-run only the failed bits of a completed run."""
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Unknown run")
        scope = (req.scope if req else "failed")
        if scope not in ("failed", "blocked", "errors"):
            raise HTTPException(400, "scope must be failed|blocked|errors")

        def _is_failed(r: dict) -> bool:
            if r.get("error") or r.get("blocked"):
                return scope in ("failed", "blocked" if r.get("blocked") else "errors")
            return False

        targets = [r for r in run["results"] if _is_failed(r)]
        kind = run.get("kind") or "scrape"
        if kind == "scrape":
            urls = []
            for r in targets:
                u = r.get("url") or r.get("profile_url")
                if u:
                    urls.append(u)
            if not urls:
                raise HTTPException(400, "Nothing to retry — no failed URLs.")
            params = {"urls": urls}
        else:
            # Frame-level kinds cannot retry a subset; relaunch the same query.
            params = dict(run.get("params") or {})
            if "options" in params:
                del params["options"]

        saved_options = (run.get("params") or {}).get("options") or {}
        opt = FetchOptions(**{
            k: v for k, v in saved_options.items()
            if k in FetchOptions.model_fields
        })
        if req and req.mode:
            opt.mode = _effective_mode(req.mode)
        child = manager.create(kind, params)
        label = f"retry {len(urls) if kind == 'scrape' else run.get('id')} of {run_id}"
        child.task = asyncio.create_task(_execute(child, opt, label=label))
        return {"job_id": child.id, "kind": kind, "retried": len(targets)}

    # -- command centre ---------------------------------------------------
    @app.get("/api/dashboard")
    async def dashboard() -> dict:
        return store.dashboard()

    @app.get("/api/notifications")
    async def notifications(limit: int = 30) -> dict:
        latest = store.list_notifications(limit)
        unread = store.notifications_summary()["unread"]
        return {"notifications": latest, "unread": unread}

    @app.post("/api/notifications/read")
    async def notifications_read(req: NotificationsReadRequest) -> dict:
        if req.ids is None:
            all_ids = [n["id"] for n in store.list_notifications(10_000)]
            store.mark_notifications_read(all_ids)
        else:
            store.mark_notifications_read(req.ids)
        return {"ok": True}

    @app.get("/api/audit")
    async def audit(limit: int = 100) -> dict:
        return {"entries": store.list_audit(limit)}

    @app.get("/api/settings")
    async def settings_get() -> dict:
        return {"settings": store.get_settings()}

    @app.patch("/api/settings")
    async def settings_set(body: dict[str, Any]) -> dict:
        known = store.DEFAULT_SETTINGS.keys()
        unknown = set(body) - set(known)
        if unknown:
            raise HTTPException(400, f"Unknown settings: {', '.join(sorted(unknown))}")
        store.update_settings(body)
        store.log_audit("settings.update", "setting", "",
                        detail=", ".join(sorted(body)), actor=_auth_user)
        return {"settings": store.get_settings()}

    # -- page capture ------------------------------------------------------
    @app.post("/api/capture")
    async def capture(req: CaptureRequest) -> dict:
        """Screenshot + raw HTML + parsed fields for a single URL.

        Browser modes for TikTok/Instagram are flaky because the page serves an
        unhydrated shell about two thirds of the time. We retry up to three
        times until the parsed fields look genuine, then fall back to returning
        whatever we got (with ``blocked`` set so the operator understands why).
        """
        ok, url = U._validate_url(U._canonical_social_url(req.url))
        if not ok:
            raise HTTPException(400, f"Invalid URL: {url}")

        mode = _effective_mode(req.mode)
        started = time.perf_counter()

        async def _try_fetch(m: str, retries: int = 3):
            """Retry browser/stealth fetches until the page is actually hydrated.

            For TikTok/Instagram, ``_block_reason`` returns ``empty_profile``
            when the server sent the unhydrated shell. Re-fetching is an
            independent draw, so retrying quickly is the most practical fix.
            """
            for attempt in range(retries + 1):
                if m == "http":
                    resp = await U.fetch_http(url, timeout=req.timeout, proxy=req.proxy)
                elif m == "stealth":
                    resp = await U.fetch_stealth(
                        url, timeout=req.timeout * 1000, proxy=req.proxy,
                        headless=True, network_idle=True, wait=1000,
                    )
                else:
                    resp = await U.fetch_browser(
                        url, timeout=req.timeout * 1000, proxy=req.proxy,
                        headless=True, network_idle=True, wait=1000,
                    )
                parsed = U._parse_for_url(resp, url)
                blocked = U._block_reason(parsed, resp, url)
                if not blocked or attempt == retries:
                    return resp, parsed, blocked, attempt + 1
                _log.info("capture %s attempt %s blocked (%s), retrying", m, attempt + 1, blocked)
                await asyncio.sleep(0.5)
            # unreachable, but keeps mypy happy
            return resp, parsed, blocked, retries + 1

        if mode == "http":
            resp, parsed, blocked, attempts = await _try_fetch("http", retries=0)
        elif mode == "stealth":
            resp, parsed, blocked, attempts = await _try_fetch("stealth", retries=5)
        else:
            resp, parsed, blocked, attempts = await _try_fetch("browser", retries=5)

        html = U._safe(getattr(resp, "body", ""))

        shot = ""
        if req.screenshot and browser_available() and not blocked:
            try:
                tmp = Path(tempfile.gettempdir()) / f"shot_{int(time.time()*1000)}.png"
                await U.capture_screenshot(
                    url, tmp, timeout=req.timeout * 1000, full_page=req.full_page,
                    proxy=req.proxy, network_idle=True,
                )
                shot = base64.b64encode(tmp.read_bytes()).decode()
                tmp.unlink(missing_ok=True)
            except Exception as exc:
                _log.warning("screenshot failed: %s", exc)

        return {
            "url": url,
            "mode": mode,
            "attempts": attempts,
            "status": getattr(resp, "status", 0),
            "blocked": blocked,
            "elapsed": round(time.perf_counter() - started, 2),
            "html": html[:400_000],
            "html_bytes": len(html),
            "parsed": U._canonical_profile_fields(parsed),
            "screenshot": shot,
        }

    # -- schedules ---------------------------------------------------------
    @app.get("/api/schedules")
    async def schedules() -> dict:
        return {"schedules": store.list_schedules()}

    @app.post("/api/schedules")
    async def schedule_create(req: ScheduleRequest) -> dict:
        if req.kind not in _RUNNERS:
            raise HTTPException(400, f"Unknown job kind: {req.kind}")
        created = store.create_schedule(
            req.name, req.kind,
            {"options": req.options.model_dump(), **req.params},
            req.interval_min,
        )
        store.log_audit("schedule.create", "schedule", created.get("id", ""),
                        detail=req.name, actor=_auth_user)
        return created

    @app.post("/api/schedules/{sched_id}/toggle")
    async def schedule_toggle(sched_id: str, enabled: bool = True) -> dict:
        if store.get_schedule(sched_id) is None:
            raise HTTPException(404, "Unknown schedule")
        store.set_schedule_enabled(sched_id, enabled)
        store.log_audit("schedule." + ("enable" if enabled else "pause"),
                        "schedule", sched_id, actor=_auth_user)
        return store.get_schedule(sched_id) or {}

    @app.post("/api/schedules/{sched_id}/run")
    async def schedule_run_now(sched_id: str) -> dict:
        sched = store.get_schedule(sched_id)
        if sched is None:
            raise HTTPException(404, "Unknown schedule")
        job = _spawn_from_schedule(sched)
        return {"job_id": job.id}

    @app.delete("/api/schedules/{sched_id}")
    async def schedule_delete(sched_id: str) -> dict:
        store.delete_schedule(sched_id)
        return {"deleted": True}

    # -- audience extraction -----------------------------------------------
    @app.post("/api/audience/start")
    async def audience_start(req: AudienceStartRequest) -> dict:
        target_url = U._canonical_social_url(req.url)
        ok, url = U._validate_url(target_url)
        if not ok:
            raise HTTPException(400, f"Invalid target URL: {url}")
        run_id, started = U._start_audience_job(url, max_followers=req.limit)
        return {"run_id": run_id, "started": started}

    @app.get("/api/audience/status/{run_id}")
    async def audience_status(run_id: str, limit: int = 100, offset: int = 0) -> dict:
        snapshot = U._audience_run_snapshot(run_id, limit=limit, offset=offset)
        if not snapshot.get("run") and not snapshot.get("followers"):
            raise HTTPException(404, "Run ID not found")
        return snapshot

    @app.post("/api/audience/cancel/{run_id}")
    async def audience_cancel(run_id: str) -> dict:
        cancelled = U._cancel_audience_job(run_id)
        return {"cancelled": cancelled, "run_id": run_id}

    @app.get("/api/audience/export/{run_id}")
    async def audience_export(run_id: str, format: str = "csv") -> Response:
        run_info, rows = U._audience_all_rows(run_id)
        if not run_info:
            raise HTTPException(404, "Run ID not found")
        return _export(rows, format, f"audience_{run_id}")

    def _spawn_from_schedule(sched: dict) -> Job:
        params = dict(sched.get("params") or {})
        opt = FetchOptions(**(params.pop("options", {}) or {}))
        job = manager.create(sched["kind"], params)
        job.task = asyncio.create_task(
            _execute(job, opt, label=sched["name"], schedule_id=sched["id"])
        )
        store.mark_schedule_ran(sched["id"], job.run_id or "")
        return job

    # -- scheduler loop ----------------------------------------------------
    async def _scheduler() -> None:
        """Fire due schedules once a minute."""
        while True:
            try:
                await asyncio.sleep(60)
                for sched in store.due_schedules():
                    _log.info("running schedule %s (%s)", sched["id"], sched["name"])
                    _spawn_from_schedule(sched)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("scheduler tick failed")

    @app.on_event("startup")
    async def _startup() -> None:
        ok = await detect_browser()
        _log.info("browser engine: %s", "available" if ok else "unavailable (HTTP-only)")
        app.state.scheduler = asyncio.create_task(_scheduler())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = getattr(app.state, "scheduler", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # -- meta --------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict:
        store_stats = store.stats()
        store_stats["backend"] = store.BACKEND
        scheduler = vars(app.state).get("scheduler")
        return {
            "status": "ok",
            "version": app.version,
            "scrapling": U._HAS_FETCHERS,
            "playwright": U._HAS_PLAYWRIGHT,
            "browser": browser_available(),
            "browser_engine": "chromium" if browser_available() else "http-only",
            "modes": ["http"] + (["browser", "stealth"] if browser_available() else []),
            "platforms": list(SUPPORTED_PLATFORMS),
            "post_platforms": list(POST_PLATFORMS),
            "targets": ["accounts", "videos", "posts", "stories", "hashtags"],
            "search_providers": {
                name: enabled for name, (_, enabled) in U._ENABLED_PROVIDERS.items()
            },
            "api_keys": {
                name: bool(credentials.get_key(name))
                for name in ("tavily", "serper", "serpapi", "scrapegraph", "querit")
            },
            "store": store_stats,
            "system": {
                "app": {"ok": True},
                "api": {"ok": True},
                "database": {"ok": store_stats.get("backend") != "broken"},
                "scheduler": {"ok": bool(scheduler) and not scheduler.done()},
                "workers": {"ok": True, "active_jobs": sum(
                    1 for j in manager.list() if j.get("status") in ("queued", "running")
                )},
            },
            "auth": bool(_auth_password),
        }

    @app.get("/api/providers")
    async def providers_list() -> dict:
        # Search engines that need no credential report enabled unconditionally;
        # the rest are resolved live so a key added since boot counts.
        search = {
            name: (needs is None or bool(U._provider_key(needs)))
            for name, (_, needs) in U._ENABLED_PROVIDERS.items()
        }
        return {
            "search_providers": search,
            "api_keys": {
                name: bool(credentials.get_key(name)) for name in credentials.PROVIDERS
            },
        }

    # ===================== creators =======================================
    @app.get("/api/creators")
    async def creators_search(
        q: str = "", platform: str = "", tag: str = "", list_id: str = "",
        min_followers: int = 0, max_followers: int = 0, min_quality: int = 0,
        has_contact: bool = False, verified_only: bool = False,
        sort: str = "last_seen", order: str = "desc",
        limit: int = 100, offset: int = 0,
    ) -> dict:
        return await asyncio.to_thread(
            crm.search_creators,
            query=q, platform=platform, tag=tag, list_id=list_id,
            min_followers=min_followers, max_followers=max_followers,
            min_quality=min_quality, has_contact=has_contact,
            verified_only=verified_only, sort=sort, order=order,
            limit=limit, offset=offset,
        )

    @app.get("/api/creators/facets")
    async def creators_facets() -> dict:
        """Filter options, counted — so the UI never offers an empty filter."""
        return {
            "platforms": await asyncio.to_thread(crm.platforms),
            "tags": await asyncio.to_thread(crm.all_tags),
            "lists": await asyncio.to_thread(crm.list_lists),
        }

    @app.get("/api/creators/export")
    async def creators_export(
        fmt: str = "csv", q: str = "", platform: str = "", tag: str = "",
        list_id: str = "", min_followers: int = 0, min_quality: int = 0,
        has_contact: bool = False, limit: int = 1000,
    ) -> Response:
        page = await asyncio.to_thread(
            crm.search_creators, query=q, platform=platform, tag=tag,
            list_id=list_id, min_followers=min_followers, min_quality=min_quality,
            has_contact=has_contact, limit=limit,
        )
        rows = [
            {
                "platform": c["platform"], "username": c["username"],
                "full_name": c["full_name"], "followers": c["followers"],
                "following": c["following"], "likes": c["likes"],
                "engagement_rate": c["engagement_rate"], "quality": c["quality"],
                "is_verified": c["is_verified"], "is_private": c["is_private"],
                "emails": ", ".join(c["emails"]),
                "phones": ", ".join(c["phones"]),
                "links": ", ".join(li.get("url", "") for li in c["links"]),
                "tags": ", ".join(c["tags"]),
                "country": c["country"], "city": c["city"],
                "biography": c["biography"], "profile_url": c["profile_url"],
                "seen_count": c["seen_count"], "status": c["status"],
                "notes": c["notes"],
            }
            for c in page["items"]
        ]
        if not rows:
            # 400, not 404: the SPA fallback handler rewrites every 404 to a
            # generic "Not found", which would swallow this explanation.
            raise HTTPException(400, "Nothing matches those filters.")
        return _export_records(rows, fmt, "creators")

    @app.get("/api/creators/{creator_id:path}")
    async def creator_detail(creator_id: str) -> dict:
        record = await asyncio.to_thread(crm.get_creator, creator_id)
        if record is None:
            raise HTTPException(404, "Unknown creator")
        return record

    @app.patch("/api/creators/{creator_id:path}")
    async def creator_update(creator_id: str, req: CreatorPatch) -> dict:
        changed = await asyncio.to_thread(
            crm.update_creator, creator_id, notes=req.notes, status=req.status
        )
        if not changed:
            raise HTTPException(404, "Unknown creator, or nothing to change")
        store.log_audit("creator.update", "creator", creator_id, actor=_auth_user)
        return {"ok": True}

    @app.post("/api/creators/bulk")
    async def creators_bulk(req: CreatorBulkRequest) -> dict:
        if not req.ids:
            raise HTTPException(400, "No creators selected.")

        action = req.action
        if action == "tag":
            n = await asyncio.to_thread(crm.add_tags, req.ids, req.tags)
        elif action == "untag":
            n = await asyncio.to_thread(crm.remove_tags, req.ids, req.tags)
        elif action == "list_add":
            if not req.list_id:
                raise HTTPException(400, "list_id is required to add to a list.")
            n = await asyncio.to_thread(crm.add_to_list, req.list_id, req.ids)
        elif action == "list_remove":
            if not req.list_id:
                raise HTTPException(400, "list_id is required to remove from a list.")
            n = await asyncio.to_thread(crm.remove_from_list, req.list_id, req.ids)
        elif action == "delete":
            n = await asyncio.to_thread(crm.delete_creators, req.ids)
        else:
            raise HTTPException(400, f"Unknown bulk action: {action}")

        store.log_audit(f"creator.{action}", "creator", ",".join(req.ids[:5]),
                        detail=f"{len(req.ids)} selected", actor=_auth_user)
        return {"ok": True, "affected": n}

    # ===================== lists ==========================================
    @app.get("/api/lists")
    async def lists_index() -> dict:
        return {"lists": await asyncio.to_thread(crm.list_lists)}

    @app.post("/api/lists")
    async def list_create(req: ListRequest) -> dict:
        record = await asyncio.to_thread(
            crm.create_list, req.name, req.description, req.color
        )
        store.log_audit("list.create", "list", record["id"], detail=req.name,
                        actor=_auth_user)
        return record

    @app.patch("/api/lists/{list_id}")
    async def list_update(list_id: str, req: ListPatch) -> dict:
        changed = await asyncio.to_thread(
            crm.update_list, list_id,
            name=req.name, description=req.description, color=req.color,
        )
        if not changed:
            raise HTTPException(404, "Unknown list, or nothing to change")
        return {"ok": True}

    @app.delete("/api/lists/{list_id}")
    async def list_delete(list_id: str) -> dict:
        if not await asyncio.to_thread(crm.delete_list, list_id):
            raise HTTPException(404, "Unknown list")
        store.log_audit("list.delete", "list", list_id, actor=_auth_user)
        return {"ok": True}

    # ===================== analytics ======================================
    @app.get("/api/analytics")
    async def analytics(days: int = 14) -> dict:
        """Everything the Analytics page plots, in one round trip.

        Assembled server-side because each piece is a different aggregate over
        a different table; making the browser stitch five requests together
        would only move the join somewhere slower.
        """
        summary, fetch_rows, provider_rows, runs = await asyncio.gather(
            asyncio.to_thread(crm.creator_summary),
            asyncio.to_thread(crm.fetch_stats, days),
            asyncio.to_thread(crm.provider_stats, days),
            asyncio.to_thread(store.list_runs, 200),
        )

        # Per-mode success, which is the number that answers "should I be
        # paying the cost of stealth mode on this host".
        by_mode: dict[str, dict[str, int]] = {}
        by_domain: dict[str, dict[str, Any]] = {}
        for row in fetch_rows:
            mode = by_mode.setdefault(row["mode"], {"attempts": 0, "ok": 0, "blocked": 0, "errors": 0})
            domain = by_domain.setdefault(row["domain"], {
                "domain": row["domain"], "attempts": 0, "ok": 0,
                "blocked": 0, "errors": 0, "total_ms": 0,
            })
            for key in ("attempts", "ok", "blocked", "errors"):
                mode[key] += row[key]
                domain[key] += row[key]
            domain["total_ms"] += row["total_ms"]

        for domain in by_domain.values():
            attempts = domain["attempts"] or 1
            domain["block_rate"] = round(domain["blocked"] / attempts * 100, 1)
            domain["avg_ms"] = round(domain["total_ms"] / attempts)

        providers: dict[str, dict[str, Any]] = {}
        for row in provider_rows:
            entry = providers.setdefault(row["provider"], {
                "provider": row["provider"], "calls": 0, "ok": 0,
                "errors": 0, "total_ms": 0,
            })
            for key in ("calls", "ok", "errors", "total_ms"):
                entry[key] += row[key]
        for entry in providers.values():
            calls = entry["calls"] or 1
            entry["success_rate"] = round(entry["ok"] / calls * 100, 1)
            entry["avg_ms"] = round(entry["total_ms"] / calls)

        return {
            "days": days,
            "creators": summary,
            "modes": by_mode,
            "domains": sorted(by_domain.values(), key=lambda d: -d["attempts"])[:25],
            "providers": sorted(providers.values(), key=lambda p: -p["calls"]),
            "provider_series": provider_rows,
            "runs": [
                {
                    "id": r["id"], "kind": r["kind"], "status": r["status"],
                    "total": r["total"], "ok": r["ok"], "blocked": r["blocked"],
                    "errors": r["errors"], "started_at": r["started_at"],
                    "finished_at": r.get("finished_at"),
                }
                for r in runs
            ],
        }

    # ===================== providers & keys ===============================
    @app.get("/api/providers/detail")
    async def providers_detail() -> dict:
        stored = await asyncio.to_thread(crm.get_provider_keys)
        health = provider_chain.provider_health()
        for row in health:
            record = stored.get(row["name"])
            row["stored"] = bool(record and record.get("api_key"))
            row["enabled"] = bool(record["enabled"]) if record else True
            row["order_index"] = record["order_index"] if record else row["priority"]
        return {
            "providers": health,
            "stats": await asyncio.to_thread(crm.provider_stats, 14),
        }

    @app.put("/api/providers/{provider}/key")
    async def provider_key_set(provider: str, req: ProviderKeyRequest) -> dict:
        if provider not in credentials.PROVIDERS:
            raise HTTPException(404, f"Unknown provider: {provider}")
        await asyncio.to_thread(
            crm.set_provider_key, provider, req.api_key, req.enabled, req.order_index
        )
        credentials.set_override(provider, req.api_key if req.enabled else "")
        # The key itself is never logged, only that it changed.
        store.log_audit("provider.key", "provider", provider,
                        detail="set" if req.api_key else "cleared", actor=_auth_user)
        return {"ok": True, "status": credentials.status()[provider]}

    @app.delete("/api/providers/{provider}/key")
    async def provider_key_clear(provider: str) -> dict:
        if provider not in credentials.PROVIDERS:
            raise HTTPException(404, f"Unknown provider: {provider}")
        await asyncio.to_thread(crm.delete_provider_key, provider)
        credentials.set_override(provider, "")
        store.log_audit("provider.key", "provider", provider, detail="deleted",
                        actor=_auth_user)
        return {"ok": True, "status": credentials.status()[provider]}

    @app.post("/api/providers/{provider}/test")
    async def provider_test(provider: str) -> dict:
        """Make one real call so the operator learns now, not mid-run."""
        try:
            candidate = U_providers.get_provider(provider)
        except KeyError:
            raise HTTPException(404, f"Unknown provider: {provider}") from None

        if not candidate.available():
            return {"ok": False, "error": "No credential configured.", "ms": 0}

        started = time.monotonic()
        try:
            result = await asyncio.to_thread(
                candidate.fetch, "https://example.com/", timeout=20
            )
            elapsed = int((time.monotonic() - started) * 1000)
            blocked = provider_chain.looks_blocked(result)
            await asyncio.to_thread(crm.record_provider_call, provider, not blocked, elapsed)
            return {
                "ok": not blocked, "ms": elapsed, "status": result.status,
                "bytes": len(result.body),
                "error": "Reachable, but the response looked blocked or empty." if blocked else "",
            }
        except Exception as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            await asyncio.to_thread(crm.record_provider_call, provider, False, elapsed)
            return {"ok": False, "ms": elapsed, "error": f"{type(exc).__name__}: {exc}"[:300]}

    # ===================== proxies & anti-block ===========================
    @app.get("/api/proxies")
    async def proxies_index() -> dict:
        rows = await asyncio.to_thread(crm.list_proxies)
        for row in rows:
            total = row["ok_count"] + row["fail_count"]
            row["success_rate"] = round(row["ok_count"] / total * 100, 1) if total else 0.0
        return {"proxies": rows, "active": len([r for r in rows if r["enabled"] and r["status"] != "down"])}

    @app.post("/api/proxies")
    async def proxy_add(req: ProxyRequest) -> dict:
        record = await asyncio.to_thread(crm.add_proxy, req.url, req.label)
        if record is None:
            raise HTTPException(400, "Could not add that proxy.")
        _reload_proxy_pool()
        store.log_audit("proxy.add", "proxy", record["id"], actor=_auth_user)
        return record

    @app.post("/api/proxies/{proxy_id}/toggle")
    async def proxy_toggle(proxy_id: str, enabled: bool = True) -> dict:
        if not await asyncio.to_thread(crm.set_proxy_enabled, proxy_id, enabled):
            raise HTTPException(404, "Unknown proxy")
        _reload_proxy_pool()
        return {"ok": True}

    @app.delete("/api/proxies/{proxy_id}")
    async def proxy_delete(proxy_id: str) -> dict:
        if not await asyncio.to_thread(crm.delete_proxy, proxy_id):
            raise HTTPException(404, "Unknown proxy")
        _reload_proxy_pool()
        store.log_audit("proxy.delete", "proxy", proxy_id, actor=_auth_user)
        return {"ok": True}

    @app.post("/api/proxies/check")
    async def proxies_check() -> dict:
        """Probe every proxy concurrently and persist the measurements."""
        rows = await asyncio.to_thread(crm.list_proxies)
        if not rows:
            return {"checked": 0, "results": []}

        outcomes = await asyncio.gather(
            *(check_proxy(row["url"]) for row in rows), return_exceptions=True
        )

        results = []
        for row, outcome in zip(rows, outcomes):
            if isinstance(outcome, BaseException):
                ok, latency, error = False, 0, str(outcome)[:200]
            else:
                ok, latency, error = outcome
            await asyncio.to_thread(crm.record_proxy_check, row["id"], ok, latency, error)
            results.append({"id": row["id"], "url": row["url"], "ok": ok,
                            "latency_ms": latency, "error": error})

        _reload_proxy_pool()
        return {"checked": len(results), "results": results}

    @app.get("/api/antiblock")
    async def antiblock(days: int = 7) -> dict:
        """Where blocks are actually happening, and what the limiter is doing."""
        rows = await asyncio.to_thread(crm.fetch_stats, days)

        combos = []
        for row in rows:
            attempts = row["attempts"] or 1
            combos.append({
                **row,
                "block_rate": round(row["blocked"] / attempts * 100, 1),
                "success_rate": round(row["ok"] / attempts * 100, 1),
                "avg_ms": round(row["total_ms"] / attempts),
            })

        pool = _proxies.all()
        return {
            "combos": sorted(combos, key=lambda c: (-c["block_rate"], -c["attempts"]))[:40],
            "limiter": {
                "default_rate": _limiter.rate,
                "burst": _limiter.burst,
                "domains": _limiter.snapshot(),
            },
            "proxy_pool": {
                "total": len(pool),
                "healthy": len(_proxies.healthy()),
                "entries": [
                    {"url": e.url, "label": e.label, "status": e.status,
                     "latency_ms": e.latency_ms, "success_rate": e.success_rate}
                    for e in pool
                ],
            },
            "browser_available": browser_available(),
        }

    # Kept for the old Render/Koyeb health probes.
    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    # -- static SPA --------------------------------------------------------
    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        page = STATIC_DIR / "index.html"
        if not page.is_file():
            raise HTTPException(500, "index.html is missing from webapp/static")
        return FileResponse(page)

    @app.exception_handler(404)
    async def _spa_fallback(request: Request, exc) -> Response:
        # API 404s stay JSON; anything else falls through to the single page.
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        page = STATIC_DIR / "index.html"
        if page.is_file():
            return FileResponse(page)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    return app


app = None  # populated by serve()/uvicorn factory


def serve() -> None:
    """Entry point: ``python -m webapp.server``.

    Binds loopback by default. The dashboard drives a scraper and, without
    ``SCRAPLING_PASSWORD``, has no auth — so listening on every interface is
    only ever correct when the operator asks for it explicitly.
    """
    import os

    import uvicorn

    host = os.getenv("SCRAPLING_HOST") or os.getenv("HOST") or "127.0.0.1"
    port = int(os.getenv("SCRAPLING_PORT") or os.getenv("PORT") or "8080")

    if host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("SCRAPLING_PASSWORD"):
        _log.warning(
            "serve: binding %s with SCRAPLING_PASSWORD unset — anyone who can "
            "reach this port controls the scraper.",
            host,
        )

    uvicorn.run(
        "webapp.server:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    serve()
