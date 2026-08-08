"""Seed the Scrapling dashboard with realistic demo data.

Run before a UI pass so every page has something to render:

    .venv/Scripts/python scripts/seed_demo.py

The script uses deterministic IDs for runs/jobs/schedules so re-running updates
existing rows rather than piling up duplicates.  Notifications and audit rows
are always appended because they represent events.
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from scrapling_tool.envfile import load_env_file
load_env_file()

from webapp import crm, store  # noqa: E402


DEMO_CREATORS = [
    {
        "platform": "tiktok",
        "username": "charlidamelio",
        "full_name": "Charli D'Amelio",
        "profile_url": "https://www.tiktok.com/@charlidamelio",
        "avatar_url": "https://placehold.co/100x100/FF0050/FFFFFF?text=CD",
        "biography": "dancer • creator • here for a good time",
        "followers": 151_200_000,
        "following": 1_800,
        "likes": 11_400_000_000,
        "avg_views": 12_500_000,
        "engagement_rate": 0.084,
        "quality": 92,
        "is_verified": True,
        "is_private": False,
        "country": "US",
        "city": "Norwalk, CT",
        "emails": ["charli@example.com"],
        "links": [{"url": "https://www.charlidamelio.com", "host": "charlidamelio.com", "kind": "link"}],
        "tags": ["dance", "gen-z", "verified"],
    },
    {
        "platform": "instagram",
        "username": "kyliejenner",
        "full_name": "Kylie",
        "profile_url": "https://www.instagram.com/kyliejenner/",
        "avatar_url": "https://placehold.co/100x100/E1306C/FFFFFF?text=KJ",
        "biography": "kylie cosmetics founder",
        "followers": 399_000_000,
        "following": 97,
        "likes": 0,
        "avg_views": 8_100_000,
        "engagement_rate": 0.032,
        "quality": 88,
        "is_verified": True,
        "is_private": False,
        "country": "US",
        "city": "Calabasas, CA",
        "emails": ["press@kyliecosmetics.com"],
        "links": [{"url": "https://kyliecosmetics.com", "host": "kyliecosmetics.com", "kind": "contact"}],
        "tags": ["beauty", "celebrity", "brand"],
    },
    {
        "platform": "youtube",
        "username": "mkbhd",
        "full_name": "Marques Brownlee",
        "profile_url": "https://www.youtube.com/@mkbhd",
        "avatar_url": "https://placehold.co/100x100/FF0000/FFFFFF?text=M",
        "biography": "Quality tech videos for the masses. Based in NYC.",
        "followers": 19_300_000,
        "following": 0,
        "likes": 0,
        "avg_views": 3_200_000,
        "engagement_rate": 0.045,
        "quality": 96,
        "is_verified": True,
        "is_private": False,
        "country": "US",
        "city": "New York, NY",
        "emails": ["business@mkbhd.com"],
        "links": [{"url": "https://www.mkbhd.com", "host": "mkbhd.com", "kind": "link"}],
        "tags": ["tech", "review", "creator"],
    },
    {
        "platform": "twitter",
        "username": "elonmusk",
        "full_name": "Elon Musk",
        "profile_url": "https://x.com/elonmusk",
        "avatar_url": "https://placehold.co/100x100/1DA1F2/FFFFFF?text=EM",
        "biography": "",
        "followers": 179_000_000,
        "following": 700,
        "likes": 0,
        "avg_views": 5_000_000,
        "engagement_rate": 0.061,
        "quality": 74,
        "is_verified": True,
        "is_private": False,
        "country": "US",
        "city": "Austin, TX",
        "emails": [],
        "links": [{"url": "https://x.com/elonmusk", "host": "x.com", "kind": "aggregator"}],
        "tags": ["tech", "news", "polarizing"],
    },
    {
        "platform": "tiktok",
        "username": "mrbeast",
        "full_name": "MrBeast",
        "profile_url": "https://www.tiktok.com/@mrbeast",
        "avatar_url": "https://placehold.co/100x100/00F2EA/FFFFFF?text=MB",
        "biography": "I want to make the world a better place before I die.",
        "followers": 97_800_000,
        "following": 45,
        "likes": 1_700_000_000,
        "avg_views": 45_000_000,
        "engagement_rate": 0.071,
        "quality": 94,
        "is_verified": True,
        "is_private": False,
        "country": "US",
        "city": "Greenville, NC",
        "emails": ["mrbeast@example.com"],
        "links": [{"url": "https://mrbeast.store", "host": "mrbeast.store", "kind": "contact"}],
        "tags": ["challenge", "philanthropy", "creator"],
    },
    {
        "platform": "instagram",
        "username": "natgeo",
        "full_name": "National Geographic",
        "profile_url": "https://www.instagram.com/natgeo/",
        "avatar_url": "https://placehold.co/100x100/000000/FFD700?text=N",
        "biography": "Experience the world through the eyes of National Geographic photographers.",
        "followers": 279_000_000,
        "following": 137,
        "likes": 0,
        "avg_views": 1_200_000,
        "engagement_rate": 0.019,
        "quality": 85,
        "is_verified": True,
        "is_private": False,
        "country": "US",
        "city": "Washington, DC",
        "emails": ["photos@natgeo.com"],
        "links": [{"url": "https://www.nationalgeographic.com", "host": "nationalgeographic.com", "kind": "link"}],
        "tags": ["photography", "travel", "media"],
    },
    {
        "platform": "youtube",
        "username": "pewdiepie",
        "full_name": "PewDiePie",
        "profile_url": "https://www.youtube.com/@PewDiePie",
        "avatar_url": "https://placehold.co/100x100/282828/FFFFFF?text=P",
        "biography": "I make videos.",
        "followers": 110_000_000,
        "following": 0,
        "likes": 0,
        "avg_views": 4_500_000,
        "engagement_rate": 0.052,
        "quality": 78,
        "is_verified": True,
        "is_private": False,
        "country": "JP",
        "city": "Tokyo",
        "emails": ["pewdiepie@example.com"],
        "links": [{"url": "https://represent.com/pewdiepie", "host": "represent.com", "kind": "contact"}],
        "tags": ["gaming", "entertainment", "veteran"],
    },
    {
        "platform": "tiktok",
        "username": "khaby.lame",
        "full_name": "Khabane Lame",
        "profile_url": "https://www.tiktok.com/@khaby.lame",
        "avatar_url": "https://placehold.co/100x100/25F4EE/000000?text=KL",
        "biography": "If u wanna laugh u r in the right place😂",
        "followers": 162_000_000,
        "following": 78,
        "likes": 2_400_000_000,
        "avg_views": 18_000_000,
        "engagement_rate": 0.093,
        "quality": 90,
        "is_verified": True,
        "is_private": False,
        "country": "IT",
        "city": "Milan",
        "emails": ["khaby@example.com"],
        "links": [{"url": "https://www.khaby-lame.com", "host": "khaby-lame.com", "kind": "link"}],
        "tags": ["comedy", "silent", "verified"],
    },
]


def _random_statuses(n: int) -> list[str]:
    weights = [0.65, 0.20, 0.10, 0.05]
    return random.choices(["ok", "blocked", "error", "timeout"], weights=weights, k=n)


def _seed_creators() -> list[str]:
    rows = []
    for c in DEMO_CREATORS:
        cid = crm.creator_id(c["platform"], c["username"])
        rows.append({
            "id": cid,
            "platform": c["platform"],
            "username": c["username"],
            "full_name": c["full_name"],
            "profile_url": c["profile_url"],
            "avatar_url": c["avatar_url"],
            "biography": c["biography"],
            "followers": c["followers"],
            "following": c["following"],
            "likes": c["likes"],
            "avg_views": c["avg_views"],
            "engagement_rate": c["engagement_rate"],
            "quality": c["quality"],
            "is_verified": 1 if c["is_verified"] else 0,
            "is_private": 1 if c["is_private"] else 0,
            "country": c["country"],
            "city": c["city"],
            "emails": c["emails"],
            "phones": [],
            "links": c["links"],
            "tags": c["tags"],
            "status": "new",
            "notes": "",
        })
    summary = crm.upsert_creators(rows, run_id="seed-demo")
    print(f"creators: +{summary['added']} / updated {summary['updated']} / skipped {summary['skipped']}")
    return [r["id"] for r in rows]


def _seed_lists(creator_ids: list[str]) -> None:
    existing = {lst["name"]: lst for lst in crm.list_lists()}
    lists_to_make = [
        ("Viral prospects", "High-reach creators to pitch first.", "rose"),
        ("Verified only", "Blue-check guaranteed.", "blue"),
        ("Micro-influencers", "Sub-20M follower niche accounts.", "emerald"),
    ]
    list_ids = []
    for name, desc, color in lists_to_make:
        if name in existing:
            lid = existing[name]["id"]
        else:
            lid = crm.create_list(name, desc, color)["id"]
            print(f"list created: {name}")
        list_ids.append(lid)

    for lid in list_ids:
        crm.add_to_list(lid, creator_ids)

    qualities = {
        cid: next(c for c in DEMO_CREATORS
                  if crm.creator_id(c["platform"], c["username"]) == cid)["quality"]
        for cid in creator_ids
    }
    priority = sorted(creator_ids, key=lambda c: qualities[c], reverse=True)[:4]
    crm.add_tags(priority, ["priority"])
    print(f"lists: assigned {len(creator_ids)} creators, tagged {len(priority)} priority")


def _seed_jobs() -> None:
    jobs = [
        {
            "id": "job-demo-discovery",
            "name": "Friday discovery sweep",
            "description": "Weekly refresh of top accounts by engagement.",
            "kind": "discover",
            "params": {"platforms": ["tiktok", "instagram"], "seed": "dance"},
            "options": {"mode": "http", "concurrency": 10, "retries": 3},
            "tags": ["weekly", "auto"],
            "pinned": True,
        },
        {
            "id": "job-demo-posts",
            "name": "Top creators — latest posts",
            "description": "Pull the last 12 posts for priority creators.",
            "kind": "posts",
            "params": {"platforms": ["tiktok", "instagram"], "urls": ["https://www.tiktok.com/@charlidamelio"]},
            "options": {"mode": "stealth", "network_idle": True, "retries": 4},
            "tags": ["content"],
            "pinned": False,
        },
        {
            "id": "job-demo-mentions",
            "name": "Brand mention tracker",
            "description": "Track who is talking about #scrapling.",
            "kind": "mentions",
            "params": {"keywords": ["#scrapling", "scrapling tool"], "platforms": ["twitter", "youtube"]},
            "options": {"mode": "http", "use_cache": True},
            "tags": ["brand", "monitoring"],
            "pinned": False,
        },
    ]
    now = time.time()
    backend = store._backend  # type: ignore[attr-defined]
    for j in jobs:
        try:
            backend.create_job(
                j["id"], j["name"], j["description"], j["kind"],
                j["params"], j["options"], j["tags"], int(j["pinned"]),
                "admin", now,
            )
        except Exception:
            pass  # already exists — fall through to update
        backend.update_job(
            j["id"],
            {
                "run_count": random.randint(1, 12),
                "last_run_id": "run-demo-1" if j["id"] == "job-demo-discovery" else "",
                "last_run_at": now - random.randint(60, 86400 * 5),
                "last_run_status": "finished",
            },
            now,
        )
    print(f"saved jobs: upserted {len(jobs)}")


def _seed_runs() -> None:
    runs = [
        ("run-demo-1", "discover", "Friday discovery sweep", {"platforms": ["tiktok", "instagram"]}, 60),
        ("run-demo-2", "posts", "Top creators — latest posts", {"platforms": ["tiktok"]}, 40),
        ("run-demo-3", "mentions", "Brand mention tracker", {"keywords": ["#scrapling"]}, 30),
        ("run-demo-4", "scrape", "Backup capture batch", {"urls": ["https://example.com/a", "https://example.com/b"]}, 20),
        ("run-demo-5", "discover", "Weekend lookalike expansion", {"platforms": ["youtube"]}, 50),
    ]

    backend = store._backend  # type: ignore[attr-defined]
    for run_id, kind, label, params, total in runs:
        now = time.time() - random.randint(0, 86400 * 7)
        statuses = _random_statuses(total)
        results = []
        ok = blocked = errors = 0
        for i, status in enumerate(statuses):
            if status == "ok":
                ok += 1
                results.append({
                    "idx": i,
                    "status": 200,
                    "url": f"https://demo.local/{run_id}/{i}",
                    "blocked": False,
                    "data": {"followers": random.randint(10_000, 50_000_000)},
                })
            elif status == "blocked":
                blocked += 1
                results.append({
                    "idx": i,
                    "status": 200,
                    "url": f"https://demo.local/{run_id}/{i}",
                    "blocked": True,
                    "data": {"blocked_reason": "captcha"},
                })
            else:
                errors += 1
                results.append({
                    "idx": i,
                    "status": 500 if status == "error" else 0,
                    "url": f"https://demo.local/{run_id}/{i}",
                    "error": f"demo {status}",
                })

        try:
            backend.create_run(run_id, kind, params, label, "", now)
            store.finish_run(run_id, "finished", results, "")
        except Exception:
            pass  # already exists

    if store.get_schedule("sched-demo-weekly") is None:
        backend.create_schedule(
            "sched-demo-weekly",
            "Weekly discovery refresh",
            "discover",
            {"platforms": ["tiktok", "instagram"]},
            10080,
            time.time(),
        )
        print("schedule: created weekly discovery refresh")
    else:
        print("schedule: weekly discovery refresh already exists")

    print(f"runs: upserted {len(runs)}")


def _seed_provider_and_proxy() -> None:
    crm.set_provider_key("tavily", os.getenv("TAVILY_API_KEY", "demo-tavily"), enabled=True, order_index=10)
    crm.set_provider_key("serper", os.getenv("SERPER_API_KEY", "demo-serper"), enabled=True, order_index=20)
    crm.set_provider_key("serpapi", os.getenv("SERPAPI_API_KEY", "demo-serpapi"), enabled=False, order_index=30)
    crm.set_provider_key("scrapegraph", os.getenv("SCRAPEGRAPH_API_KEY", "demo-sg"), enabled=True, order_index=40)

    proxies = [
        ("http://proxy-us.demo:8080", "US rotating"),
        ("http://proxy-eu.demo:8080", "EU residential"),
        ("socks5://proxy-asia.demo:1080", "Asia static"),
    ]
    for url, label in proxies:
        crm.add_proxy(url, label)
    for provider in ["tavily", "serper", "scrapegraph"]:
        for _ in range(random.randint(3, 8)):
            crm.record_provider_call(provider, ok=random.random() > 0.15, elapsed_ms=random.randint(120, 1200))
    for domain, mode in [("tiktok.com", "stealth"), ("instagram.com", "http"), ("youtube.com", "http"), ("x.com", "browser")]:
        for _ in range(random.randint(5, 15)):
            outcome = random.choices(["ok", "blocked", "error"], weights=[0.6, 0.25, 0.15])[0]
            crm.record_fetch(domain, mode, outcome, random.randint(300, 3000))
    print("providers/proxies/fetch stats seeded")


def _seed_notifications_and_audit() -> None:
    notes = [
        ("info", "Demo data loaded — dashboard is ready to inspect."),
        ("success", "Friday discovery sweep finished with 78% success rate."),
        ("warn", "3 proxies are down; rotation pool reduced."),
        ("error", "TikTok stealth mode hit a captcha wall on 8 profiles."),
    ]
    for level, message in notes:
        store.add_notification(level, "system", message)

    for action, obj_kind, detail in [
        ("run_created", "run", "Started run-demo-1"),
        ("run_finished", "run", "Finished run-demo-1"),
        ("creator_tagged", "creator", "Tagged 4 creators as priority"),
        ("settings_changed", "settings", "Notifications enabled"),
    ]:
        store.log_audit(action, obj_kind, "seed", detail, "admin")
    print("notifications & audit seeded")


def main() -> None:
    store.init()
    crm.init()
    print(f"using backend: {store.BACKEND}")

    cids = _seed_creators()
    _seed_lists(cids)
    _seed_jobs()
    _seed_runs()
    _seed_provider_and_proxy()
    _seed_notifications_and_audit()

    stats = store.stats()
    print("dashboard stats now:", stats)


if __name__ == "__main__":
    main()
