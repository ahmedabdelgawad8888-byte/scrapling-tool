"""Deterministic brand-mention identity, evidence, and ranking helpers."""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

CONFIDENCE_TIERS = ("verified", "probable", "unverified")
RECENCY_DAYS: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "1y": 365,
    "any": None,
}
_FIRST_PARTY_METHODS = {"native_structured", "post_page", "platform_api", "oembed"}


def _unique(values: Iterable[str], *, limit: int = 64) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = re.sub(r"\s+", " ", str(value or "")).strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
            if len(result) >= limit:
                break
    return result


def _seed_token(seed: object) -> str:
    value = str(seed or "").strip()
    if not value:
        return ""
    if "://" in value or value.lower().startswith("www."):
        parsed = urlparse(value if "://" in value else f"https://{value}")
        parts = [part for part in parsed.path.split("/") if part]
        value = parts[0] if parts else parsed.netloc.split(".")[0]
    return value.lstrip("@#").strip(" /.")


def expand_brand_aliases(
    seeds: Iterable[object], extra_terms: Iterable[object] = (), *, limit: int = 32
) -> list[str]:
    """Expand brand seeds into conservative handle, hashtag, and phrase aliases."""
    aliases: list[str] = []
    for raw in [*seeds, *extra_terms]:
        token = _seed_token(raw)
        if not token:
            continue
        compact = re.sub(r"[^\w\u0600-\u06FF]", "", token, flags=re.UNICODE)
        phrase = re.sub(r"[._-]+", " ", token).strip()
        prefix = str(raw or "").strip()[:1]
        if prefix in {"@", "#"}:
            aliases.append(prefix + token.casefold())
        else:
            aliases.append("@" + token.casefold())
        if compact:
            aliases.append("#" + compact.casefold())
        if phrase:
            aliases.append(phrase.casefold())
        if compact and compact.casefold() != phrase.casefold():
            aliases.append(compact.casefold())
    return _unique(aliases, limit=limit)


def normalize_recency(value: object) -> str:
    candidate = str(value or "any").strip().lower()
    return candidate if candidate in RECENCY_DAYS else "any"


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.isdigit():
            return _parse_datetime(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = re.escape(alias.casefold())
    if alias.startswith(("@", "#")):
        return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE)
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE)


def _matched_aliases(record: dict[str, Any], aliases: Iterable[str]) -> tuple[list[str], list[str]]:
    locations: dict[str, str] = {
        "caption": str(record.get("caption") or record.get("description") or ""),
        "title": str(record.get("title") or ""),
        "snippet": str(record.get("snippet") or ""),
    }
    structured = {
        "hashtag_entity": {"#" + str(v).lstrip("#").casefold() for v in record.get("hashtags", [])},
        "mention_entity": {"@" + str(v).lstrip("@").casefold() for v in record.get("mentions", [])},
    }
    matched: list[str] = []
    hit_locations: list[str] = []
    for alias in _unique(str(a) for a in aliases):
        normalized = alias.casefold()
        for location, entities in structured.items():
            if normalized in entities:
                matched.append(alias)
                hit_locations.append(location)
        pattern = _alias_pattern(normalized)
        for location, text in locations.items():
            if text and pattern.search(text.casefold()):
                matched.append(alias)
                hit_locations.append(location)
    return _unique(matched), _unique(hit_locations)


