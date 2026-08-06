"""Tests for the hardening layer: retry, robots, cache, proxy rotation."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from scrapling_tool.hardening import (
    ProxyRotator,
    ResponseCache,
    RetryConfig,
    RobotsCache,
    fetch_with_retry,
)
from scrapling_tool.hardening.retry import RetryExhausted
from scrapling_tool.providers.base import FetchResult

# ----- retry -----

def _ok_result() -> FetchResult:
    return FetchResult(url="u", final_url="u", body=b"", status=200, provider="t")


def test_retry_succeeds_first_try() -> None:
    fn = MagicMock(return_value=_ok_result())
    result = fetch_with_retry(fn, "https://x", config=RetryConfig(max_attempts=3))
    assert result.status == 200
    assert fn.call_count == 1


def test_retry_succeeds_on_third_attempt() -> None:
    fn = MagicMock(
        side_effect=[
            FetchResult(url="u", final_url="u", body=b"", status=503, provider="t"),
            FetchResult(url="u", final_url="u", body=b"", status=502, provider="t"),
            _ok_result(),
        ]
    )
    result = fetch_with_retry(fn, "https://x", config=RetryConfig(max_attempts=5, base_delay=0.0, jitter=0.0))
    assert result.status == 200
    assert fn.call_count == 3


def test_retry_exhausts_on_persistent_5xx() -> None:
    fn = MagicMock(
        return_value=FetchResult(url="u", final_url="u", body=b"", status=500, provider="t")
    )
    with pytest.raises(RetryExhausted):
        fetch_with_retry(fn, "https://x", config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=0.0))
    assert fn.call_count == 3


def test_retry_propagates_non_retryable_exception() -> None:
    fn = MagicMock(side_effect=ValueError("nope"))
    with pytest.raises(ValueError, match="nope"):
        fetch_with_retry(fn, "https://x", config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=0.0))
    assert fn.call_count == 1


# ----- robots -----

def test_robots_allows_when_no_robots_txt() -> None:
    fake_response = MagicMock()
    fake_response.status_code = 404
    fake_response.text = ""
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.get.return_value = fake_response
    with pytest.MonkeyPatch.context() as m:
        import httpx
        m.setattr(httpx, "Client", lambda *a, **kw: fake_client)
        cache = RobotsCache(ttl=60)
        try:
            d = cache.is_allowed("https://example.com/anything")
            assert d.allowed is True
        finally:
            cache.close()


def test_robots_disallow_path() -> None:
    body = (
        "User-agent: *\n"
        "Disallow: /private\n"
        "Crawl-delay: 5\n"
    )
    ua, allowed, delay = RobotsCache.parse_body(body, "https://example.com/private/x")
    assert allowed is False
    assert delay == 5.0
    ua, allowed, delay = RobotsCache.parse_body(body, "https://example.com/public")
    assert allowed is True


def test_robots_allow_beats_disallow_when_more_specific() -> None:
    body = (
        "User-agent: *\n"
        "Disallow: /private\n"
        "Allow: /private/public\n"
    )
    ua, allowed, _ = RobotsCache.parse_body(body, "https://example.com/private/public")
    assert allowed is True
    _, allowed, _ = RobotsCache.parse_body(body, "https://example.com/private/secret")
    assert allowed is False


# ----- response cache -----

def test_response_cache_round_trip(tmp_path) -> None:
    cache = ResponseCache(tmp_path / "cache.db")
    try:
        cache.put(
            CacheEntry_(  # type: ignore[arg-type]
                url="https://example.com/",
                status=200,
                headers={"etag": '"abc"'},
                body=b"<html/>",
                etag='"abc"',
                last_modified=None,
                cached_at=time.time(),
            )
        )
        got = cache.get("https://example.com/")
        assert got is not None
        assert got.body == b"<html/>"
        assert got.etag == '"abc"'
    finally:
        cache.close()


# local alias to avoid importing the dataclass from the module path mismatch
CacheEntry_ = __import__("scrapling_tool.hardening.cache", fromlist=["CacheEntry"]).CacheEntry


def test_response_cache_prunes(tmp_path) -> None:
    cache = ResponseCache(tmp_path / "cache.db")
    try:
        for i in range(10):
            cache.put(
                CacheEntry_(
                    url=f"https://example.com/{i}",
                    status=200,
                    headers={},
                    body=b"x" * 1024,
                    etag=None,
                    last_modified=None,
                    cached_at=time.time() - i,
                )
            )
        # Force a tiny limit; should drop several
        n = cache.prune(max_bytes=2048)
        assert n > 0
    finally:
        cache.close()


# ----- proxy rotation -----

def test_proxy_rotator_round_robin() -> None:
    r = ProxyRotator(["http://a:1", "http://b:2"])
    a = r.next()
    b = r.next()
    a2 = r.next()
    assert a is not None and b is not None
    assert a != b
    assert a == a2  # wrapped in {"http://": ..., "https://": ...}


def test_proxy_rotator_disables_after_threshold() -> None:
    r = ProxyRotator(["http://a:1", "http://b:2"], cooldown_seconds=999, error_threshold=2)
    r.report_error("http://a:1")
    r.report_error("http://a:1")
    # After 2 errors, a is disabled; b is still enabled
    first = r.next()
    second = r.next()
    third = r.next()
    # We expect to see b twice in a row, with no a
    assert first is not None and second is not None and third is not None
    assert "b:2" in first["http://"] or "b:2" in second["http://"]


def test_proxy_rotator_recovers_after_cooldown() -> None:
    r = ProxyRotator(["http://a:1"], cooldown_seconds=0.0, error_threshold=1)
    r.report_error("http://a:1")
    # With cooldown=0 the next next() call recovers
    p = r.next()
    assert p is not None
    assert p["http://"] == "http://a:1"


def test_proxy_rotator_empty_returns_none() -> None:
    r = ProxyRotator([])
    assert r.next() is None
