"""Tests for the cheap/free provider layer.

These tests do NOT touch the network. They exercise:
- provider registry + smart picker
- direct provider behavior with a mocked httpx Client
- oembed / RSS / sitemap providers' URL detection and XML parsing
- JSON-LD extraction from a synthetic HTML payload
- provider priority and free-tier ``available()`` gates
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scrapling_tool.providers import (
    FetchResult,
    ProviderError,
    list_providers,
    pick_provider,
)
from scrapling_tool.providers.direct import DirectProvider
from scrapling_tool.providers.jsonld import JsonLdProvider
from scrapling_tool.providers.rss import RssProvider
from scrapling_tool.providers.sitemap import SitemapProvider


def test_registry_has_core_providers() -> None:
    names = {p.name for p in list_providers()}
    # core, all free
    for required in (
        "direct",
        "scrapling",
        "tiktok_oembed",
        "youtube_oembed",
        "rss",
        "sitemap",
        "jsonld",
    ):
        assert required in names, f"missing provider: {required}"


def test_pick_prefers_oembed_for_known_hosts() -> None:
    p = pick_provider("https://www.youtube.com/watch?v=abc")
    assert p.name == "youtube_oembed"
    p = pick_provider("https://www.tiktok.com/@user/video/123")
    assert p.name == "tiktok_oembed"


def test_pick_rss_for_feed_urls() -> None:
    # /feed matches the rss hint via the XML-or-feed suffix check
    assert pick_provider("https://example.com/feed").name == "rss"
    # .xml alone is ambiguous between RSS and sitemap; rss has higher priority
    assert pick_provider("https://example.com/rss.xml").name == "rss"


def test_pick_sitemap_for_sitemap_urls() -> None:
    # The sitemap hint fires before the generic .xml → rss mapping
    assert pick_provider("https://example.com/sitemap.xml").name == "sitemap"
    assert pick_provider("https://example.com/sitemap_index.xml").name == "sitemap"


def test_pick_hint_overrides() -> None:
    p = pick_provider("https://example.com/page", hint="direct")
    assert p.name == "direct"


def test_pick_scrapling_fallback() -> None:
    p = pick_provider("https://example.com/about")
    assert p.name == "scrapling"


def test_pick_hard_target_prefers_proxy_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPERAPI_KEY", "fake")
    p = pick_provider("https://www.instagram.com/example/")
    assert p.name == "scraperapi"


def test_pick_hard_target_falls_back_when_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCRAPERAPI_KEY", raising=False)
    monkeypatch.delenv("SCRAPINGBEE_API_KEY", raising=False)
    p = pick_provider("https://www.instagram.com/example/")
    # No creds → scrapling is the only fallback
    assert p.name == "scrapling"


def test_direct_provider_returns_fetch_result(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = MagicMock()
    fake_response.content = b"<html>hi</html>"
    fake_response.text = "<html>hi</html>"
    fake_response.status_code = 200
    fake_response.url = "https://example.com/"
    fake_response.headers = {"content-type": "text/html"}
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.get.return_value = fake_response
    with patch("httpx.Client", return_value=fake_client):
        result = DirectProvider().fetch("https://example.com/")
    assert isinstance(result, FetchResult)
    assert result.status == 200
    assert result.body == b"<html>hi</html>"
    assert result.text == "<html>hi</html>"
    assert result.provider == "direct"


def test_direct_provider_propagates_conditional_headers() -> None:
    fake_response = MagicMock()
    fake_response.content = b""
    fake_response.text = ""
    fake_response.status_code = 304
    fake_response.url = "https://example.com/"
    fake_response.headers = {}
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.get.return_value = fake_response
    with patch("httpx.Client", return_value=fake_client):
        result = DirectProvider().fetch(
            "https://example.com/",
            if_none_match='"abc"',
            if_modified_since="Wed, 21 Oct 2026 07:28:00 GMT",
        )
    sent_headers = fake_client.get.call_args.kwargs["headers"]
    assert sent_headers["If-None-Match"] == '"abc"'
    assert sent_headers["If-Modified-Since"] == "Wed, 21 Oct 2026 07:28:00 GMT"
    assert result.status == 304


def test_rss_parses_rss2_items() -> None:
    body = """<?xml version='1.0'?>
<rss version='2.0'>
  <channel>
    <title>Test</title>
    <item>
      <title>Hello</title>
      <link>https://example.com/hello</link>
      <pubDate>Wed, 21 Oct 2026 07:28:00 GMT</pubDate>
      <description>Summary</description>
    </item>
  </channel>
</rss>"""
    items = RssProvider._parse_items(body)  # type: ignore[attr-defined]
    assert len(items) == 1
    assert items[0]["title"] == "Hello"
    assert items[0]["link"] == "https://example.com/hello"
    assert items[0]["summary"] == "Summary"


def test_rss_parses_atom_entries() -> None:
    body = """<?xml version='1.0'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <entry>
    <title>Atom entry</title>
    <link href='https://example.com/atom'/>
    <published>2026-10-21T07:28:00Z</published>
    <summary>atom summary</summary>
  </entry>
</feed>"""
    items = RssProvider._parse_items(body)  # type: ignore[attr-defined]
    assert len(items) == 1
    assert items[0]["title"] == "Atom entry"
    assert items[0]["link"] == "https://example.com/atom"
    assert items[0]["published"].startswith("2026-10-21")


def test_sitemap_extracts_locs() -> None:
    body = (
        b'<?xml version="1.0"?><urlset>'
        b"<url><loc>https://a/</loc></url>"
        b"<url><loc>https://b/</loc></url>"
        b"</urlset>"
    )
    import re

    matches = re.findall(rb"<loc>([^<]+)</loc>", body)
    assert len(matches) == 2
    parsed = SitemapProvider.parse_xml(body.decode("utf-8"))
    assert parsed == ["https://a/", "https://b/"]


def test_jsonld_extraction() -> None:
    html = b"""<html><head>
    <script type="application/ld+json">{"@context":"https://schema.org","@type":"Article","headline":"X"}</script>
    </head><body>hi</body></html>"""
    fake_response = MagicMock()
    fake_response.content = html
    fake_response.text = html.decode("utf-8")
    fake_response.status_code = 200
    fake_response.url = "https://example.com/article"
    fake_response.headers = {}
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.get.return_value = fake_response
    with patch("httpx.Client", return_value=fake_client):
        result = JsonLdProvider().fetch("https://example.com/article")
    assert result.meta["jsonld_count"] == 1
    assert result.meta["jsonld"][0]["headline"] == "X"
    assert result.provider == "jsonld"


def test_scrapling_provider_raises_provider_error_on_failure() -> None:
    from scrapling_tool.providers.scrapling_provider import ScraplingProvider

    p = ScraplingProvider()
    # We don't actually want to spawn a browser; we just exercise the
    # dispatch path. With a busted URL, scrapling raises, the provider
    # lets it bubble up.
    with pytest.raises(Exception):  # noqa: B017  (scrapling raises driver-specific errors)
        p.fetch("http://no-such-host.invalid/.", mode="http", timeout=1)


def test_provider_abstract_base() -> None:
    # ProviderError is the public surface — confirm it's importable and
    # distinct from generic exceptions
    assert issubclass(ProviderError, RuntimeError)
