# Mention Intelligence & Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build confidence-scored, recency-aware brand mention discovery across TikTok, Instagram, YouTube, Twitter/X, and Snapchat while preserving public-first operation and partial results.

**Architecture:** Add a pure `mention_intelligence` module for identity expansion, date policy, evidence scoring, aggregation, and ranking. Keep network collection in `ultra_scraper.py`, normalize every collected post through the module, and expose additive API/stream fields consumed by the existing single-page UI.

**Tech Stack:** Python 3.10–3.14, asyncio, pytest/pytest-asyncio, standard-library HTTP server, vanilla HTML/CSS/JavaScript.

## Global Constraints

- Existing request payloads and stream consumers remain backward compatible.
- Public sources work without credentials; optional APIs activate only when configured.
- Search snippets alone can never produce `verified` evidence.
- Creator ranking prioritizes evidence tier and score before follower count.
- Supported platforms are TikTok, Instagram, YouTube, Twitter/X, and Snapchat.
- Recency values are exactly `7d`, `30d`, `90d`, `1y`, and `any`.
- Provider/platform failure preserves successful partial results and produces warnings.

## File Map

- Create `src/scrapling_tool/mention_intelligence.py`: pure normalization, alias, recency, confidence, and aggregation logic.
- Create `tests/scrapling_tool/test_mention_intelligence.py`: deterministic unit tests for the new module.
- Modify `ultra_scraper.py`: Snapchat post recognition, request parsing, candidate normalization, stream events, and aggregation integration.
- Modify `tests/scrapling_tool/test_ultra_discovery.py`: orchestration, partial failure, and compatibility tests.
- Modify `index.html`: recency/confidence controls, coverage/tier metrics, cancellation, filtering, rendering, and export-visible fields.
- Modify `CHANGELOG.md`: user-facing enhancement summary.

---

### Task 1: Pure Mention Intelligence Domain Module

**Files:**
- Create: `src/scrapling_tool/mention_intelligence.py`
- Create: `tests/scrapling_tool/test_mention_intelligence.py`

**Interfaces:**
- Produces: `expand_brand_aliases(seeds, extra_terms=(), limit=32) -> list[str]`
- Produces: `normalize_recency(value) -> str`
- Produces: `apply_evidence_policy(record, *, aliases, recency, now=None) -> dict`
- Produces: `aggregate_creators(records, profiles=None) -> list[dict]`

- [ ] **Step 1: Write failing tests for alias expansion and recency normalization**

```python
def test_aliases_are_ordered_and_deduplicated():
    assert expand_brand_aliases(["https://instagram.com/Roxa.Shop/"])[:3] == [
        "@roxa.shop", "#roxashop", "roxa shop"
    ]

def test_recency_rejects_unknown_values():
    assert normalize_recency("30d") == "30d"
    assert normalize_recency("wrong") == "any"
```

- [ ] **Step 2: Run `pytest tests/scrapling_tool/test_mention_intelligence.py -v` and confirm imports fail**

- [ ] **Step 3: Implement conservative seed parsing and stable alias deduplication**

```python
RECENCY_DAYS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365, "any": None}

def normalize_recency(value: object) -> str:
    candidate = str(value or "any").strip().lower()
    return candidate if candidate in RECENCY_DAYS else "any"
```

- [ ] **Step 4: Add failing confidence tests for native exact matches, corroborated snippets, weak leads, false partial words, and dates outside the window**

```python
def test_search_snippet_is_not_verified():
    result = apply_evidence_policy(
        {"author_username": "creator", "post_url": "https://x.com/creator/status/1",
         "source": "search:bing", "title": "Review of @roxashop"},
        aliases=["@roxashop"], recency="any"
    )
    assert result["confidence_tier"] == "probable"
    assert result["confidence_score"] < 80
```

- [ ] **Step 5: Implement exact-boundary matching, ISO date parsing, recency classification, and deterministic tier rules**

