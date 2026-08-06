from __future__ import annotations

import pytest

import ultra_scraper as ultra


def test_bing_rss_fallback_returns_real_search_items() -> None:
    rss = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel><title>Bing results</title>
      <item><title>NASA mission update</title>
        <link>https://www.youtube.com/watch?v=abc123</link>
        <description>Creator reviews @nasa mission footage.</description>
        <pubDate>Thu, 30 Jul 2026 12:00:00 GMT</pubDate>
      </item>
      <item><title>Second result</title>
        <link>https://x.com/creator/status/42</link>
        <description><![CDATA[Mentioning #nasa]]></description>
      </item>
    </channel></rss>"""

    hits = ultra._parse_bing_rss(rss, max_results=10)

    assert hits == [
        {
            "url": "https://www.youtube.com/watch?v=abc123",
            "title": "NASA mission update",
            "snippet": "Creator reviews @nasa mission footage.",
            "published_at": "Thu, 30 Jul 2026 12:00:00 GMT",
            "date_precision": "approximate",
            "source": "bing_rss",
        },
        {
            "url": "https://x.com/creator/status/42",
            "title": "Second result",
            "snippet": "Mentioning #nasa",
            "published_at": "",
            "date_precision": "unknown",
            "source": "bing_rss",
        },
    ]


def test_youtube_search_html_extracts_public_video_and_creator() -> None:
    payload = {
        "contents": [{
            "videoRenderer": {
                "videoId": "abc123",
                "title": {"runs": [{"text": "Independent NASA mission review"}]},
                "ownerText": {"runs": [{
                    "text": "Space Creator",
                    "navigationEndpoint": {
                        "browseEndpoint": {"canonicalBaseUrl": "/@spacecreator"}
                    },
                }]},
                "descriptionSnippet": {"runs": [{"text": "What NASA announced today"}]},
                "publishedTimeText": {"simpleText": "2 days ago"},
            }
        }]
    }
    html = f'<script>var ytInitialData = {ultra.json.dumps(payload)};</script>'

    items = ultra._youtube_items_from_search_html(html)

    assert items == [{
        "video_id": "abc123",
        "title": "Independent NASA mission review",
        "description": "What NASA announced today",
        "author": "spacecreator",
        "author_name": "Space Creator",
        "author_url": "https://www.youtube.com/@spacecreator",
        "published_text": "2 days ago",
    }]


@pytest.mark.asyncio
async def test_discover_keeps_keywords_separate_and_honors_provider_selection(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_search(query: str, *, timeout: int, max_results: int, providers):
        calls.append((query, tuple(providers or [])))
        slug = "beauty_creator" if "beauty" in query else "food_creator"
        return [{
            "url": f"https://www.instagram.com/{slug}/",
            "title": slug,
            "snippet": query,
            "source": "bing",
        }]

    monkeypatch.setattr(ultra, "_search_aggregate", fake_search)
    monkeypatch.setattr(ultra, "_local_profile_hits", lambda *args, **kwargs: {"instagram": []})
    monkeypatch.setattr(ultra, "_direct_candidates", lambda *args, **kwargs: [])

    found = await ultra._discover(
        ["beauty", "food"],
        ["instagram"],
        per_platform=10,
        search_providers=["bing"],
    )

    urls = {row["url"] for row in found["instagram"]}
    assert "https://www.instagram.com/beauty_creator/" in urls
    assert "https://www.instagram.com/food_creator/" in urls
    assert calls
    assert all(providers == ("bing",) for _, providers in calls)
    assert any("beauty" in query for query, _ in calls)
    assert any("food" in query for query, _ in calls)


@pytest.mark.asyncio
async def test_bulk_lookalike_excludes_seeds_and_returns_explained_scores(monkeypatch) -> None:
    seed = {
        "url": "https://www.instagram.com/seed/",
        "platform": "instagram",
        "username": "seed",
        "bio": "Cairo beauty skincare creator",
        "location": "Cairo",
        "followers": "100K",
        "status": 200,
    }
    candidate = {
        "url": "https://www.tiktok.com/@match",
        "title": "Beauty Match",
        "snippet": "Cairo skincare beauty reviews",
        "source": "test",
        "local_data": {
            "url": "https://www.tiktok.com/@match",
            "platform": "tiktok",
            "username": "match",
            "bio": "Cairo skincare beauty reviews",
            "location": "Cairo",
            "followers": "120K",
            "status": 200,
        },
    }

    monkeypatch.setattr(ultra, "_load_local_profiles", lambda: [seed])

    async def fake_discover(*args, **kwargs):
        return {
            "instagram": [{"url": seed["url"], "source": "direct"}],
            "tiktok": [candidate],
        }

    monkeypatch.setattr(ultra, "_discover", fake_discover)
    payload = await ultra._run_lookalike({
        "seeds": [seed["url"]],
        "platforms": ["instagram", "tiktok"],
        "signals": ["beauty", "skincare"],
        "location": "Cairo",
        "search_providers": ["bing"],
        "perPlatform": 10,
        "limit": 20,
        "minScore": 10,
        "mode": "search_only",
        "concurrency": 2,
        "timeout": 20,
        "retries": 0,
        "auto_escalate": False,
        "use_cache": True,
    })

    assert payload["stats"]["seedCount"] == 1
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["username"] == "match"
    assert result["similarity_score"] > 10
    assert result["match_reasons"]
    assert result["matched_seed"]["username"] == "seed"


@pytest.mark.asyncio
async def test_brand_mentions_dedupes_posts_and_enriches_public_creators(monkeypatch) -> None:
    async def fake_search(query: str, *, timeout: int, max_results: int, providers):
        if "tiktok" in query.lower():
            return [{
                "url": "https://www.tiktok.com/@creator_one/video/1234567890123456789",
                "title": "Creator One mentions @roxashop",
                "snippet": "#roxashop review",
                "source": "bing",
                "sources": ["bing"],
            }]
        return [{
            "url": "https://www.instagram.com/reel/ABC123/",
            "title": "Creator Two (@creator.two) • Instagram",
            "snippet": "Testing #roxashop",
            "source": "duckduckgo",
            "sources": ["duckduckgo"],
        }]

    async def fake_scrape(url, mode, timeout, sem, **kwargs):
        username = ultra._username_from_url(url)
        return {
            "url": url,
            "username": username,
            "platform": ultra._platform_of(url),
            "full_name": username.replace(".", " ").title(),
            "followers": "10K" if "creator_one" in url else "20K",
            "is_verified": "creator.two" in url,
            "status": 200,
        }

    monkeypatch.setattr(ultra, "_search_aggregate", fake_search)
    monkeypatch.setattr(ultra, "_scrape_one", fake_scrape)

    async def no_native_posts(*args, **kwargs):
        return []

    monkeypatch.setattr(ultra, "_tiktok_public_search_posts", no_native_posts)

    payload = await ultra._find_brand_mentions(
        ultra._parse_terms(["@roxashop", "#roxashop"]),
        ["tiktok", "instagram"],
        per_platform=20,
        search_providers=["bing"],
        excluded_handles={"roxashop"},
    )

    assert payload["stats"]["posts"] == 2
    assert payload["stats"]["creators"] == 2
    assert payload["stats"]["verifiedCreators"] == 1
    assert payload["stats"]["byPlatform"] == {"tiktok": 1, "instagram": 1}
    assert payload["stats"]["probableEvidence"] == 2
    assert payload["stats"]["runStatus"] == "complete"
    assert {creator["username"] for creator in payload["creators"]} == {
        "creator_one",
        "creator.two",
    }
    assert all(creator["mention_count"] == 1 for creator in payload["creators"])
    assert all(creator["post_urls"] for creator in payload["creators"])


def test_brand_post_queries_cover_public_post_surfaces() -> None:
    mention = {"kind": "mention", "word": "roxashop"}
    assert any("inurl:/video/" in query for query in ultra._brand_posts_queries("tiktok", mention))
    instagram_queries = ultra._brand_posts_queries("instagram", mention)
    assert any("instagram.com/reel" in query for query in instagram_queries)
    assert any("instagram.com/p" in query for query in instagram_queries)


def test_snapchat_queries_and_public_content_urls_are_supported() -> None:
    queries = ultra._brand_posts_queries(
        "snapchat", {"kind": "hashtag", "word": "roxashop"}
    )
    assert any("snapchat.com/spotlight" in query for query in queries)
    assert ultra._canon_post_url(
        "snapchat", "https://www.snapchat.com/spotlight/abc123"
    ) == "https://www.snapchat.com/spotlight/abc123"


def test_discovery_content_context_includes_confidence_and_corroboration() -> None:
    result = ultra._apply_discovery_context(
        {
            "url": "https://twitter.com/creator/status/123",
            "platform": "twitter",
            "username": "creator",
            "status": 200,
            "search_only": True,
        },
        {
            "url": "https://twitter.com/creator/status/123",
            "title": "Creator reviews @roxashop",
            "snippet": "A public review",
            "source": "bing",
            "sources": ["bing", "duckduckgo"],
        },
        ["roxashop"],
        target="posts",
        recency="any",
    )
    assert result["source_count"] == 2
    assert result["source_corroborated"] is True
    assert result["confidence_tier"] == "probable"


@pytest.mark.asyncio
async def test_brand_mentions_preserves_partial_results_and_reports_platform_failure(monkeypatch) -> None:
    async def fake_search(query: str, **kwargs):
        if "snapchat" in query.lower():
            raise RuntimeError("provider unavailable")
        return [{
            "url": "https://www.instagram.com/reel/ABC123/",
            "title": "Creator (@creator) · Instagram",
            "snippet": "Reviewing #roxashop",
            "source": "bing",
            "sources": ["bing"],
        }]

    monkeypatch.setattr(ultra, "_search_aggregate", fake_search)
    payload = await ultra._find_brand_mentions(
        ultra._parse_terms(["#roxashop"]),
        ["instagram", "snapchat"],
        enrich_creators=False,
    )
    assert payload["creators"]
    assert payload["stats"]["runStatus"] == "partial"
    assert payload["warnings"][0]["platform"] == "snapchat"


def test_canonical_profile_schema_extracts_public_contact_and_social_fields() -> None:
    result = ultra._canonical_profile_fields({
        "url": "https://www.tiktok.com/@creator",
        "platform": "tiktok",
        "full_name": "Creator Name",
        "bio": (
            "Cairo beauty creator. hello@example.com +20 100 123 4567 "
            "https://instagram.com/creator https://creator.example/shop"
        ),
        "followers": 120_000,
        "following": 42,
        "likes": 2_400_000,
        "business_category": "Beauty",
        "is_business": True,
        "is_private": False,
        "recent_posts": [{"views": 1000}, {"view_count": 3000}],
        "location": "Cairo, Egypt",
    })

    assert result["profile_url"] == "https://www.tiktok.com/@creator"
    assert result["username"] == "creator"
    assert result["profile_category"] == "Beauty"
    assert result["biography"].startswith("Cairo beauty creator")
    assert result["avg_views"] == 2000
    assert result["emails"] == ["hello@example.com"]
    assert result["phone_numbers"] == ["+20 100 123 4567"]
    assert result["country"] == "Egypt"
    assert result["city"] == "Cairo"
    assert result["tiktok_links"] == ["https://www.tiktok.com/@creator"]
    assert result["instagram_links"] == ["https://instagram.com/creator"]
    assert result["other_links"] == ["https://creator.example/shop"]
    assert result["is_business"] is True
    assert result["is_private"] is False


@pytest.mark.asyncio
async def test_native_tiktok_brand_search_extracts_authors_from_captured_xhr(monkeypatch) -> None:
    class Response:
        status = 200
        body = ""

        def __init__(self, body: str = "") -> None:
            self.body = body
            self.captured_xhr = []

    payload = {
        "item_list": [{
            "id": "1234567890123456789",
            "desc": "Testing #roxashop and mentioning @roxashop",
            "author": {"uniqueId": "creator_one"},
        }]
    }
    page = Response()
    page.captured_xhr = [Response(ultra.json.dumps(payload))]

    async def fake_browser(url, **kwargs):
        assert "q=roxashop" in url
        assert kwargs["capture_xhr"] == r"api/search"
        return page

    monkeypatch.setattr(ultra, "fetch_browser", fake_browser)
    monkeypatch.setattr(ultra, "_HAS_PLAYWRIGHT", True)

    rows = await ultra._tiktok_public_search_posts(
        ultra._parse_terms(["@roxashop", "#roxashop"]),
        mode="browser",
    )

    assert len(rows) == 1
    assert rows[0]["author_username"] == "creator_one"
    assert rows[0]["author_url"] == "https://www.tiktok.com/@creator_one"
    assert rows[0]["matched_terms"] == ["@roxashop", "#roxashop"]
    assert rows[0]["source"] == "tiktok_public_search"
