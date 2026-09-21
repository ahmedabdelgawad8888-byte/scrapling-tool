"""Derive contact and quality signals from a scraped profile row.

A scrape gives you a bio blob and some counters. What an operator actually
wants is "can I reach this person, and are they worth reaching" — which means
pulling the email out of a bio that spells it ``name (at) gmail dot com``,
recognising that a linktr.ee is a booking page, and turning raw follower counts
into a comparable engagement number.

Everything here is a pure function over a result dict so it can be unit tested
without a network, and so it can be re-run over historical rows when the
heuristics improve.
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

__all__ = [
    "extract_emails",
    "extract_phones",
    "extract_links",
    "extract_handles",
    "engagement_rate",
    "quality_score",
    "enrich_row",
]

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}",
)

# Creators deliberately break the plain form so scrapers miss it. These are the
# three shapes that actually show up: separators spelled as words, wrapped in
# brackets, or padded with spaces.
_OBFUSCATED_AT = r"(?:\s*[\(\[\{]?\s*(?:@|at|\[at\]|\(at\))\s*[\)\]\}]?\s*)"
_OBFUSCATED_DOT = r"(?:\s*[\(\[\{]?\s*(?:\.|dot|\[dot\]|\(dot\))\s*[\)\]\}]?\s*)"
_OBFUSCATED_RE = re.compile(
    rf"([A-Za-z0-9._%+\-]+){_OBFUSCATED_AT}([A-Za-z0-9\-]+){_OBFUSCATED_DOT}([A-Za-z]{{2,63}})",
    re.IGNORECASE,
)

# Bios routinely contain image filenames and CDN junk that match the email
# shape once "@" is involved. Drop anything ending in a file extension.
_NOT_A_TLD = {
    "png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "mp3", "pdf",
    "html", "htm", "php", "js", "css", "json", "xml",
}


def extract_emails(*texts: str | None) -> list[str]:
    """Every address in the supplied text, plain or lightly obfuscated.

    Order is preserved and duplicates removed so the first address in a bio —
    which is nearly always the business one — stays first.
    """
    found: list[str] = []
    seen: set[str] = set()

    for text in texts:
        if not text:
            continue
        blob = str(text)

        candidates = list(_EMAIL_RE.findall(blob))
        candidates += [
            f"{user}@{domain}.{tld}"
            for user, domain, tld in _OBFUSCATED_RE.findall(blob)
        ]

        for raw in candidates:
            addr = raw.strip(" .,;:!?\"'<>()[]").lower()
            if "@" not in addr:
                continue
            tld = addr.rsplit(".", 1)[-1]
            if tld in _NOT_A_TLD or len(tld) < 2:
                continue
            if addr in seen:
                continue
            seen.add(addr)
            found.append(addr)

    return found


# ---------------------------------------------------------------------------
# Phone
# ---------------------------------------------------------------------------
# Deliberately conservative: requires either a leading + or a separator-bearing
# grouping. A bare run of digits in a bio is far more often a follower count, a
# date or a discount code than a phone number.
_PHONE_RE = re.compile(
    r"(?:\+\d{1,3}[\s.\-]?)?"
    r"(?:\(\d{2,4}\)[\s.\-]?)?"
    r"\d{2,4}(?:[\s.\-]\d{2,4}){1,4}"
)


def extract_phones(*texts: str | None) -> list[str]:
    """Phone-shaped strings, normalised to digits plus an optional leading +."""
    found: list[str] = []
    seen: set[str] = set()

    for text in texts:
        if not text:
            continue
        for raw in _PHONE_RE.findall(str(text)):
            digits = re.sub(r"[^\d+]", "", raw)
            core = digits.lstrip("+")
            # Shorter than 7 is not dialable; longer than 15 breaks E.164.
            if not 7 <= len(core) <= 15:
                continue
            if digits in seen:
                continue
            seen.add(digits)
            found.append(digits)

    return found


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
# Three shapes, in priority order. Bios almost never include the scheme, so
# matching only `https?://` would miss the majority of link-in-bio entries — but
# accepting any bare dotted token would swallow "coach.mom.of.3". The middle
# alternative therefore requires a path, and the last one requires a TLD from a
# known list of link hubs.
_BARE_TLDS = r"com|net|org|io|co|me|bio|ee|ai|app|link|to|cc|shop|store|tv|gg"
_URL_RE = re.compile(
    r"https?://[^\s<>\"')\]]+"
    r"|(?<![@\w.])(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+/[^\s<>\"')\]]*"
    rf"|(?<![@\w.])(?:www\.)?[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:{_BARE_TLDS})(?![\w.])",
    re.I,
)

# A link-in-bio hub is a strong buying signal: it means the creator is set up to
# be contacted commercially. Worth surfacing separately from a random link.
_AGGREGATORS = {
    "linktr.ee", "beacons.ai", "linkin.bio", "lnk.bio", "campsite.bio",
    "solo.to", "carrd.co", "bio.link", "milkshake.app", "koji.to",
    "taplink.cc", "flowcode.com", "shorby.com", "allmylinks.com",
}

_CONTACT_HINTS = ("mailto:", "wa.me", "api.whatsapp.com", "t.me", "calendly.com")


def extract_links(*texts: str | None) -> list[dict[str, str]]:
    """URLs found in text, each labelled by what it is.

    Returns dicts of ``{url, host, kind}`` where kind is one of ``aggregator``,
    ``contact`` or ``link``.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    for text in texts:
        if not text:
            continue
        for raw in _URL_RE.findall(str(text)):
            url = raw.rstrip(".,;:!?)\"'")
            if not url:
                continue
            normalised = url if "://" in url else f"https://{url}"
            try:
                host = (urlparse(normalised).hostname or "").lower()
            except ValueError:
                continue
            if not host or "." not in host:
                continue
            if normalised in seen:
                continue
            seen.add(normalised)

            bare = host[4:] if host.startswith("www.") else host
            if bare in _AGGREGATORS:
                kind = "aggregator"
            elif any(hint in normalised.lower() for hint in _CONTACT_HINTS):
                kind = "contact"
            else:
                kind = "link"

            found.append({"url": normalised, "host": bare, "kind": kind})

    return found


