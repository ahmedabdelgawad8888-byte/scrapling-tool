# Mention Intelligence & Discovery Enhancement Design

Date: 2026-07-31

## Objective

Improve Mention Intelligence and Discovery so the tool finds more relevant public brand mentions across every supported platform while clearly separating verified evidence from weaker leads. The workflow remains usable without credentials and opportunistically uses configured APIs for better coverage and verification.

## Product Decisions

- Optimize for both precision and breadth.
- Keep every usable candidate and assign one of three confidence tiers: `verified`, `probable`, or `unverified`.
- Cover TikTok, Instagram, YouTube, Twitter/X, and Snapchat.
- Use public sources by default and optional APIs when configured.
- Generate brand aliases and query variants automatically without adding a separate confirmation step.
- Support preset recency windows: 7 days, 30 days, 90 days, one year, and any time.
- Preserve partial results when a provider or platform fails.

## Architecture

Move deterministic mention logic into a focused `scrapling_tool.mention_intelligence` module. `ultra_scraper.py` remains the HTTP and streaming orchestration layer, but delegates planning, evidence normalization, confidence scoring, creator aggregation, and filtering to the new module.

The feature is divided into six bounded units:

1. **Brand identity expansion** accepts handles, profile URLs, names, and domains. It produces normalized handles, hashtag forms, plain-name forms, compact forms, domain tokens, and conservative Arabic/Latin spelling variants. Generated aliases are included in run metadata but remain hidden in the main form.
2. **Query planning** creates platform- and source-specific queries for every alias and requested recency window. Each query records its platform, alias, intent, provider, and date constraint.
3. **Candidate collection** uses platform adapters. Each adapter may collect from native public surfaces, general search providers, configured APIs, or local cached evidence. It returns a shared candidate schema and reports its capabilities.
4. **Evidence verification** attempts to confirm the author and matched brand signal from the post page, captured structured data, oEmbed/API data, or trusted native search data. Search snippets remain leads, not verified evidence.
5. **Confidence scoring** applies deterministic rules and produces a score, tier, reasons, evidence source, and verification method.
6. **Creator aggregation** groups evidence by canonical platform identity, calculates creator-level confidence and mention statistics, and ranks creators using evidence quality before audience size.

## Data Flow

1. The UI submits brand seeds, extra terms, platforms, recency, per-platform limit, timeout, concurrency, enrichment preference, and configured provider selection.
2. The server expands brand identity variants and builds a traceable query plan.
3. Platform adapters collect candidates concurrently within the existing safety limits.
4. Candidates are canonicalized and deduplicated by platform plus content identifier or canonical URL.
5. Verification adapters inspect the strongest available source and record an evidence trail.
6. Date policy is applied. Exact published timestamps satisfy a strict window; search-result dates satisfy an approximate window; missing dates are retained only for `any time` or labeled as unknown-date evidence.
7. Evidence is scored and assigned a confidence tier.
8. Creator records are enriched, aggregated, and ranked.
9. NDJSON events stream phase progress, per-platform coverage, results, warnings, and a final summary. Completed partial results remain available after cancellation or provider failure.

## Canonical Evidence Model

Each evidence record contains:

- `platform`, `post_url`, `post_id`, `author_username`, and `author_url`
- `matched_terms`, `matched_aliases`, and `match_locations` such as caption, hashtag, mention entity, title, or snippet
- `caption_excerpt` limited to a short operational preview
- `published_at` and `date_precision` (`exact`, `approximate`, or `unknown`)
- `source`, `source_url`, `verification_method`, and `provider_names`
- `confidence_score`, `confidence_tier`, and `confidence_reasons`
- `first_seen_at` and `last_verified_at`
- `status`, `warnings`, and optional failure information

Creator records add:

- `mention_count`, `verified_mention_count`, `probable_mention_count`, and `unverified_mention_count`
- `unique_alias_count`, `latest_mention_at`, and `platform_coverage`
- `creator_confidence_score`, `creator_confidence_tier`, and `ranking_reasons`
- enrichment fields already supported by the canonical profile schema

## Confidence Policy

Confidence is deterministic and explainable.

### Verified

Requires author identity plus an exact brand signal confirmed in native structured data, the fetched post content, a platform API/oEmbed response, or an equivalent first-party source. Strong signals include an exact @mention entity, exact hashtag entity, exact linked domain, or an exact normalized brand phrase in content.

### Probable

Requires a canonical public post, a resolved author, and corroborating metadata from one or more independent discovery sources, but the post body could not be fetched or fully parsed. Exact terms in a search title/snippet and agreement across providers increase the score.

### Unverified

Represents a potentially relevant lead where the canonical post or author is incomplete, the date is unknown for a bounded recency request, or only one weak source mentions the term. These records remain visible and filterable but never outrank verified or probable evidence.

Hard exclusions include the brand's own handles, non-content URLs, unrelated partial-word matches, invalid canonical URLs, and duplicates. Confidence tier is the first ranking key, followed by confidence score, verified mention count, freshness, total mention count, and audience size.

## Platform Strategy

### TikTok

Use rendered public video search and captured structured/XHR data first, followed by configured APIs and public search providers. Verify author, caption, hashtag entities, mention entities, and timestamps when exposed.

### Instagram

Use public post/reel pages, embedded metadata, configured APIs, and search providers. Because anonymous pages may be blocked, retain canonical search hits as probable or unverified until content is confirmed.

### YouTube

Use public watch/Shorts pages, oEmbed, structured metadata, optional API search, and public search providers. Channel identity and published timestamps are generally verifiable and should contribute strongly to confidence.

