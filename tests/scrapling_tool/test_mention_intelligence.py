from datetime import datetime, timezone

from scrapling_tool.mention_intelligence import (
    aggregate_creators,
    apply_evidence_policy,
    expand_brand_aliases,
    normalize_recency,
)


def test_aliases_are_ordered_and_deduplicated() -> None:
    assert expand_brand_aliases(["https://instagram.com/Roxa.Shop/"])[:3] == [
        "@roxa.shop", "#roxashop", "roxa shop",
    ]


def test_extra_arabic_terms_are_preserved() -> None:
    aliases = expand_brand_aliases(["@roxashop"], ["#روكسا"])
    assert "@roxashop" in aliases
    assert "#روكسا" in aliases


def test_recency_rejects_unknown_values() -> None:
    assert normalize_recency("30d") == "30d"
    assert normalize_recency("wrong") == "any"


def test_search_snippet_is_probable_not_verified() -> None:
    result = apply_evidence_policy(
        {"platform": "twitter", "author_username": "creator",
         "post_url": "https://x.com/creator/status/1", "source": "search:bing",
         "search_sources": ["bing", "duckduckgo"], "title": "Review of @roxashop"},
        aliases=["@roxashop"], recency="any",
    )
    assert result["confidence_tier"] == "probable"
    assert result["confidence_score"] < 80


def test_native_exact_match_is_verified() -> None:
    result = apply_evidence_policy(
        {"platform": "tiktok", "author_username": "trusted",
         "post_url": "https://tiktok.com/@trusted/video/1", "caption": "Love @roxashop",
         "verification_method": "native_structured"},
        aliases=["@roxashop"], recency="any",
    )
    assert result["confidence_tier"] == "verified"
    assert result["confidence_score"] >= 90


def test_partial_word_is_not_an_exact_match() -> None:
    result = apply_evidence_policy(
        {"author_username": "creator", "post_url": "https://example.com/post/1",
         "title": "roxashopping is unrelated", "source": "search:bing"},
        aliases=["roxashop"], recency="any",
    )
    assert result["matched_aliases"] == []
    assert result["confidence_tier"] == "unverified"
    assert result["rejected"] is True


def test_known_out_of_window_evidence_is_rejected() -> None:
    result = apply_evidence_policy(
        {"author_username": "creator", "post_url": "https://example.com/post/1",
         "caption": "@roxashop", "published_at": "2025-01-01T00:00:00Z",
         "verification_method": "post_page"},
        aliases=["@roxashop"], recency="30d", now=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    assert result["rejected"] is True
    assert result["confidence_tier"] == "unverified"


def test_verified_creator_ranks_before_large_unverified_creator() -> None:
    verified = apply_evidence_policy(
        {"platform": "tiktok", "author_username": "trusted", "author_url": "https://tiktok.com/@trusted",
         "post_url": "https://tiktok.com/@trusted/video/1", "caption": "@roxashop",
         "verification_method": "native_structured"}, aliases=["@roxashop"], recency="any")
    weak = apply_evidence_policy(
        {"platform": "instagram", "author_username": "large", "author_url": "https://instagram.com/large",
         "post_url": "https://instagram.com/p/1", "title": "@roxashop", "source": "search:bing"},
        aliases=["@roxashop"], recency="30d")
    creators = aggregate_creators([verified, weak], profiles=[
        {"platform": "tiktok", "username": "trusted", "followers": 1000},
        {"platform": "instagram", "username": "large", "followers": 9_000_000},
    ])
    assert creators[0]["username"] == "trusted"
    assert creators[0]["verified_mention_count"] == 1
