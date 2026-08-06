"""Pure discovery and lookalike-ranking helpers.

The network orchestration lives in ``ultra_scraper.py``.  This module keeps
query normalization and ranking deterministic so they can be tested without
network access or browser dependencies.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

SUPPORTED_PLATFORMS = ("tiktok", "instagram", "snapchat", "youtube", "twitter")

_TOKEN_RE = re.compile(r"[\w\u0600-\u06FF][\w.\-\u0600-\u06FF]{1,}", re.UNICODE)
_STOP_WORDS = {
    "about",
    "account",
    "and",
    "are",
    "bio",
    "channel",
    "com",
    "content",
    "creator",
    "for",
    "from",
    "https",
    "instagram",
    "official",
    "page",
    "profile",
    "snapchat",
    "that",
    "the",
    "their",
    "this",
    "tiktok",
    "twitter",
    "with",
    "www",
    "youtube",
    "على",
    "الى",
    "إلى",
    "عن",
    "في",
    "من",
    "هذا",
    "هذه",
    "هو",
    "هي",
}


def split_keywords(raw: str | Iterable[Any] | None, *, limit: int = 12) -> list[str]:
    """Normalize phrases from a string or iterable while preserving order."""
    if raw is None:
        return []
    values = [raw] if isinstance(raw, str) else list(raw)
    phrases: list[str] = []
    seen: set[str] = set()
    for value in values:
        for phrase in re.split(r"[\r\n;,]+", str(value or "")):
            clean = re.sub(r"\s+", " ", phrase).strip(" \t,")
            key = clean.casefold()
            if len(clean) < 2 or key in seen:
                continue
            seen.add(key)
            phrases.append(clean[:120])
            if len(phrases) >= limit:
                return phrases
    return phrases


def parse_count(value: Any) -> int:
    """Parse compact audience counts such as ``1.2M`` or ``18,500``."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().replace(",", "").replace(" ", "")
    match = re.match(r"^([\d.]+)([kmb]?)$", text, re.I)
    if not match:
        digits = re.sub(r"\D", "", text)
        return int(digits) if digits else 0
    try:
        number = float(match.group(1))
    except ValueError:
        return 0
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    return max(0, int(number * multiplier[match.group(2).lower()]))


def profile_tokens(profile: dict[str, Any]) -> set[str]:
    """Return meaningful, case-folded words that describe a profile."""
    fields = (
        "username",
        "full_name",
        "display_name",
        "title",
        "bio",
        "description",
        "business_category",
        "category",
        "hashtags",
        "location",
    )
    text = " ".join(str(profile.get(field, "") or "") for field in fields)
    out: set[str] = set()
    for token in _TOKEN_RE.findall(text.casefold()):
        clean = token.strip(".-_")
        if (
            len(clean) >= 2
            and clean not in _STOP_WORDS
            and not clean.isdigit()
            and not clean.startswith("http")
        ):
            out.add(clean)
    return out


def derive_seed_keywords(
    profile: dict[str, Any],
    *,
    explicit: str | Iterable[Any] | None = None,
    limit: int = 6,
) -> list[str]:
    """Derive high-signal discovery terms from a seed profile."""
    explicit_terms = split_keywords(explicit, limit=limit)
    if explicit_terms:
        return explicit_terms

    weighted: Counter[str] = Counter()
    field_weights = {
        "business_category": 6,
        "category": 6,
        "hashtags": 5,
        "bio": 3,
        "description": 3,
        "full_name": 2,
        "display_name": 2,
        "title": 2,
        "username": 1,
        "location": 1,
    }
    for field, weight in field_weights.items():
        value = str(profile.get(field, "") or "").casefold()
        for token in _TOKEN_RE.findall(value):
            clean = token.strip(".-_")
            if len(clean) >= 3 and clean not in _STOP_WORDS and not clean.isdigit():
                weighted[clean] += weight
    return [term for term, _ in weighted.most_common(limit)]


def similarity_breakdown(
    seed: dict[str, Any],
    candidate: dict[str, Any],
    *,
    signals: str | Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Calculate an explainable 0–100 similarity score.

    Text relevance is intentionally dominant. Audience proximity is useful
    operationally but never outweighs an unrelated biography.
    """
    seed_terms = profile_tokens(seed)
    candidate_terms = profile_tokens(candidate)
    overlap = sorted(seed_terms & candidate_terms)
    union = seed_terms | candidate_terms
    lexical_ratio = len(overlap) / len(union) if union else 0.0

    signal_terms = set()
    for phrase in split_keywords(signals, limit=12):
        signal_terms.update(profile_tokens({"bio": phrase}))
    signal_hits = sorted(signal_terms & candidate_terms)

    score = min(42.0, lexical_ratio * 110.0)
    reasons: list[str] = []
    if overlap:
        reasons.append("Shared topics: " + ", ".join(overlap[:4]))

    if signal_terms:
        signal_ratio = len(signal_hits) / len(signal_terms)
        score += min(25.0, signal_ratio * 25.0)
        if signal_hits:
            reasons.append("Matches signals: " + ", ".join(signal_hits[:4]))

    seed_count = parse_count(seed.get("followers") or seed.get("subscribers"))
    candidate_count = parse_count(candidate.get("followers") or candidate.get("subscribers"))
    audience_score = 0.0
    if seed_count and candidate_count:
        log_distance = abs(math.log10(seed_count + 1) - math.log10(candidate_count + 1))
        audience_score = max(0.0, 20.0 * (1.0 - min(log_distance / 2.0, 1.0)))
        score += audience_score
        if audience_score >= 12:
            reasons.append("Comparable audience size")

    seed_location = str(seed.get("location", "") or "").casefold().strip()
    candidate_blob = " ".join(
        str(candidate.get(key, "") or "")
        for key in ("location", "bio", "description", "title")
    ).casefold()
    if seed_location and seed_location in candidate_blob:
        score += 8
        reasons.append("Same market/location signal")

    completeness_fields = ("username", "full_name", "bio", "profile_pic")
    completeness = sum(bool(candidate.get(field)) for field in completeness_fields)
    score += completeness * 1.5
    if candidate.get("is_verified"):
        score += 3
        reasons.append("Verified profile")

    if not reasons:
        reasons.append("Discovered from seed profile signals")

    return {
        "score": round(min(100.0, score), 1),
        "reasons": reasons[:5],
        "shared_topics": overlap[:10],
        "signal_hits": signal_hits[:10],
        "audience_proximity": round(audience_score, 1),
    }