_HANDLE_RE = re.compile(r"(?<![\w./])@([A-Za-z0-9._]{2,30})(?![\w.])")


def extract_handles(*texts: str | None) -> list[str]:
    """``@mentions`` in a bio — usually a management or second-account handle."""
    found: list[str] = []
    seen: set[str] = set()

    for text in texts:
        if not text:
            continue
        for raw in _HANDLE_RE.findall(str(text)):
            handle = raw.strip(".").lower()
            # An email's local part matches this pattern too; those are handled
            # by extract_emails and would be noise here.
            if not handle or handle in seen:
                continue
            seen.add(handle)
            found.append(handle)

    return found


# ---------------------------------------------------------------------------
# Numeric signals
# ---------------------------------------------------------------------------
_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def to_int(value: Any) -> int:
    """Parse a follower-style count. Handles ``1.2M``, ``12,345`` and ints."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if not value:
        return 0

    text = str(value).strip().lower().replace(",", "").replace(" ", "")
    if not text:
        return 0

    multiplier = 1
    if text[-1] in _SUFFIX:
        multiplier = _SUFFIX[text[-1]]
        text = text[:-1]

    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def engagement_rate(row: dict[str, Any]) -> float:
    """Engagement as a percentage of the audience, or 0.0 when unknowable.

    Prefers average views over total likes: total likes on TikTok is a lifetime
    counter, so dividing it by followers rewards old accounts rather than
    engaged ones. Average views is per-post and comparable across accounts.
    """
    followers = to_int(row.get("followers"))
    if followers <= 0:
        return 0.0

    views = to_int(row.get("avg_views"))
    if views > 0:
        return round(min(views / followers * 100, 999.0), 2)

    likes = to_int(row.get("avg_likes"))
    if likes > 0:
        return round(min(likes / followers * 100, 999.0), 2)

    return 0.0


def quality_score(row: dict[str, Any]) -> int:
    """0-100 heuristic for "is this row worth an operator's attention".

    Not a popularity ranking. It scores *reachability and completeness*, which
    is what decides whether a row can be actioned: a 5k-follower account with a
    business email beats a 500k account with a locked profile and no contact.
    """
    score = 0

    # Reachable at all — the single biggest factor.
    if row.get("emails"):
        score += 30
    if row.get("phones"):
        score += 10
    links = row.get("links") or []
    if any(link.get("kind") in ("aggregator", "contact") for link in links):
        score += 10
    elif links:
        score += 5

    # Audience, on a log-ish curve so a mega account doesn't swamp everything.
    followers = to_int(row.get("followers"))
    for threshold, points in ((1_000_000, 20), (100_000, 16), (10_000, 12), (1_000, 8), (1, 4)):
        if followers >= threshold:
            score += points
            break

    # Engagement, banded because exact rates are noisy at small sample sizes.
    rate = row.get("engagement_rate") or engagement_rate(row)
    for threshold, points in ((10.0, 15), (5.0, 12), (2.0, 8), (0.5, 4)):
        if rate >= threshold:
            score += points
            break

    # Profile completeness.
    if row.get("biography") or row.get("bio"):
        score += 5
    if row.get("full_name"):
        score += 3
    if row.get("is_verified"):
        score += 7

    # A private account cannot be evaluated or, usually, worked with.
    if row.get("is_private"):
        score -= 20

    return max(0, min(100, score))


# ---------------------------------------------------------------------------
# Row enrichment
# ---------------------------------------------------------------------------
def _text_fields(row: dict[str, Any]) -> Iterable[str | None]:
    """Every field on a result row that might hide a contact detail."""
    yield row.get("biography")
    yield row.get("bio")
    yield row.get("description")
    yield row.get("full_name")
    yield row.get("external_url")
    yield row.get("website")
    signature = row.get("bio_link")
    if isinstance(signature, dict):
        yield signature.get("link")
    elif signature:
        yield str(signature)


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return ``row`` with contact, engagement and quality fields filled in.

    Mutates nothing: callers get a new dict. Existing values win over derived
    ones so a scraper that already found the email keeps its answer.
    """
    out = dict(row)
    texts = list(_text_fields(row))

    existing_emails = [str(e).lower() for e in (row.get("emails") or []) if e]
    derived_emails = extract_emails(*texts)
    out["emails"] = list(dict.fromkeys(existing_emails + derived_emails))

    if not out.get("phones"):
        out["phones"] = extract_phones(*texts)

    if not out.get("links"):
        out["links"] = extract_links(*texts)

    if not out.get("handles"):
        out["handles"] = extract_handles(row.get("biography"), row.get("bio"))

    out["followers_n"] = to_int(row.get("followers"))
    out["following_n"] = to_int(row.get("following"))
    out["likes_n"] = to_int(row.get("likes"))
    out["engagement_rate"] = engagement_rate(out)
    out["quality"] = quality_score(out)
    out["has_contact"] = bool(out["emails"] or out["phones"])

    return out
