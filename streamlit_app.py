#!/usr/bin/env python3
"""
Scrapling Tool — Streamlit Dashboard
A native Streamlit app that wraps the scraping engine from ultra_scraper.py.
Designed for Streamlit Community Cloud (free, no credit card).
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure the project root is on sys.path so we can import ultra_scraper, and
# src/ so we can import the scrapling_tool package (src-layout, see
# [tool.setuptools.package-dir] in pyproject.toml). Hosts that only run
# `streamlit run streamlit_app.py` against requirements.txt never install the
# local package, so without src/ here ultra_scraper dies on import.
_PROJECT_ROOT = Path(__file__).resolve().parent
for _path in (_PROJECT_ROOT, _PROJECT_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# Import the scraping engine
from ultra_scraper import (  # noqa: E402
    _HAS_FETCHERS,
    _HAS_PLAYWRIGHT,
    _canonical_profile_fields,
    _canonical_social_url,
    _discover,
    _find_brand_mentions,
    _find_user_posts,
    _parse_terms,
    _run_lookalike,
    _scrape_one,
    _scrape_stats,
    _search_passes,
    _to_int_count,
    _validate_url,
    derive_seed_keywords,
    expand_brand_aliases,
    normalize_recency,
    split_keywords,
    _term_label,
)
from scrapling_tool.discovery import SUPPORTED_PLATFORMS  # noqa: E402

TARGETS = ("accounts", "videos", "posts", "stories", "hashtags")
_POST_PLATFORMS = ("tiktok", "instagram", "youtube", "twitter", "snapchat")


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Scrapling Tool",
    page_icon="🕷️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Dark theme matching the original dashboard
st.markdown("""
<style>
    /* Dark theme overrides */
    .stApp {
        background: #0a0910;
    }
    /* Tighter spacing */
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
        max-width: 1400px;
    }
    /* Metric cards */
    [data-testid="stMetric"] {
        background: #15131f;
        border: 1px solid #2b2738;
        border-radius: 10px;
        padding: 15px;
    }
    /* Expander styling */
    .streamlit-expanderHeader {
        background: #1d1a29;
        border-radius: 8px;
    }
    /* Status badges */
    .badge-ok { color: #10b981; font-weight: bold; }
    .badge-err { color: #ef4444; font-weight: bold; }
    .badge-warn { color: #f59e0b; font-weight: bold; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _run_async(coro):
    """Run an async coroutine in Streamlit's sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Streamlit doesn't have a running loop, but just in case
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        pass
    return asyncio.run(coro)


def _fmt_count(val) -> str:
    """Format large numbers: 1500000 -> 1.5M."""
    n = _to_int_count(val)
    if n == 0:
        return str(val) if val else ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _results_to_df(results: list[dict]) -> pd.DataFrame:
    """Convert scraping results to a flat DataFrame."""
    rows = []
    for r in results:
        rows.append({
            "Platform": r.get("platform", "").title(),
            "Username": r.get("username", ""),
            "Full Name": r.get("full_name", ""),
            "Followers": _fmt_count(r.get("followers")),
            "Following": _fmt_count(r.get("following")),
            "Likes": _fmt_count(r.get("likes")),
            "Avg Views": _fmt_count(r.get("avg_views")),
            "Verified": "✓" if r.get("is_verified") else "",
            "Private": "🔒" if r.get("is_private") else "",
            "Bio": (r.get("biography", "") or "")[:120],
            "Emails": ", ".join(r.get("emails", [])),
            "Country": r.get("country", ""),
            "City": r.get("city", ""),
            "Profile URL": r.get("profile_url", r.get("url", "")),
            "Status": r.get("status", 0),
            "Error": r.get("error", ""),
            "Response Time": r.get("response_time", ""),
        })
    return pd.DataFrame(rows)


def _df_to_csv_download(df: pd.DataFrame, filename: str = "results.csv"):
    """Create a CSV download button."""
    csv = df.to_csv(index=False).encode("utf-8")
    b64 = base64.b64encode(csv).decode()
    href = f'<a href="data:file/csv;base64,{b64}" download="{filename}">📥 Download CSV</a>'
    st.markdown(href, unsafe_allow_html=True)


def _df_to_json_download(data: list[dict], filename: str = "results.json"):
    """Create a JSON download button."""
    json_str = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    b64 = base64.b64encode(json_str).decode()
    href = f'<a href="data:application/json;base64,{b64}" download="{filename}">📥 Download JSON</a>'
    st.markdown(href, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🕷️ Scrapling Tool")
    st.caption("v1.3.0 — Web Scraping Dashboard")

    # Status indicators
    st.markdown("### System Status")
    col1, col2 = st.columns(2)
    with col1:
        if _HAS_FETCHERS:
            st.markdown("✅ Scrapling")
        else:
            st.markdown("❌ Scrapling")
    with col2:
        if _HAS_PLAYWRIGHT:
            st.markdown("✅ Playwright")
        else:
            st.markdown("⚠️ No Playwright")

    if not _HAS_FETCHERS:
        st.warning("Scrapling fetchers not available. Only HTTP mode will work.")
    if not _HAS_PLAYWRIGHT:
        st.info("Playwright not installed. Browser/Stealth modes unavailable.")

    st.divider()

    # Mode selector
    st.markdown("### Fetch Mode")
    mode = st.selectbox(
        "Mode",
        options=["http", "browser", "stealth"] if _HAS_PLAYWRIGHT else ["http"],
        help=(
            "HTTP: fast curl_cffi impersonation\n"
            "Browser: Playwright Chromium\n"
            "Stealth: anti-detection + Cloudflare bypass"
        ),
    )

    # Common options
    col_t, col_c = st.columns(2)
    with col_t:
        timeout = st.number_input("Timeout (s)", 5, 120, 30)
    with col_c:
        concurrency = st.number_input("Concurrency", 1, 50, 15)

    col_r, col_e = st.columns(2)
    with col_r:
        retries = st.number_input("Retries", 0, 5, 2)
    with col_e:
        auto_escalate = st.checkbox("Auto-escalate", help="If HTTP fails, try browser then stealth")

    use_cache = st.checkbox("Use cache", value=True)
    proxy = st.text_input("Proxy URL", placeholder="http://user:pass@host:port")

    st.divider()
    st.markdown("Made with ❤️ via Streamlit Cloud")


# ---------------------------------------------------------------------------
# Main content — tabs
# ---------------------------------------------------------------------------
st.title("🕷️ Scrapling Tool — Dashboard")
st.markdown("Scrape profiles, discover creators, find lookalikes, and search for brand mentions across social platforms.")

tab_scrape, tab_search, tab_lookalike, tab_posts = st.tabs([
    "🔎 Scrape URLs",
    "🔍 Discover Creators",
    "👤 Lookalike Finder",
    "📰 Post / Mention Finder",
])


# ---------------------------------------------------------------------------
# Tab 1: Scrape URLs
# ---------------------------------------------------------------------------
with tab_scrape:
    st.markdown("### Scrape one or more profile/page URLs")
    st.caption("Enter social media URLs (TikTok, Instagram, YouTube, Twitter/X, Snapchat) or any web page.")

    urls_input = st.text_area(
        "URLs (one per line)",
        height=120,
        placeholder="https://www.tiktok.com/@username\nhttps://www.instagram.com/username/\nhttps://www.youtube.com/@channel",
        key="scrape_urls",
    )

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        scrape_btn = st.button("🚀 Scrape", type="primary", use_container_width=True)
    with col_info:
        if urls_input.strip():
            url_count = len([u for u in urls_input.strip().split("\n") if u.strip()])
            st.caption(f"{url_count} URL(s) ready")

    if scrape_btn:
        raw_urls = [u.strip() for u in urls_input.strip().split("\n") if u.strip()]
        if not raw_urls:
            st.warning("Enter at least one URL.")
        else:
            # Validate and deduplicate
            valid_urls = []
            for u in raw_urls:
                canonical = _canonical_social_url(u)
                ok, result = _validate_url(canonical)
                if ok:
                    valid_urls.append(result)
                else:
                    st.error(f"Invalid URL: {u} — {result}")

            if valid_urls:
                progress = st.progress(0.0, text="Scraping...")
                status_text = st.empty()

                async def _scrape_all():
                    sem = asyncio.Semaphore(concurrency)
                    tasks = [
                        _scrape_one(
                            u, mode, timeout, sem,
                            retries=retries,
                            auto_escalate=auto_escalate,
                            use_cache=use_cache,
                            options={"proxy": proxy, "headless": True},
                        )
                        for u in valid_urls
                    ]
                    results = []
                    for i, coro in enumerate(asyncio.as_completed(tasks)):
                        r = await coro
                        results.append(r)
                        progress_val = (i + 1) / len(valid_urls)
                        progress.progress(progress_val, text=f"Scraped {i + 1}/{len(valid_urls)}")
                    return results

                results = _run_async(_scrape_all())
                progress.progress(1.0, text="Done!")
                time.sleep(0.3)
                progress.empty()

                # Stats
                stats = _scrape_stats(results)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Total", stats["total"])
                c2.metric("Successful", stats["ok"])
                c3.metric("Errors", stats["errors"])
                c4.metric("Avg Time", f"{stats['avgTime']}s")

                # Results table
                df = _results_to_df(results)
                st.markdown("### Results")
                st.dataframe(df, use_container_width=True, hide_index=True)

                # Downloads
                if not df.empty:
                    col1, col2 = st.columns(2)
                    with col1:
                        _df_to_csv_download(df, "scrape_results.csv")
                    with col2:
                        _df_to_json_download(results, "scrape_results.json")


# ---------------------------------------------------------------------------
# Tab 2: Discover Creators
# ---------------------------------------------------------------------------
with tab_search:
    st.markdown("### Discover creators by keyword across platforms")
    st.caption("Search for social media accounts, videos, posts, or hashtags matching your keywords.")

    col1, col2 = st.columns(2)
    with col1:
        keywords = st.text_input(
            "Keywords (comma-separated)",
            placeholder="fitness, yoga, wellness",
            key="search_keywords",
        )
    with col2:
        target = st.selectbox("Target", TARGETS, help="What to search for")

    platforms = st.multiselect(
        "Platforms",
        options=list(SUPPORTED_PLATFORMS),
        default=["tiktok", "instagram"],
        key="search_platforms",
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        per_platform = st.number_input("Results per platform", 1, 40, 15, key="search_pp")
    with col2:
        limit = st.number_input("Max results", 1, 500, 100, key="search_limit")
    with col3:
        location = st.text_input("Location", placeholder="US, UK, etc.", key="search_loc")

    with st.expander("Advanced filters"):
        col1, col2 = st.columns(2)
        with col1:
            min_followers = st.number_input("Min followers", 0, 10_000_000, 0, 1000, key="search_minf")
            verified_only = st.checkbox("Verified only", key="search_verified")
        with col2:
            max_followers = st.number_input("Max followers", 0, 10_000_000, 0, 1000, key="search_maxf")
            bio_keyword = st.text_input("Bio keyword", key="search_bio")
        recency = st.selectbox("Recency", ["any", "day", "week", "month", "year"], key="search_recency")

    if st.button("🔍 Discover", type="primary", key="search_btn"):
        kw_list = split_keywords(keywords, limit=12)
        if not kw_list:
            st.warning("Enter at least one keyword.")
        elif not platforms:
            st.warning("Select at least one platform.")
        else:
            with st.spinner("Discovering creators..."):
                async def _do_search():
                    found = await _discover(
                        kw_list, platforms,
                        location=location,
                        per_platform=per_platform,
                        timeout=timeout,
                        target=target,
                        search_providers=None,
                    )
                    candidates = [(plat, hit) for plat, lst in found.items() for hit in lst]
                    sem = asyncio.Semaphore(concurrency)

                    async def _enrich(plat, hit):
                        data = await _scrape_one(
                            hit["url"], mode, timeout, sem,
                            retries=retries,
                            auto_escalate=auto_escalate,
                            use_cache=use_cache,
                            options={"proxy": proxy, "headless": True},
                        )
                        data.setdefault("platform", plat)
                        data.setdefault("target", target)
                        return data

                    results = await asyncio.gather(*[_enrich(p, h) for p, h in candidates])
                    # Filter
                    collected = [
                        (d, _search_passes(
                            d, min_f=min_followers, max_f=max_followers,
                            verified_only=verified_only, bio_kw=bio_keyword,
                            target=target,
                        ))
                        for d in results
                    ]
                    # Rank
                    matched = [d for d, ok in collected if ok]
                    matched.sort(
                        key=lambda d: (
                            float(d.get("discovery_score", 0) or 0),
                            _to_int_count(d.get("followers")),
                            1 if d.get("is_verified") else 0,
                            0 if d.get("error") else 1,
                        ),
                        reverse=True,
                    )
                    return matched[:limit], found

                results, found = _run_async(_do_search())

            # Stats
            st.markdown("### Discovery Results")
            c1, c2, c3 = st.columns(3)
            c1.metric("Candidates", sum(len(v) for v in found.values()))
            c2.metric("Matched", len(results))
            c3.metric("Platforms", len(found))

            # Discovered by platform
            disc_cols = st.columns(len(found))
            for i, (plat, hits) in enumerate(found.items()):
                with disc_cols[i]:
                    st.metric(plat.title(), len(hits))

            if results:
                df = _results_to_df(results)
                st.dataframe(df, use_container_width=True, hide_index=True)
                col1, col2 = st.columns(2)
                with col1:
                    _df_to_csv_download(df, "discovery_results.csv")
                with col2:
                    _df_to_json_download(results, "discovery_results.json")
            else:
                st.info("No results matched your filters. Try widening them.")


# ---------------------------------------------------------------------------
# Tab 3: Lookalike Finder
# ---------------------------------------------------------------------------
with tab_lookalike:
    st.markdown("### Find lookalike creators based on seed profiles")
    st.caption("Provide one or more profile URLs — we'll analyze them and find similar creators across platforms.")

    seed_input = st.text_area(
        "Seed profile URLs (one per line)",
        height=100,
        placeholder="https://www.tiktok.com/@username\nhttps://www.instagram.com/username/",
        key="lookalike_seeds",
    )

    platforms_l = st.multiselect(
        "Platforms to search",
        options=list(SUPPORTED_PLATFORMS),
        default=list(SUPPORTED_PLATFORMS),
        key="lookalike_platforms",
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        per_platform_l = st.number_input("Per platform", 1, 40, 12, key="lookalike_pp")
    with col2:
        limit_l = st.number_input("Max results", 1, 500, 100, key="lookalike_limit")
    with col3:
        min_score = st.number_input("Min similarity score", 0.0, 100.0, 20.0, 5.0, key="lookalike_minscore")

    signals_input = st.text_input(
        "Extra signals (optional, comma-separated)",
        placeholder="fashion, lifestyle, influencer",
        key="lookalike_signals",
    )

    if st.button("👤 Find Lookalikes", type="primary", key="lookalike_btn"):
        raw_seeds = [s.strip() for s in seed_input.strip().split("\n") if s.strip()]
        if not raw_seeds:
            st.warning("Enter at least one seed URL.")
        else:
            # Validate seeds
            valid_seeds = []
            for s in raw_seeds:
                canonical = _canonical_social_url(s)
                ok, result = _validate_url(canonical)
                if ok:
                    valid_seeds.append(result)
                else:
                    st.error(f"Invalid URL: {s}")

            if valid_seeds:
                with st.spinner("Analyzing seeds and finding lookalikes..."):
                    p = {
                        "seeds": valid_seeds,
                        "platforms": platforms_l or list(SUPPORTED_PLATFORMS),
                        "signals": split_keywords(signals_input, limit=12),
                        "location": "",
                        "search_providers": None,
                        "perPlatform": per_platform_l,
                        "limit": limit_l,
                        "minScore": min_score,
                        "mode": mode,
                        "concurrency": concurrency,
                        "timeout": timeout,
                        "retries": retries,
                        "auto_escalate": auto_escalate,
                        "use_cache": use_cache,
                    }
                    payload = _run_async(_run_lookalike(p))

                if payload.get("error"):
                    st.error(f"Error: {payload['error']}")
                else:
                    results = payload.get("results", [])
                    signals = payload.get("signals", [])

                    st.markdown("### Lookalike Results")
                    c1, c2 = st.columns(2)
                    c1.metric("Found", len(results))
                    c2.metric("Signals used", len(signals))

                    if signals:
                        st.caption("**Derived signals:** " + ", ".join(signals))

                    if results:
                        df = _results_to_df(results)
                        st.dataframe(df, use_container_width=True, hide_index=True)
                        col1, col2 = st.columns(2)
                        with col1:
                            _df_to_csv_download(df, "lookalike_results.csv")
                        with col2:
                            _df_to_json_download(results, "lookalike_results.json")
                    else:
                        st.info("No lookalikes found above the similarity threshold.")


# ---------------------------------------------------------------------------
# Tab 4: Post / Mention Finder
# ---------------------------------------------------------------------------
with tab_posts:
    st.markdown("### Find posts and brand mentions")
    st.caption("Search for posts matching hashtags/mentions by specific users, or discover creators mentioning a brand.")

    sub_mode = st.radio(
        "Mode",
        options=["user_posts", "brand_mentions"],
        horizontal=True,
        help="User posts: find posts by specific users. Brand mentions: discover creators mentioning a brand.",
    )

    if sub_mode == "user_posts":
        usernames = st.text_input(
            "Usernames (comma-separated)",
            placeholder="username1, username2",
            key="posts_usernames",
        )
        terms = st.text_input(
            "Hashtags / mentions (comma-separated)",
            placeholder="#hashtag, @mention",
            key="posts_terms",
        )
        platforms_p = st.multiselect(
            "Platforms",
            options=list(_POST_PLATFORMS),
            default=["tiktok", "instagram"],
            key="posts_platforms",
        )
        col1, col2 = st.columns(2)
        with col1:
            per_platform_p = st.number_input("Per platform", 1, 100, 25, key="posts_pp")
        with col2:
            deep = st.checkbox("Deep search", value=True, key="posts_deep")

        if st.button("📰 Find Posts", type="primary", key="posts_btn"):
            user_list = [u.strip().lstrip("@") for u in usernames.split(",") if u.strip()]
            term_list = _parse_terms(terms)
            if not user_list or not term_list:
                st.warning("Enter usernames and at least one hashtag/mention.")
            else:
                with st.spinner("Finding posts..."):
                    async def _do_posts():
                        sem = asyncio.Semaphore(concurrency)
                        async def _bounded(u):
                            async with sem:
                                return await _find_user_posts(
                                    u, term_list, platforms_p,
                                    per_platform=per_platform_p,
                                    timeout=timeout,
                                    deep=deep,
                                    engines=True,
                                    mode=mode,
                                    proxy=proxy,
                                    search_providers=None,
                                )
                        founds = await asyncio.gather(
                            *[_bounded(u) for u in user_list],
                            return_exceptions=True,
                        )
                        rows = []
                        for found in founds:
                            if isinstance(found, Exception):
                                continue
                            for lst in found.values():
                                rows.extend(lst)
                        return rows

                    rows = _run_async(_do_posts())

                st.markdown(f"### Found {len(rows)} posts")
                if rows:
                    # Flatten for display
                    display_rows = []
                    for r in rows:
                        display_rows.append({
                            "Platform": r.get("platform", "").title(),
                            "Username": r.get("username", ""),
                            "URL": r.get("url", ""),
                            "Title": (r.get("title", "") or "")[:80],
                            "Matched": ", ".join(r.get("matched_terms", [])),
                            "Followers": _fmt_count(r.get("followers")),
                            "Snippet": (r.get("snippet", "") or "")[:120],
                        })
                    df = pd.DataFrame(display_rows)
                    st.dataframe(df, use_container_width=True, hide_index=True)
                    _df_to_csv_download(df, "posts_results.csv")
                    _df_to_json_download(rows, "posts_results.json")
                else:
                    st.info("No posts found.")

    else:  # brand_mentions
        brand_seeds = st.text_input(
            "Brand handle(s) / URL(s) (comma-separated)",
            placeholder="@brandname, https://www.tiktok.com/@brandname",
            key="bm_seeds",
        )
        terms_bm = st.text_input(
            "Mentions / hashtags (comma-separated)",
            placeholder="#brand, @brand",
            key="bm_terms",
        )
        platforms_bm = st.multiselect(
            "Platforms",
            options=list(_POST_PLATFORMS),
            default=list(_POST_PLATFORMS),
            key="bm_platforms",
        )
        col1, col2 = st.columns(2)
        with col1:
            per_platform_bm = st.number_input("Per platform", 1, 100, 25, key="bm_pp")
        with col2:
            recency_bm = st.selectbox("Recency", ["any", "day", "week", "month", "year"], key="bm_recency")

        if st.button("🔍 Discover Mentions", type="primary", key="bm_btn"):
            seeds = [s.strip() for s in brand_seeds.split(",") if s.strip()]
            term_list = _parse_terms(terms_bm)
            if not seeds or not term_list:
                st.warning("Enter brand handle(s) and mention term(s).")
            else:
                with st.spinner("Discovering brand mentions..."):
                    payload = _run_async(_find_brand_mentions(
                        term_list, platforms_bm,
                        per_platform=per_platform_bm,
                        timeout=timeout,
                        proxy=proxy,
                        mode=mode,
                        search_providers=None,
                        enrich_creators=True,
                        concurrency=concurrency,
                        recency=normalize_recency(recency_bm),
                        aliases=expand_brand_aliases(seeds, [_term_label(t) for t in term_list]),
                    ))

                creators = payload.get("creators", [])
                posts = payload.get("posts", [])
                st.markdown("### Brand Mention Results")
                c1, c2 = st.columns(2)
                c1.metric("Creators", len(creators))
                c2.metric("Posts", len(posts))

                if creators:
                    display_creators = []
                    for c in creators:
                        display_creators.append({
                            "Platform": c.get("platform", "").title(),
                            "Username": c.get("username", ""),
                            "Followers": _fmt_count(c.get("followers")),
                            "URL": c.get("profile_url", c.get("url", "")),
                            "Posts": c.get("post_count", c.get("posts", 0)),
                            "Confidence": c.get("confidence", ""),
                        })
                    df_c = pd.DataFrame(display_creators)
                    st.markdown("#### Creators")
                    st.dataframe(df_c, use_container_width=True, hide_index=True)
                    _df_to_csv_download(df_c, "brand_creators.csv")
                    _df_to_json_download(creators, "brand_creators.json")

                if posts:
                    display_posts = []
                    for p_row in posts:
                        display_posts.append({
                            "Platform": p_row.get("platform", "").title(),
                            "Username": p_row.get("username", ""),
                            "URL": p_row.get("url", ""),
                            "Matched": ", ".join(p_row.get("matched_terms", [])),
                            "Confidence": p_row.get("confidence", p_row.get("confidence_tier", "")),
                        })
                    df_p = pd.DataFrame(display_posts)
                    st.markdown("#### Posts")
                    st.dataframe(df_p, use_container_width=True, hide_index=True)
                    _df_to_csv_download(df_p, "brand_posts.csv")
                    _df_to_json_download(posts, "brand_posts.json")

                if not creators and not posts:
                    st.info("No mentions found for this brand.")