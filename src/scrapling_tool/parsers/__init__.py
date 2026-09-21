"""scrapling_tool.parsers — content extraction helpers.

Currently hosts :func:`extract_profile`, which pulls a profile's main caption
(bio / description) and the captions of its last 3–5 posts / reels / stories
out of a raw HTML payload or a structured (oEmbed / JSON-LD) payload.

The strategy is layered: try the *cheapest* structured path first, fall
through to meta-tag scraping, fall through to inline ``window._sharedData``
/ ``__UNIVERSAL_DATA__`` blobs that some sites embed for their own SPA
bootstrapping. None of the strategies hit the network — they all run on
the bytes a provider already returned.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# -------- types --------


@dataclass(slots=True)
class PostExcerpt:
    """A single recent post / reel / story entry."""

    kind: str  # "post" | "reel" | "story" | "video" | "tweet" | "thread"
    caption: str
    url: str = ""
    posted_at: str = ""


@dataclass(slots=True)
class ProfileExcerpt:
    """Result of :func:`extract_profile`."""

    main_caption: str = ""
    handle: str = ""
    display_name: str = ""
    avatar: str = ""
    recent_posts: list[PostExcerpt] = field(default_factory=list)
    source: str = ""  # which strategy produced this
    raw_data: dict[str, Any] = field(default_factory=dict)


# -------- helpers --------


_META_PROP = re.compile(
    rb'<meta\s+(?:[^>]*?\s+)?(?:property|name)\s*=\s*(?P<q1>["\'])(?P<key>[^"\']*?)(?P=q1)\s+content\s*=\s*(?P<q2>["\'])(?P<value>.*?)(?P=q2)',
    re.I | re.S,
)
_META_PROP_REV = re.compile(
    rb'<meta\s+(?:[^>]*?\s+)?content\s*=\s*(?P<q2>["\'])(?P<value>.*?)(?P=q2)\s+(?:property|name)\s*=\s*(?P<q1>["\'])(?P<key>[^"\']*?)(?P=q1)',
    re.I | re.S,
)
_OG_DESC_KEYS = {b"og:description", b"og:title", b"og:image", b"twitter:description", b"twitter:title", b"description"}
_SCRIPT_LD_JSON = re.compile(
    rb'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
# Instagram embeds the SPA bootstrap as a JSON blob in a <script> tag.
_INSTAGRAM_SHARED = re.compile(
    rb'window\._sharedData\s*=\s*(\{.*?\});',
    re.S,
)
# Newer Instagram / TikTok pages use a script type=application/ld+json OR
# an inline JSON blob keyed by __INITIAL_STATE__ / __UNIVERSAL_DATA__.
_INITIAL_STATE = re.compile(
    rb'(?:window\.|var\s+)?(?:__INITIAL_STATE__|__UNIVERSAL_DATA__|__NEXT_DATA__|__NUXT__)\s*=\s*(\{.*?\})\s*[;<]',
    re.S,
)


def _decode(blob: bytes) -> str:
    return blob.decode("utf-8", errors="replace")


def _meta_tags(html: bytes) -> dict[str, str]:
    """Parse ``<meta property=… content=…>`` into a normalized dict.

    Tolerant of apostrophes inside attribute values: the opening quote
    character is captured and matched against the closing one.
    """
    out: dict[str, str] = {}
    for pat in (_META_PROP, _META_PROP_REV):
        for m in pat.finditer(html):
            key = m.group("key")
            value = m.group("value")
            key_lc = key.lower()
            if key_lc in _OG_DESC_KEYS:
                out[key_lc.decode("utf-8", "replace")] = _decode(value)
    return out


def _ldjson_blocks(html: bytes) -> list[dict[str, Any]]:
    """Return parsed JSON-LD blocks. Tolerates top-level arrays."""
    out: list[dict[str, Any]] = []
    for m in _SCRIPT_LD_JSON.finditer(html):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            try:
                parsed = json.loads(f"[{raw}]")
            except ValueError:
                continue
        if isinstance(parsed, list):
            out.extend(x for x in parsed if isinstance(x, dict))
        elif isinstance(parsed, dict):
            out.append(parsed)
    return out


def _node_text(node: dict[str, Any]) -> str:
    """Extract a caption-ish string from a JSON-LD node.

    Looks at ``headline`` → ``name`` → ``description`` → ``caption`` → ``text``
    in that order, recursing into ``@graph`` lists.
    """
    for key in ("headline", "name", "description", "caption", "text"):
        v = node.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    graph = node.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            if isinstance(item, dict):
                t = _node_text(item)
                if t:
                    return t
    return ""


def _node_url(node: dict[str, Any]) -> str:
    v = node.get("url")
    if isinstance(v, str):
        return v
    main = node.get("mainEntityOfPage")
    if isinstance(main, dict):
        u = main.get("@id") or main.get("url")
        if isinstance(u, str):
            return u
    return ""


def _node_date(node: dict[str, Any]) -> str:
    for key in ("datePublished", "dateCreated", "uploadDate", "publishedAt"):
        v = node.get(key)
        if isinstance(v, str) and v:
            return v
    return ""


# -------- main entry point --------

# Cap so a single response can't blow the response size
_MAX_RECENT_POSTS = 5


def extract_profile(
    html: bytes | str,
    *,
    url: str = "",
    oembed: dict[str, Any] | None = None,
    max_recent: int = _MAX_RECENT_POSTS,
) -> ProfileExcerpt:
    """Return a :class:`ProfileExcerpt` parsed from a response body.

    Parameters
    ----------
    html : bytes | str
        The response body. Bytes is preferred (cheaper, no decode).
    url : str
        The page URL — used as a fallback to seed the handle when nothing
        else is available.
    oembed : dict, optional
        If the caller already has an oEmbed payload (e.g. scraped from
        YouTube/TikTok), pass it here to short-circuit the extraction.
    max_recent : int
        Cap on ``recent_posts`` (default 5, hard-capped at 5).
    """
    cap = max(0, min(max_recent, _MAX_RECENT_POSTS))
    body = html.encode("utf-8") if isinstance(html, str) else html

    # --- strategy 1: oEmbed (free, no parse cost) ---
    if oembed:
        profile = _from_oembed(oembed, url)
        if profile:
            return profile

    # --- strategy 2: JSON-LD ---
    blocks = _ldjson_blocks(body)
    profile = _from_jsonld(blocks, url, cap)
    if profile and (profile.main_caption or profile.recent_posts):
        return profile

    # --- strategy 3: meta tags ---
    meta = _meta_tags(body)
    profile = _from_meta_tags(meta, url, cap, body)
    if profile and (profile.main_caption or profile.recent_posts):
        return profile

    # --- strategy 4: inline SPA bootstrap (Instagram / TikTok / Next.js) ---
    profile = _from_shared_data(body, url, cap)
    if profile and (profile.main_caption or profile.recent_posts):
        return profile

    # --- final fallback: profile is empty but we can still report the URL ---
    if url:
        handle = _guess_handle(url)
        if handle:
            return ProfileExcerpt(handle=handle, source="url")
    return ProfileExcerpt()


# -------- strategy implementations --------


def _from_oembed(oembed: dict[str, Any], url: str) -> ProfileExcerpt | None:
    if not isinstance(oembed, dict):
        return None
    # Order matters: title is usually the page/video title, author_name is
    # the channel. For a profile page they swap, but title-first reads
    # better for the common case (a single video on a channel page).
    title = oembed.get("title") or oembed.get("author_name") or ""
    desc = oembed.get("author_description") or oembed.get("description") or ""
    main = desc or title
    if not main:
        return None
    return ProfileExcerpt(
        main_caption=str(main).strip(),
        handle=str(oembed.get("author_url") or _guess_handle(url)).strip(),
        display_name=str(title).strip(),
        avatar=str(oembed.get("thumbnail_url") or "").strip(),
        recent_posts=[],
        source="oembed",
        raw_data={"oembed": oembed},
    )


def _from_jsonld(
    blocks: list[dict[str, Any]], url: str, cap: int
) -> ProfileExcerpt | None:
    if not blocks:
        return None
    # A "profile" page typically has a Person / Organization as the root
    # entity, with Article / SocialMediaPosting / VideoObject in the graph
    # or referenced via mainEntity.
    profile = ProfileExcerpt(source="jsonld", raw_data={"jsonld_blocks": blocks})
    for block in blocks:
        t = block.get("@type")
        if isinstance(t, list):
            t = next((x for x in t if isinstance(x, str)), "")
        if t in {"Person", "Organization"}:
            profile.main_caption = (
                block.get("description")
                or block.get("bio")
                or _node_text(block)
            )
            profile.display_name = block.get("name") or ""
            profile.handle = block.get("alternateName") or block.get("identifier") or _guess_handle(url)
            img = block.get("image")
            if isinstance(img, str):
                profile.avatar = img
            elif isinstance(img, dict):
                profile.avatar = img.get("url") or ""
            elif isinstance(img, list) and img:
                first = img[0]
                profile.avatar = first.get("url") if isinstance(first, dict) else str(first)

        # The page may list recent posts as SocialMediaPosting / Article /
        # VideoObject. The page itself is usually one of these, so we add
        # it to recent_posts as a single entry even when no other items are
        # present.
        elif t in {"SocialMediaPosting", "Article", "NewsArticle", "BlogPosting", "VideoObject", "Short"}:
            caption = _node_text(block)
            if caption and len(profile.recent_posts) < cap:
                profile.recent_posts.append(
                    PostExcerpt(
                        kind=_kind_from_type(t),
                        caption=caption,
                        url=_node_url(block) or url,
                        posted_at=_node_date(block),
                    )
                )

        # The page may have a hasPart / mainEntity pointing at a list
        for key in ("hasPart", "mainEntity", "subjectOf", "workExample"):
            sub = block.get(key)
            if isinstance(sub, list):
                for s in sub:
                    if isinstance(s, dict):
                        caption = _node_text(s)
                        if caption and len(profile.recent_posts) < cap:
                            profile.recent_posts.append(
                                PostExcerpt(
                                    kind=_kind_from_jsonld(s),
                                    caption=caption,
                                    url=_node_url(s),
                                    posted_at=_node_date(s),
                                )
                            )
            elif isinstance(sub, dict):
                caption = _node_text(sub)
                if caption and len(profile.recent_posts) < cap:
                    profile.recent_posts.append(
                        PostExcerpt(
                            kind=_kind_from_jsonld(sub),
                            caption=caption,
                            url=_node_url(sub),
                            posted_at=_node_date(sub),
                        )
                    )

    if not profile.main_caption and not profile.recent_posts and not profile.display_name:
        return None
    if not profile.handle:
        profile.handle = _guess_handle(url)
    return profile


def _from_meta_tags(
    meta: dict[str, str], url: str, cap: int, body: bytes
) -> ProfileExcerpt | None:
    if not meta:
        return None
    main = meta.get("og:description") or meta.get("twitter:description") or meta.get("description") or ""
    title = meta.get("og:title") or meta.get("twitter:title") or ""
    if not main and not title:
        return None
    avatar = meta.get("og:image") or ""
    return ProfileExcerpt(
        main_caption=main.strip(),
        handle="",  # filled by the fallback in extract_profile() if no other strategy claimed it
        display_name=title.strip(),
        avatar=avatar,
        recent_posts=[],
        source="meta",
        raw_data={"meta_tags": meta},
    )


def _from_shared_data(body: bytes, url: str, cap: int) -> ProfileExcerpt | None:
    """Recover profile + recent posts from inline SPA bootstraps.

    Handles Instagram ``window._sharedData``, Next.js ``__NEXT_DATA__``,
    TikTok / IG ``__UNIVERSAL_DATA__``, and similar inline JSON blobs.
    """
    # Instagram classic sharedData
    m = _INSTAGRAM_SHARED.search(body)
    if m:
        try:
            data = json.loads(m.group(1))
        except (ValueError, TypeError):
            data = None
        if data:
            profile = _parse_instagram_shared(data, url, cap)
            if profile:
                return profile

    # Generic __INITIAL_STATE__ / __NEXT_DATA__ / __UNIVERSAL_DATA__
    m = _INITIAL_STATE.search(body)
    if m:
        try:
            data = json.loads(m.group(1))
        except (ValueError, TypeError):
            data = None
        if data:
            profile = _parse_universal_state(data, url, cap)
            if profile:
                return profile
    return None


def _parse_instagram_shared(
    data: dict[str, Any], url: str, cap: int
) -> ProfileExcerpt | None:
    try:
        user = (
            data.get("entry_data", {})
            .get("ProfilePage", [{}])[0]
            .get("graphql", {})
            .get("user", {})
        )
    except (AttributeError, IndexError, KeyError, TypeError):
        return None
    if not user:
        return None
    profile = ProfileExcerpt(
        main_caption=user.get("biography", "").strip(),
        handle=user.get("username", "").strip() or _guess_handle(url),
        display_name=user.get("full_name", "").strip(),
        avatar=user.get("profile_pic_url", "").strip(),
        source="shared_data",
        raw_data={"shared_data": data},
    )
    edges = (
        user.get("edge_owner_to_timeline_media", {}).get("edges", [])
        if isinstance(user.get("edge_owner_to_timeline_media"), dict)
        else []
    )
    for edge in edges[:cap]:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        caption = ""
        if caption_edges:
            caption = caption_edges[0].get("node", {}).get("text", "")
        kind = "reel" if node.get("product_type") == "clips" else (
            "video" if node.get("is_video") else "post"
        )
        shortcode = node.get("shortcode", "")
        profile.recent_posts.append(
            PostExcerpt(
                kind=kind,
                caption=(caption or "").strip(),
                url=f"https://www.instagram.com/p/{shortcode}/" if shortcode else "",
                posted_at=str(node.get("taken_at_timestamp", "")),
            )
        )
    return profile


def _parse_universal_state(
    data: dict[str, Any], url: str, cap: int
) -> ProfileExcerpt | None:
    """Heuristic for Next.js / Nuxt / TikTok universal state blobs.

    We don't know the exact shape, so we walk the JSON looking for objects
    that look like a profile (``biography`` / ``description`` / ``bio``)
    or a list of posts. Cheap and best-effort.
    """
    profile: ProfileExcerpt | None = None

    def visit(node: Any) -> None:
        nonlocal profile
        if profile is not None and len(profile.recent_posts) >= cap:
            return
        if isinstance(node, dict):
            # Profile shape
            if profile is None and any(
                k in node for k in ("biography", "bio", "description")
            ) and any(k in node for k in ("username", "handle", "author")):
                profile = ProfileExcerpt(
                    main_caption=str(
                        node.get("biography") or node.get("bio") or node.get("description") or ""
                    ).strip(),
                    handle=str(
                        node.get("username") or node.get("handle") or _guess_handle(url)
                    ).strip(),
                    display_name=str(node.get("full_name") or node.get("name") or "").strip(),
                    avatar=str(
                        node.get("profile_pic_url")
                        or node.get("avatar")
                        or node.get("avatarUrl")
                        or ""
                    ).strip(),
                    source="universal_state",
                    raw_data={"universal_state": data},
                )
            # Post shape
            for key in ("posts", "items", "videos", "tweets", "statuses", "entries"):
                arr = node.get(key)
                if isinstance(arr, list) and arr:
                    for item in arr:
                        if profile is None:
                            profile = ProfileExcerpt(source="universal_state")
                        if not isinstance(item, dict):
                            continue
                        caption = (
                            item.get("caption")
                            or item.get("text")
                            or item.get("description")
                            or item.get("body")
                            or ""
                        )
                        if not caption:
                            continue
                        if len(profile.recent_posts) >= cap:
                            return
                        profile.recent_posts.append(
                            PostExcerpt(
                                kind=str(item.get("type") or item.get("kind") or "post"),
                                caption=str(caption).strip(),
                                url=str(item.get("url") or item.get("link") or ""),
                                posted_at=str(
                                    item.get("created_at")
                                    or item.get("createdAt")
                                    or item.get("publishedAt")
                                    or ""
                                ),
                            )
                        )
            for v in node.values():
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)

    visit(data)
    if profile and not profile.handle:
        profile.handle = _guess_handle(url)
    return profile


# -------- small utilities --------


def _kind_from_type(t: str) -> str:
    t = (t or "").lower()
    if t in {"short", "reel", "videobject"}:
        return "reel" if t in {"short", "reel"} else "video"
    if t in {"article", "newsarticle", "blogposting"}:
        return "article"
    if t in {"socialmediaposting"}:
        return "post"
    return "post"


def _kind_from_jsonld(node: dict[str, Any]) -> str:
    t = node.get("@type")
    if isinstance(t, list):
        t = next((x for x in t if isinstance(x, str)), "")
    return _kind_from_type(str(t))


_HANDLE_PATTERNS = (
    re.compile(r"https?://(?:www\.)?instagram\.com/([^/?#]+)/?", re.I),
    re.compile(r"https?://(?:www\.)?tiktok\.com/@([^/?#]+)/?", re.I),
    re.compile(r"https?://(?:www\.)?youtube\.com/(?:@([^/?#]+)|channel/([^/?#]+))/?", re.I),
    re.compile(r"https?://(?:www\.)?twitter\.com/([^/?#]+)/?", re.I),
    re.compile(r"https?://(?:www\.)?x\.com/([^/?#]+)/?", re.I),
)


def _guess_handle(url: str) -> str:
    if not url:
        return ""
    for pat in _HANDLE_PATTERNS:
        m = pat.match(url)
        if m:
            for group in m.groups():
                if group:
                    return group.lstrip("@")
    return ""
