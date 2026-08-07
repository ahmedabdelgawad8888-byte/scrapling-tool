"""Tests for soft-block detection — pure fixtures, no network.

Social platforms answer bot traffic with HTTP 200 and a captcha or login
shell. Before this layer existed a blocked fetch looked like a clean success:
auto-escalate never fired, the empty row was cached forever, and the dashboard
reported "200 OK" over no data at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ultra_scraper import (  # noqa: E402
    _block_reason,
    _cache_usable,
    _is_profile_url,
    _scrape_stats,
)


class _FakeResponse:
    """Minimal stand-in for a Scrapling response."""

    def __init__(self, body: str = "", status: int = 200) -> None:
        self.body = body.encode("utf-8")
        self.status = status


# --------------------------------------------------------------------------
# Profile-root classification
# --------------------------------------------------------------------------
# Only profile roots carry follower counts, so post and hashtag URLs must be
# exempt from the evidence check or every video scrape reports a false block.
@pytest.mark.parametrize(
    ("url", "platform", "expected"),
    [
        ("https://www.tiktok.com/@user", "tiktok", True),
        ("https://www.tiktok.com/@user/video/123", "tiktok", False),
        ("https://www.tiktok.com/tag/fitness", "tiktok", False),
        ("https://www.instagram.com/user/", "instagram", True),
        ("https://www.instagram.com/p/abc/", "instagram", False),
        ("https://www.instagram.com/reel/abc/", "instagram", False),
        ("https://www.instagram.com/explore/", "instagram", False),
        ("https://www.youtube.com/@handle", "youtube", True),
        ("https://www.youtube.com/channel/UC123", "youtube", True),
        ("https://www.youtube.com/watch?v=abc", "youtube", False),
        ("https://www.youtube.com/shorts/xyz", "youtube", False),
        ("https://twitter.com/user", "twitter", True),
        ("https://twitter.com/user/status/1", "twitter", False),
        ("https://twitter.com/search", "twitter", False),
        ("https://www.snapchat.com/add/user", "snapchat", True),
        ("https://example.com/", "other", False),
    ],
)
def test_is_profile_url(url: str, platform: str, expected: bool) -> None:
    assert _is_profile_url(url, platform) is expected


# --------------------------------------------------------------------------
# Block reasons
# --------------------------------------------------------------------------
def test_populated_profile_is_not_blocked() -> None:
    data = {"platform": "tiktok", "followers": "162500000"}
    resp = _FakeResponse("<html>real page</html>")
    assert _block_reason(data, resp, "https://www.tiktok.com/@khaby.lame") == ""


def test_empty_profile_is_blocked() -> None:
    """The captcha shell TikTok serves: HTTP 200, no profile fields."""
    data = {"platform": "tiktok", "followers": "", "likes": ""}
    resp = _FakeResponse("<html><head></head><body></body></html>")
    assert _block_reason(data, resp, "https://www.tiktok.com/@khaby.lame") == "empty_profile"


def test_challenge_marker_is_blocked_even_with_data() -> None:
    data = {"platform": "instagram", "followers": "5"}
    resp = _FakeResponse("<title>Just a moment...</title>")
    assert _block_reason(data, resp, "https://www.instagram.com/nasa/") == "challenge_page"


@pytest.mark.parametrize("status", [401, 403, 407, 429, 503])
def test_denied_statuses_are_blocked(status: int) -> None:
    data = {"platform": "tiktok", "followers": "10"}
    resp = _FakeResponse("<html>ok</html>", status=status)
    assert _block_reason(data, resp, "https://www.tiktok.com/@x") == f"http_{status}"


def test_404_is_a_real_answer_not_a_block() -> None:
    """A missing profile is information, not a denial — escalating wastes time."""
    data = {"platform": "tiktok", "followers": "1"}
    resp = _FakeResponse("<html>not found</html>", status=404)
    assert _block_reason(data, resp, "https://www.tiktok.com/@x") == ""


def test_post_url_without_followers_is_not_blocked() -> None:
    """Videos have no follower count; demanding one would flag every post."""
    data = {"platform": "tiktok", "followers": ""}
    resp = _FakeResponse("<html>a video page</html>")
    assert _block_reason(data, resp, "https://www.tiktok.com/@user/video/123") == ""


def test_generic_page_without_profile_fields_is_not_blocked() -> None:
    data = {"platform": "other", "title": "Example Domain"}
    resp = _FakeResponse("<html>Example</html>")
    assert _block_reason(data, resp, "https://example.com/") == ""


# --------------------------------------------------------------------------
# Cache hygiene
# --------------------------------------------------------------------------
def test_cache_rejects_rows_written_before_block_detection() -> None:
    """Warm caches still hold empty rows from when a captcha counted as OK."""
    stale = {
        "platform": "tiktok",
        "url": "https://www.tiktok.com/@user",
        "followers": "",
        "status": 200,
    }
    assert _cache_usable(stale) is False


def test_cache_keeps_populated_rows() -> None:
    good = {
        "platform": "tiktok",
        "url": "https://www.tiktok.com/@user",
        "followers": "162500000",
    }
    assert _cache_usable(good) is True


def test_cache_rejects_explicitly_blocked_rows() -> None:
    assert _cache_usable({"platform": "tiktok", "blocked": "challenge_page"}) is False


def test_cache_keeps_generic_pages() -> None:
    assert _cache_usable({"platform": "other", "url": "https://example.com/"}) is True


# --------------------------------------------------------------------------
# Stats honesty
# --------------------------------------------------------------------------
def test_blocked_rows_never_count_as_successes() -> None:
    results = [
        {"status": 200, "blocked": "empty_profile", "error": "blocked: ..."},
        {"status": 200, "followers": "5"},
        {"status": 0, "error": "timeout"},
    ]
    stats = _scrape_stats(results)
    assert stats["total"] == 3
    assert stats["ok"] == 1
    assert stats["blocked"] == 1
    assert stats["errors"] == 1
    assert stats["withData"] == 1
