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

import ultra_scraper as U  # noqa: E402
from scrapling_tool.discovery import SUPPORTED_PLATFORMS  # noqa: E402
from webapp import store  # noqa: E402
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


def _fetch_opts(opt: FetchOptions) -> dict:
    return {"proxy": opt.proxy, "headless": True, "network_idle": opt.network_idle}


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
    tasks = [
        U._scrape_one(
            url, mode, opt.timeout, sem,
            retries=opt.retries,
            auto_escalate=opt.auto_escalate,
            use_cache=opt.use_cache,
            options=_fetch_opts(opt),
        )
        for url in valid
    ]
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
def _flatten(rows: list[dict]) -> "Any":
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
        return Response(
            df.to_markdown(index=False).encode(),
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


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(title="Scrapling Tool", version="2.0.0", docs_url="/api/docs")
    manager = JobManager()
    store.init()
    U._db_init()

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
    async def _execute(job: Job, opt: FetchOptions, label: str, schedule_id: str = "") -> None:
        job.status = "running"
        job.run_id = store.create_run(job.kind, {"options": opt.model_dump(), **job.params},
                                      label=label, schedule_id=schedule_id)
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
        finally:
            job.status = status
            job.error = error
            job.finished_at = time.time()
            # Partial results are still worth keeping when a run is cancelled.
            with contextlib.suppress(Exception):
                store.finish_run(job.run_id, status, job.results, error)
            job.emit({"type": "finished", **job.summary()})

    @app.post("/api/jobs")
    async def start_job(req: JobRequest) -> dict:
        if req.kind not in _RUNNERS:
            raise HTTPException(400, f"Unknown job kind: {req.kind}")
        job = manager.create(req.kind, req.params)
        job.task = asyncio.create_task(_execute(job, req.options, req.label))
        return {"job_id": job.id, "kind": job.kind}

    @app.get("/api/jobs")
    async def list_jobs() -> dict:
        return {"jobs": manager.list()}

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
    async def runs(limit: int = 60) -> dict:
        return {"runs": store.list_runs(limit)}

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str) -> dict:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Unknown run")
        return run

    @app.delete("/api/runs/{run_id}")
    async def run_delete(run_id: str) -> dict:
        store.delete_run(run_id)
        return {"deleted": True}

    @app.get("/api/runs/{run_id}/export")
    async def export_run(run_id: str, format: str = "csv") -> Response:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Unknown run")
        return _export(run["results"], format, f"{run['kind']}_{run_id}")

    # -- page capture ------------------------------------------------------
    @app.post("/api/capture")
    async def capture(req: CaptureRequest) -> dict:
        """Screenshot + raw HTML + parsed fields for a single URL."""
        ok, url = U._validate_url(U._canonical_social_url(req.url))
        if not ok:
            raise HTTPException(400, f"Invalid URL: {url}")

        mode = _effective_mode(req.mode)
        sem = asyncio.Semaphore(1)
        started = time.perf_counter()

        if mode == "http":
            resp = await U.fetch_http(url, timeout=req.timeout, proxy=req.proxy)
        elif mode == "stealth":
            resp = await U.fetch_stealth(url, timeout=req.timeout * 1000, proxy=req.proxy,
                                         headless=True, network_idle=True)
        else:
            resp = await U.fetch_browser(url, timeout=req.timeout * 1000, proxy=req.proxy,
                                         headless=True, network_idle=True)

        html = U._safe(getattr(resp, "body", ""))
        parsed = U._parse_for_url(resp, url)
        blocked = U._block_reason(parsed, resp, url)

        shot = ""
        if req.screenshot and browser_available():
            try:
                tmp = Path(tempfile.gettempdir()) / f"shot_{int(time.time()*1000)}.png"
                await U.capture_screenshot(
                    url, tmp, timeout=req.timeout * 1000, full_page=req.full_page,
                    proxy=req.proxy,
                )
                shot = base64.b64encode(tmp.read_bytes()).decode()
                tmp.unlink(missing_ok=True)
            except Exception as exc:
                _log.warning("screenshot failed: %s", exc)

        return {
            "url": url,
            "mode": mode,
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
        return store.create_schedule(
            req.name, req.kind,
            {"options": req.options.model_dump(), **req.params},
            req.interval_min,
        )

    @app.post("/api/schedules/{sched_id}/toggle")
    async def schedule_toggle(sched_id: str, enabled: bool = True) -> dict:
        if store.get_schedule(sched_id) is None:
            raise HTTPException(404, "Unknown schedule")
        store.set_schedule_enabled(sched_id, enabled)
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
        return {
            "status": "ok",
            "version": app.version,
            "scrapling": U._HAS_FETCHERS,
            "playwright": U._HAS_PLAYWRIGHT,
            "browser": browser_available(),
            "modes": ["http"] + (["browser", "stealth"] if browser_available() else []),
            "platforms": list(SUPPORTED_PLATFORMS),
            "post_platforms": list(POST_PLATFORMS),
            "targets": ["accounts", "videos", "posts", "stories", "hashtags"],
            "store": store.stats(),
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
    """Entry point: ``python -m webapp.server``."""
    import os

    import uvicorn

    uvicorn.run(
        "webapp.server:create_app",
        factory=True,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "7860")),
        log_level=os.getenv("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    serve()