### Twitter/X

Use canonical status URLs, public metadata or configured API responses, and public search providers. Resolve the author from the status URL whenever possible.

### Snapchat

Add discovery adapters for canonical public profile/Spotlight/story surfaces and configured search providers. Results remain probable or unverified unless a canonical public content URL, author, and exact signal can be confirmed. The UI reports limited verification capability instead of presenting low-coverage runs as complete.

## Recency

The UI adds a `Recency` selector with `7 days`, `30 days`, `90 days`, `1 year`, and `Any time`.

- Query planners add provider-supported date constraints.
- Verification captures exact timestamps whenever available.
- A result with an exact date outside the window is rejected.
- A result with an approximate date may remain probable with a reason.
- A result with no date becomes unverified for bounded windows and is retained only when the user has not hidden that tier.

## API and Streaming Contract

The existing `/api/posts` and `/api/posts/stream` routes remain backward compatible. New request fields are optional:

- `recency`: `7d`, `30d`, `90d`, `1y`, or `any`
- `confidenceTiers`: defaults to all three tiers
- `verifyEvidence`: defaults to `true`
- `useOptionalApis`: defaults to `true`

The stream adds these events:

- `plan`: derived alias count, query count, and platform capabilities
- `platform`: discovery/verification progress and warnings for one platform
- `evidence`: a normalized evidence record as soon as it is scored
- `creator`: an updated aggregated creator record
- `warning`: recoverable provider, parsing, or capability issue
- `complete`: final creators, evidence, tier counts, platform coverage, warnings, and timing

Existing `start`, `result`, and `complete` consumers continue to work. The server handles disconnects by cancelling pending work where safe.

## User Experience

Mention Intelligence gains:

- A recency selector beside platform and result-limit controls.
- Summary metrics for verified, probable, and unverified evidence.
- Confidence badges and concise reasons in creator cards and evidence rows.
- Filters for confidence tier, platform, matched term, and recency.
- Sorting by confidence, newest mention, total mentions, verified mentions, or followers.
- A platform coverage panel showing sources attempted, sources successful, candidate counts, verified counts, and warnings.
- Cancellation support matching the Discovery and Lookalike workflows.
- Export output that includes confidence, evidence provenance, dates, aliases, and verification reasons.

The main brand form stays simple. Automatically derived aliases are exposed only in run details and exports for auditability.

Discovery gains the same platform coverage reporting and a confidence-aware result schema when the target is posts, videos, stories, or hashtags. Account discovery keeps its existing relevance score but adds source corroboration and freshness metadata. This avoids two competing scoring systems while sharing collection and provenance infrastructure.

## Error Handling

- Provider or platform failures produce warnings and do not discard successful results.
- Unsupported recency filtering is reported as approximate or unavailable per platform/source.
- Verification timeouts downgrade evidence instead of deleting it.
- Invalid brand seeds are skipped with a warning; the run fails only if no usable seed or term remains.
- Rate limiting and quota exhaustion are surfaced separately from empty-result states.
- The final run status is `complete`, `partial`, `cancelled`, or `failed`.

## Persistence and Compatibility

Existing history storage remains valid. New runs store a versioned summary containing request parameters, generated aliases, coverage, confidence-tier counts, warnings, and result counts. Full evidence remains in response/export data; persistence of large evidence payloads is deferred unless the current database schema already supports it cleanly.

Existing request payloads and exports continue to work. New fields are additive. Old clients that ignore new stream event types remain functional.

## Testing

### Pure unit tests

- Brand seed and alias normalization, including Arabic and Latin text
- Exact-boundary term matching and false-positive rejection
- Confidence scoring for first-party, API, corroborated snippet, and weak single-source evidence
- Recency classification for exact, approximate, missing, and out-of-window dates
- Canonical URL and content-ID deduplication
- Creator aggregation and ranking

### Adapter contract tests

Each platform adapter is tested with saved HTML/JSON fixtures and no network dependency. Tests assert canonical record shape, author extraction, timestamp extraction, exact signal matching, and declared capability behavior.

### Orchestration tests

- Partial platform/provider failure preserves results and emits warnings
- Optional APIs are used only when configured and allowed
- Streaming emits plan, progress, evidence, creator, warning, and completion events in a valid lifecycle
- Cancellation stops pending tasks and retains completed results
- Existing request shapes and event consumers remain compatible

### UI tests

- Recency and confidence filters affect the request and rendered results
- Confidence badges, reasons, coverage warnings, and exports use the canonical fields
- Empty, partial, failed, cancelled, and successful states are distinguishable
- Keyboard and small-screen operation remain usable

## Delivery Scope

This enhancement includes the shared intelligence module, platform adapters over current sources, deterministic confidence and recency logic, API/stream changes, Mention Intelligence UI improvements, Discovery provenance improvements, exports, and automated tests.

It does not add paid credentials, bypass authentication, scrape private content, or guarantee historical completeness. Optional providers activate only when users configure them.

## Success Criteria

- Every displayed evidence record has a confidence tier and at least one human-readable reason.
- Verified evidence is backed by a first-party/native or configured API source, never a search snippet alone.
- All five supported platforms return results or explicit capability/failure reporting.
- Bounded recency requests never present known out-of-window evidence as valid.
- One provider failure cannot fail an otherwise productive run.
- Creator ranking always prioritizes evidence quality over follower count.
- Existing Discovery, Lookalike, and Post Finder tests remain passing alongside new coverage.
