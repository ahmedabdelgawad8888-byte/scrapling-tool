from scrapling_tool.discovery import (
    derive_seed_keywords,
    parse_count,
    similarity_breakdown,
    split_keywords,
)


def test_split_keywords_preserves_phrases_and_deduplicates() -> None:
    assert split_keywords("beauty creators\nfood reviews;Beauty Creators") == [
        "beauty creators",
        "food reviews",
    ]


def test_parse_count_handles_compact_numbers() -> None:
    assert parse_count("1.2M") == 1_200_000
    assert parse_count("18,500") == 18_500
    assert parse_count(None) == 0


def test_seed_keywords_prioritize_category_and_hashtags() -> None:
    terms = derive_seed_keywords(
        {
            "full_name": "Mona",
            "bio": "Beauty and skincare reviews in Cairo",
            "business_category": "Beauty",
            "hashtags": "#skincare #makeup",
        }
    )
    assert terms[0] == "beauty"
    assert "skincare" in terms


def test_similarity_is_explainable_and_rewards_audience_fit() -> None:
    seed = {
        "bio": "Cairo beauty skincare creator",
        "location": "Cairo",
        "followers": "100K",
    }
    close = {
        "bio": "Beauty and skincare reviews from Cairo",
        "location": "Cairo",
        "followers": "120K",
        "username": "close",
        "full_name": "Close",
        "profile_pic": "https://example.com/x.jpg",
    }
    far = {
        "bio": "Enterprise software engineering",
        "followers": "9M",
        "username": "far",
    }
    close_score = similarity_breakdown(seed, close, signals=["beauty", "skincare"])
    far_score = similarity_breakdown(seed, far, signals=["beauty", "skincare"])
    assert close_score["score"] > far_score["score"]
    assert close_score["reasons"]
    assert "skincare" in close_score["shared_topics"]