```python
def apply_evidence_policy(record, *, aliases, recency, now=None):
    """Return a copy with matched_aliases, match_locations, date_precision,
    confidence_score, confidence_tier, confidence_reasons, and rejected."""
```

- [ ] **Step 6: Add failing aggregation tests and implement creator counters/ranking**

```python
def test_verified_mentions_rank_before_large_unverified_creators():
    creators = aggregate_creators([verified_small, unverified_large])
    assert creators[0]["username"] == "trusted"
    assert creators[0]["verified_mention_count"] == 1
```

- [ ] **Step 7: Run unit tests and commit**

Run: `pytest tests/scrapling_tool/test_mention_intelligence.py -v`
Expected: PASS.

```bash
git add src/scrapling_tool/mention_intelligence.py tests/scrapling_tool/test_mention_intelligence.py
git commit -m "feat: add mention confidence domain model"
```

### Task 2: Cross-Platform Collection and Verification Integration

**Files:**
- Modify: `ultra_scraper.py:4396-4551`
- Modify: `ultra_scraper.py:5030-5227`
- Modify: `ultra_scraper.py:6647-6865`
- Modify: `tests/scrapling_tool/test_ultra_discovery.py`

**Interfaces:**
- Consumes: Task 1 pure functions.
- Produces: `_find_brand_mentions(..., recency="any", aliases=None, emit=None) -> dict`
- Produces: payload keys `posts`, `creators`, `stats`, `coverage`, `warnings`, and `aliases`.

- [ ] **Step 1: Add failing tests for Snapchat query/canonical handling and all-platform defaults**

```python
def test_snapchat_queries_and_urls_are_supported():
    queries = ultra._brand_posts_queries("snapchat", {"kind": "hashtag", "word": "roxashop"})
    assert any("snapchat.com" in query for query in queries)
    assert ultra._canon_post_url("snapchat", "https://www.snapchat.com/spotlight/abc")
```

- [ ] **Step 2: Extend `_POST_PLATFORMS`, `_brand_posts_queries`, `_post_author_from_hit`, and canonical recognition for Snapchat public surfaces**

- [ ] **Step 3: Add failing orchestration test where one platform raises and successful evidence remains with a warning**

```python
assert payload["stats"]["runStatus"] == "partial"
assert payload["creators"]
assert payload["warnings"][0]["platform"] == "snapchat"
```

- [ ] **Step 4: Integrate alias expansion and `apply_evidence_policy` before aggregation**

Native/structured rows provide `verification_method="native_structured"`; fetched page matches use `post_page`; API/oEmbed rows use `platform_api`; search rows use `search_metadata`. Preserve `title`, `snippet`, exact/approximate timestamps, and provider provenance.

- [ ] **Step 5: Replace inline creator-map ranking with `aggregate_creators` and build coverage per platform**

Coverage fields are `platform`, `status`, `sources_attempted`, `sources_succeeded`, `candidates`, `evidence`, `verified`, and `warnings`.

- [ ] **Step 6: Parse additive request fields and emit additive stream events**

```python
"recency": normalize_recency(req.get("recency", "any")),
"confidenceTiers": [tier for tier in req.get("confidenceTiers", CONFIDENCE_TIERS)
                    if tier in CONFIDENCE_TIERS],
"verifyEvidence": bool(req.get("verifyEvidence", True)),
"useOptionalApis": bool(req.get("useOptionalApis", True)),
```

Emit `plan`, `platform`, `evidence`, `creator`, `warning`, and the enriched `complete`; retain existing events.

- [ ] **Step 7: Run targeted tests and commit**

Run: `pytest tests/scrapling_tool/test_ultra_discovery.py tests/scrapling_tool/test_mention_intelligence.py -v`
Expected: PASS.

```bash
git add ultra_scraper.py tests/scrapling_tool/test_ultra_discovery.py
git commit -m "feat: verify and rank cross-platform mention evidence"
```

### Task 3: Mention Intelligence Workflow UI

**Files:**
- Modify: `index.html:2040-2134`
- Modify: `index.html:2291-2300`
- Modify: `index.html:3700-3905`

