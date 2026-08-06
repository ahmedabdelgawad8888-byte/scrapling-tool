# Changelog

All notable changes to **scrapling-tool** are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Filtering, sorting and search for Mention Intelligence creators (platform,
  minimum followers, minimum evidence volume, free-text) and for Lookalike
  Studio results (platform, score floor, follower range, verified-only).
- Evidence-post exports (JSON/CSV/Markdown) and a "Copy URLs" action for
  Mention Intelligence, using a post-shaped schema instead of the profile one.
- Per-platform coverage chips and a visible list of the brand aliases a
  mention run actually searched for.
- Lookalike Studio controls for concurrency, timeout, retries and result cap,
  plus an option to exclude the seed profiles from their own results.
- Confidence-scored Mention Intelligence with verified, probable, and
  unverified evidence tiers, exact-boundary brand matching, explainable
  reasons, and evidence-quality-first creator ranking.
- Recency controls for Mention Intelligence and Discovery covering 7, 30, and
  90 days, one year, or any time.
- Public-first mention coverage across TikTok, Instagram, YouTube, Twitter/X,
  and Snapchat, with automatic hidden brand aliases, per-platform capability
  reporting, recoverable partial results, and cancelable runs.

### Changed

- Mention exports and live results now include verification method, source
  provenance, confidence, publication-date precision, and platform warnings.
- List views and exports are scope-aware: Lookalike results carry the
  similarity score, match reasons, shared topics and seed attribution, and
  Mention results carry evidence tier, score, mention counts, matched aliases
  and recency. All of it previously existed only in memory.
- Exports emit the filtered, sorted view rather than the raw result set, so a
  CSV always matches what is on screen.
- CI now lints the whole repository and runs only this fork's test suite;
  releasing and publishing are tag-driven and gated behind an explicit
  `PUBLISH_ENABLED` repository variable.

### Fixed

- The published wheel omitted `index.html`, so `scraper-serve` and
  `scrape web` answered `{"error": "UI file not found"}` on any real install.
  An in-tree build backend now stages the dashboard into the package, and the
  UI is resolved from the checkout, an editable install, a wheel, or the
  `SCRAPER_DASHBOARD` override.
- CI could never pass: `actions/checkout` and `actions/upload-artifact` were
  pinned to 64-character hashes (Git SHAs are 40), so the very first step
  failed to resolve; the lint step covered a tree with 50 findings and the
  test step collected upstream Scrapling's suite, which errored on import.
- Code Quality ran every tool against the deleted vendored `scrapling/`
  directory and then exited 1, keeping the workflow permanently red.
- `tox.ini` referenced `--cov=scrapling`, `tests/requirements.txt` and the
  non-existent `ai`/`shell` extras, so no tox environment could start.
- The release workflow ran on every PR merged to main, failed the build when a
  PR title did not begin with `v`, and auto-published under upstream's
  `scrapling` PyPI project. The Docker workflow pushed to upstream's image
  namespaces and the image's entrypoint invoked the upstream `scrapling` CLI
  rather than this project's `scrape`.
- A leftover `ruff.toml` silently overrode `[tool.ruff]` in `pyproject.toml`,
  so the configured rules never applied. Removed, and the remaining findings
  across the project's own modules were fixed.
- The Mention Intelligence confidence-tier filter only hid evidence rows;
  creators backed solely by hidden evidence still appeared in the creator
  list, the KPI counts and every export.
- Dashboard statistics stayed at `0` / `0 KB` on load because they were only
  fetched on section change, and the dashboard is the section already active.
- `compactNum` parsed already-compacted counts with `parseInt`, rendering
  "1.2M" as `1` and "12K" as `12` on creator cards.

- Removed the stale vendored copy of Scrapling (0.4.8) that shadowed the
  pip-installed engine. The tool now runs against the installed
  `scrapling[all]` (0.4.11+), which the app already depended on.
- The published wheel no longer bundles a conflicting top-level `scrapling`
  package that collided with the `scrapling[all]` dependency; Scrapling is now
  provided solely through that dependency (minimum bumped to 0.4.11).

## [1.3.0] - 2026-07-28

### Added

- Brand Mention Intelligence for discovering creators who publicly mention, tag,
  or hashtag a seed brand across TikTok and Instagram.