def apply_evidence_policy(
    record: dict[str, Any], *, aliases: Iterable[str], recency: str, now: datetime | None = None
) -> dict[str, Any]:
    """Normalize and score one evidence lead without performing network I/O."""
    result = dict(record)
    aliases = _unique(str(alias) for alias in aliases)
    method = str(result.get("verification_method") or "")
    source = str(result.get("source") or "")
    if not method:
        method = "search_metadata" if source.startswith("search:") else "unknown"
    result["verification_method"] = method
    matched, locations = _matched_aliases(result, aliases)
    if method in _FIRST_PARTY_METHODS:
        for label in result.get("matched_terms", []) or []:
            if label.casefold() in {alias.casefold() for alias in aliases}:
                matched = _unique([*matched, label])
    result["matched_aliases"] = matched
    result["matched_terms"] = _unique([*(result.get("matched_terms") or []), *matched])
    result["match_locations"] = locations

    published = _parse_datetime(
        result.get("published_at") or result.get("datePublished") or result.get("created_at")
    )
    date_precision = str(result.get("date_precision") or ("exact" if published else "unknown"))
    result["published_at"] = published.isoformat().replace("+00:00", "Z") if published else ""
    result["date_precision"] = date_precision

    recency = normalize_recency(recency)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    outside_window = bool(
        published and RECENCY_DAYS[recency] is not None
        and published < current - timedelta(days=int(RECENCY_DAYS[recency] or 0))
    )

    author = bool(result.get("author_username") or result.get("username"))
    canonical = bool(result.get("post_url") or result.get("url"))
    exact = bool(matched)
    providers = _unique([
        *(result.get("provider_names") or []),
        *(result.get("search_sources") or []),
    ])
    result["provider_names"] = providers

    score = 0
    reasons: list[str] = []
    if exact:
        score += 35
        reasons.append("Exact brand signal matched")
    if author:
        score += 15
        reasons.append("Creator identity resolved")
    if canonical:
        score += 10
        reasons.append("Canonical public content URL")
    if method in _FIRST_PARTY_METHODS:
        score += 35
        reasons.append("Confirmed by first-party content data")
    elif len(providers) >= 2:
        score += 15
        reasons.append("Corroborated by multiple discovery sources")
    elif method == "search_metadata":
        score += 10
        reasons.append("Supported by public search metadata")
    if published:
        score += 5
        reasons.append("Publication date available")

    rejected = outside_window or (bool(locations) is False and not result.get("matched_terms"))
    if outside_window:
        reasons.append(f"Outside requested {recency} window")
    bounded_unknown = recency != "any" and not published
    if bounded_unknown:
        reasons.append("Publication date could not be verified")
        score = min(score, 49)

    if exact and author and canonical and method in _FIRST_PARTY_METHODS and not outside_window:
        tier = "verified"
    elif exact and author and canonical and not outside_window and not bounded_unknown:
        tier = "probable"
    elif exact and author and canonical and recency == "any" and not outside_window:
        tier = "probable"
    else:
        tier = "unverified"
    if bounded_unknown or outside_window:
        tier = "unverified"

    result["confidence_score"] = max(0, min(int(score), 100))
    result["confidence_tier"] = tier
    result["confidence_reasons"] = _unique(reasons)
    result["rejected"] = rejected
    return result


def _count(value: object) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value or "").replace(",", "").strip().lower()
    match = re.match(r"^([\d.]+)([kmb]?)$", text)
    if not match:
        return 0
    return int(float(match.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[match.group(2)])


def aggregate_creators(
    records: Iterable[dict[str, Any]], profiles: Iterable[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Aggregate accepted evidence and rank creators by trust before reach."""
    profile_map = {
        (str(p.get("platform", "")).casefold(), str(p.get("username", "")).lstrip("@").casefold()): dict(p)
        for p in (profiles or [])
    }
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("rejected"):
            continue
        platform = str(record.get("platform") or "other").casefold()
        username = str(record.get("author_username") or record.get("username") or "").lstrip("@").casefold()
        if username:
            grouped[(platform, username)].append(record)

    creators: list[dict[str, Any]] = []
    tier_rank = {"verified": 3, "probable": 2, "unverified": 1}
    for key, evidence in grouped.items():
        platform, username = key
        creator = profile_map.get(key, {}).copy()
        counts = {tier: sum(r.get("confidence_tier") == tier for r in evidence) for tier in CONFIDENCE_TIERS}
        best = max(evidence, key=lambda r: (tier_rank.get(str(r.get("confidence_tier")), 0), int(r.get("confidence_score", 0))))
        dates = [str(r.get("published_at")) for r in evidence if r.get("published_at")]
        aliases = _unique(alias for r in evidence for alias in (r.get("matched_aliases") or []))
        creator.update({
            "platform": platform,
            "username": creator.get("username") or username,
            "url": creator.get("url") or best.get("author_url") or "",
            "profile_url": creator.get("profile_url") or best.get("author_url") or "",
            "mention_count": len(evidence),
            "verified_mention_count": counts["verified"],
            "probable_mention_count": counts["probable"],
            "unverified_mention_count": counts["unverified"],
            "unique_alias_count": len(aliases),
            "matched_terms": aliases,
            "latest_mention_at": max(dates, default=""),
            "creator_confidence_tier": best.get("confidence_tier", "unverified"),
            "creator_confidence_score": max(int(r.get("confidence_score", 0)) for r in evidence),
            "confidence_reasons": best.get("confidence_reasons", []),
            "post_urls": _unique(str(r.get("post_url") or r.get("url") or "") for r in evidence),
            "sources": _unique(str(r.get("source") or "") for r in evidence),
        })
        creators.append(creator)
    creators.sort(key=lambda c: (
        tier_rank.get(str(c.get("creator_confidence_tier")), 0),
        int(c.get("creator_confidence_score", 0)),
        int(c.get("verified_mention_count", 0)),
        str(c.get("latest_mention_at", "")),
        int(c.get("mention_count", 0)),
        _count(c.get("followers")),
    ), reverse=True)
    return creators