**Interfaces:**
- Consumes: Task 2 request/stream schema.
- Produces: filters and rendering for `confidence_tier`, `confidence_reasons`, `published_at`, and coverage.

- [ ] **Step 1: Add recency, confidence-tier, and cancellation controls**

```html
<select id="pfRecency">
  <option value="7d">Last 7 days</option><option value="30d" selected>Last 30 days</option>
  <option value="90d">Last 90 days</option><option value="1y">Last year</option>
  <option value="any">Any time</option>
</select>
```

Add checked filters for all confidence tiers and `pfCancel` using an `AbortController`.

- [ ] **Step 2: Add tier KPI cards and a platform coverage panel**

Render per-platform state without treating limited Snapchat verification as a zero-result success.

- [ ] **Step 3: Send new request fields and process new stream events**

```javascript
recency: $('pfRecency').value,
confidenceTiers: selectedConfidenceTiers(),
verifyEvidence: true,
useOptionalApis: true
```

- [ ] **Step 4: Render confidence, reasons, dates, and verification source**

Use text-safe DOM insertion or existing `escapeHtml`; do not insert unescaped external metadata. Apply tier filters client-side without deleting the full result arrays.

- [ ] **Step 5: Verify sandbox and live-page JavaScript parsing, then commit**

Run: `pytest tests/scrapling_tool/test_ultra_discovery.py -v`
Expected: PASS; manually load the dashboard and run sandbox Mention Intelligence.

```bash
git add index.html
git commit -m "feat: expose mention confidence and coverage controls"
```

### Task 4: Discovery Provenance and Confidence Alignment

**Files:**
- Modify: `ultra_scraper.py:5385-5405`
- Modify: `ultra_scraper.py:6300-6523`
- Modify: `index.html:1823-1932`
- Modify: `tests/scrapling_tool/test_ultra_discovery.py`

**Interfaces:**
- Consumes: normalized provenance conventions from Task 1.
- Produces: Discovery results with `source_count`, `freshness`, `confidence_tier`, and `confidence_reasons` for content targets.

- [ ] **Step 1: Add failing tests for source corroboration metadata and evidence-aware content ranking**
- [ ] **Step 2: Add provenance count/freshness fields in `_apply_discovery_context` without changing account relevance scoring**
- [ ] **Step 3: Apply evidence policy only to `posts`, `videos`, `stories`, and `hashtags` targets**
- [ ] **Step 4: Add coverage warnings and recency to Discovery request/rendering**
- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/scrapling_tool/test_discovery.py tests/scrapling_tool/test_ultra_discovery.py -v`
Expected: PASS.

```bash
git add ultra_scraper.py index.html tests/scrapling_tool/test_ultra_discovery.py
git commit -m "feat: add provenance-aware discovery results"
```

### Task 5: Regression, Quality, and Documentation

**Files:**
- Modify: `CHANGELOG.md`
- Modify tests only if a verified regression requires a fixture correction.

**Interfaces:**
- Consumes: completed feature.
- Produces: release-ready, documented behavior.

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/scrapling_tool/test_mention_intelligence.py tests/scrapling_tool/test_ultra_discovery.py tests/scrapling_tool/test_discovery.py -v`
Expected: PASS.

- [ ] **Step 2: Run the complete offline suite**

Run: `pytest -q`
Expected: PASS with live tests skipped by existing configuration.

- [ ] **Step 3: Run static checks on changed Python files**

Run: `ruff check src/scrapling_tool/mention_intelligence.py ultra_scraper.py tests/scrapling_tool/test_mention_intelligence.py tests/scrapling_tool/test_ultra_discovery.py`
Expected: no violations.

- [ ] **Step 4: Document the behavior in the changelog**

Record confidence tiers, cross-platform coverage, public-first optional APIs, recency presets, partial-run warnings, and enhanced exports under Unreleased.

- [ ] **Step 5: Commit final verification changes**

```bash
git add CHANGELOG.md
git commit -m "docs: describe mention intelligence enhancements"
```