- Native rendered TikTok video search with author extraction and search-engine
  fallback for older indexed evidence.
- Creator enrichment, follower and verification fields, evidence counts,
  deduplication, live progress, and bulk CSV export.

### Fixed

- Single Fetch now starts browser-first and escalates to stealth only when
  blocked, avoiding unnecessary long waits.
- Added the missing browser render-wait control that previously stopped the
  Single Fetch form before its request was sent.
- Preserved required social-page resources by default for more reliable
  JavaScript profile extraction.

## [1.2.0] - 2026-07-28

### Added

- Cross-platform Lookalike Studio for single and bulk seed profiles.
- Explainable similarity scoring with topic, market, audience, and quality reasons.
- True multi-keyword discovery with source provenance and configurable enrichment.
- Persistent premium light mode with responsive operational layouts.
- Cancelable discovery/lookalike streams, progress metrics, and ranked CSV export.

### Changed

- Unified Windows and console launchers on the complete operational dashboard.
- Ranked discovery by relevance before audience size.
- Hardened result deduplication, request validation, and stream error handling.

### Removed

- Obsolete duplicate web panels, temporary probes, and generated build/cache artifacts.

## [1.1.0] - 2026-07-19

### Added
- **Provider layer** (`scrapling_tool.providers`): pluggable, prioritized
  provider registry with smart auto-picker. Free-first routing across:
  - `direct` (httpx) — baseline
  - `tiktok_oembed` / `youtube_oembed` — official, free, no auth
  - `rss` / `sitemap` / `jsonld` — discovery providers that return rich
    structured data before we ever spend a browser byte
  - `scrapling` — wraps scrapling's Fetcher / StealthyFetcher / DynamicFetcher
  - `scraperapi` / `scrapingbee` — opt-in free-tier proxy providers
    (1000 calls / month each), used as a last-resort fallback for hard
    targets (Instagram, Twitter/X) when the credential is configured
- **Hardening layer** (`scrapling_tool.hardening`):
  - `fetch_with_retry` — exponential backoff with jitter, configurable
    `RetryConfig`, distinguishes quota exhaustion from network errors
  - `RobotsCache` — per-host robots.txt with TTL; honors `Crawl-delay`
    and `User-agent` / `Disallow` / `Allow` matching
  - `ResponseCache` — SQLite-backed cache with honest conditional
    requests (`If-None-Match` / `If-Modified-Since`, 304 round-trips
    counted as cache hits), with size-bounded pruning
  - `ProxyRotator` — round-robin user-supplied proxy pool with
    self-healing cooldowns
- **Web layer** (`scrapling_tool.web`):
  - FastAPI app exposing the provider layer over HTTP
  - Single-page card/list UI at `/` with a dark theme
  - Endpoints: `POST /api/scrape`, `POST /api/sitemap/parse`,
    `POST /api/rss/parse`, `GET /api/providers`, `GET /healthz`
  - New `scraper-serve` console script
- **Real tests** (no network): 26+ tests across providers, hardening,
  and the web layer
- **GitHub Actions CI**: matrix of Python 3.10–3.14 on Linux, macOS, Windows

### Changed
- **Python 3.14** is the primary supported version (downgrade-safe to
  3.10). The `requires-python` range is `>=3.10,<3.15`.
- `pyproject.toml` is now a proper PEP 621 package manifest with
  `authors`, `readme`, `license`, `urls`, and pinned dep ranges.
- Project layout: new `src/scrapling_tool/` package; the original
  `ultra_scraper.py` / `scraper_mcp.py` / `bulk_scraper.py` are still
  shipped as top-level modules and the existing `scrape` / `scraper-mcp`
  entry points keep working unchanged.

### Notes
- This is a *local* release. The upstream `Scrapling` package on PyPI is
  owned by Karim Shoair / D4Vinci — **do not publish under that name**.
  Publish `scrapling-tool` (this package) under your own account if/when
  you're ready.

[1.3.0]: https://github.com/.../releases/tag/v1.3.0
[1.2.0]: https://github.com/.../releases/tag/v1.2.0
[1.1.0]: https://github.com/.../releases/tag/v1.1.0
