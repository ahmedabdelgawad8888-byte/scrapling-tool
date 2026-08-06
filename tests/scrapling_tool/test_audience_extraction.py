from __future__ import annotations

import json

import ultra_scraper as ultra


def test_normalize_tiktok_follower_handles_nested_public_stats() -> None:
    row = ultra._normalize_tiktok_follower({
        "user": {
            "id": "42",
            "secUid": "secure-42",
            "uniqueId": "creator.eg",
            "nickname": "Creator Egypt",
            "avatarMedium": "https://cdn.example/avatar.jpg",
            "verified": True,
            "privateAccount": False,
            "stats": {
                "followerCount": 12500,
                "followingCount": 320,
                "heartCount": 70000,
                "videoCount": 84,
            },
        },
    })

    assert row["user_id"] == "42"
    assert row["profile_url"] == "https://www.tiktok.com/@creator.eg"
    assert row["followers"] == 12500
    assert row["likes"] == 70000
    assert row["is_verified"] is True


def test_audience_storage_deduplicates_and_reports_progress(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ultra, "_DB_PATH", tmp_path / "audience.db")
    ultra._db_init()
    run_id = "run-1"
    ultra._audience_create_run(
        run_id,
        "https://www.tiktok.com/@roxashop",
        "tiktok",
        "roxashop",
        2,
    )
    rows = [{
        "user": {
            "id": "1",
            "uniqueId": "one",
            "nickname": "One Creator",
            "stats": {"followerCount": 100},
        },
    }]

    ultra._audience_store_followers(run_id, "tiktok", "roxashop", rows)
    ultra._audience_store_followers(run_id, "tiktok", "roxashop", rows)
    ultra._audience_update_run(run_id, collected=1, pages=1, status="collecting")

    snapshot = ultra._audience_run_snapshot(run_id)
    assert snapshot is not None
    assert snapshot["result_total"] == 1
    assert snapshot["progress"] == 50
    assert snapshot["followers"][0]["username"] == "one"


def test_expected_audience_uses_cached_profile_count(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ultra, "_DB_PATH", tmp_path / "audience.db")
    ultra._db_init()
    with ultra._db_lock:
        connection = ultra.sqlite3.connect(str(ultra._DB_PATH))
        connection.execute(
            """
            INSERT INTO scrapes
            (url, mode, timestamp, data, response_time, status, error, platform, username)
            VALUES (?, ?, 0, ?, 0, 200, '', 'tiktok', 'roxashop')
            """,
            (
                "https://www.tiktok.com/@roxashop",
                "browser",
                json.dumps({"followers": "211.1K"}),
            ),
        )
        connection.commit()
        connection.close()

    assert ultra._audience_expected_total("roxashop") == 211100
