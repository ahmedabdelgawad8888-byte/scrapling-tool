#!/usr/bin/env python3
"""Shared UI kit for the Scrapling Tool dashboard.

Three things every tab needs: a light/dark theme, a way to look at results
(table / cards / list), and export to any format. Keeping them here means the
Scrape, Discover, Lookalike and Posts tabs all behave identically instead of
each growing its own slightly different download row.
"""

from __future__ import annotations

import html
import io
import json
from typing import Any, Callable

import pandas as pd
import streamlit as st

__all__ = [
    "current_theme",
    "theme_toggle",
    "inject_theme",
    "render_results",
    "export_bar",
    "urls_from_upload",
    "fmt_count",
]

_THEME_KEY = "_ui_theme"

_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0a0910",
        "surface": "#15131f",
        "surface_alt": "#1d1a29",
        "border": "#2b2738",
        "text": "#f7f5fb",
        "muted": "#a29ab8",
        "primary": "#8b5cf6",
        "ok": "#10b981",
        "err": "#ef4444",
        "shadow": "0 1px 3px rgba(0,0,0,.45)",
    },
    "light": {
        "bg": "#f6f5fa",
        "surface": "#ffffff",
        "surface_alt": "#f0edf7",
        "border": "#dcd6ea",
        "text": "#17131f",
        "muted": "#5d5674",
        "primary": "#7c3aed",
        "ok": "#059669",
        "err": "#dc2626",
        "shadow": "0 1px 3px rgba(23,19,31,.10)",
    },
}


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
def current_theme() -> str:
    """Return the active theme name ('dark' or 'light')."""
    return st.session_state.get(_THEME_KEY, "dark")


def _sync_streamlit_base(theme: str) -> None:
    """Push the choice into Streamlit's own theme.

    Widgets drawn on canvas -- most importantly the ``st.dataframe`` grid --
    ignore injected CSS and follow Streamlit's configured base theme. Setting
    it at runtime keeps the grid from staying dark inside a light page. This
    touches a private config API, so treat failure as non-fatal: the CSS below
    still themes everything we render ourselves.
    """
    try:
        from streamlit import config as _st_config

        if _st_config.get_option("theme.base") != theme:
            _st_config.set_option("theme.base", theme)
    except Exception:
        pass


def theme_toggle(label: str = "Appearance") -> str:
    """Render the light/dark switch. Returns the selected theme."""
    options = ["dark", "light"]
    icons = {"dark": "🌙 Dark", "light": "☀️ Light"}
    choice = st.radio(
        label,
        options=options,
        index=options.index(current_theme()),
        format_func=lambda v: icons[v],
        horizontal=True,
        key="_ui_theme_radio",
    )
    if choice != current_theme():
        st.session_state[_THEME_KEY] = choice
        _sync_streamlit_base(choice)
        st.rerun()
    st.session_state[_THEME_KEY] = choice
    _sync_streamlit_base(choice)
    return choice


def inject_theme() -> None:
    """Emit the CSS for the active theme. Call once, right after page config."""
    p = _PALETTES[current_theme()]
    st.markdown(
        f"""
<style>
  :root {{
    --sc-bg: {p["bg"]};
    --sc-surface: {p["surface"]};
    --sc-surface-alt: {p["surface_alt"]};
    --sc-border: {p["border"]};
    --sc-text: {p["text"]};
    --sc-muted: {p["muted"]};
    --sc-primary: {p["primary"]};
    --sc-ok: {p["ok"]};
    --sc-err: {p["err"]};
  }}

  .stApp {{ background: var(--sc-bg); }}
  [data-testid="stHeader"] {{ background: transparent; }}
  .main .block-container {{
    padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1400px;
  }}

  /* Text */
  .stApp, .stApp p, .stApp li, .stApp label,
  .stApp [data-testid="stMarkdownContainer"] {{ color: var(--sc-text); }}
  .stApp h1, .stApp h2, .stApp h3, .stApp h4 {{ color: var(--sc-text); }}
  .stApp [data-testid="stCaptionContainer"] {{ color: var(--sc-muted); }}

  /* Sidebar */
  section[data-testid="stSidebar"] {{
    background: var(--sc-surface); border-right: 1px solid var(--sc-border);
  }}

  /* Inputs */
  .stTextInput input, .stTextArea textarea, .stNumberInput input {{
    background: var(--sc-surface-alt) !important;
    color: var(--sc-text) !important;
    border-color: var(--sc-border) !important;
  }}
  [data-baseweb="select"] > div {{
    background: var(--sc-surface-alt) !important;
    border-color: var(--sc-border) !important;
  }}

  /* Metrics */
  [data-testid="stMetric"] {{
    background: var(--sc-surface);
    border: 1px solid var(--sc-border);
    border-radius: 12px; padding: 14px;
  }}
  [data-testid="stMetricValue"] {{ color: var(--sc-text); }}
  [data-testid="stMetricLabel"] {{ color: var(--sc-muted); }}

  /* Tabs */
  .stTabs [data-baseweb="tab"] {{ color: var(--sc-muted); }}
  .stTabs [aria-selected="true"] {{ color: var(--sc-primary) !important; }}

  [data-testid="stDataFrame"] {{
    border: 1px solid var(--sc-border); border-radius: 12px;
  }}

  .badge-ok {{ color: var(--sc-ok); font-weight: 600; }}
  .badge-err {{ color: var(--sc-err); font-weight: 600; }}

  /* ---- Cards ---- */
  .sc-grid {{
    display: grid; gap: 14px;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    margin-top: .5rem;
  }}
  .sc-card {{
    background: var(--sc-surface);
    border: 1px solid var(--sc-border);
    border-radius: 14px; padding: 14px;
    box-shadow: {p["shadow"]};
    display: flex; flex-direction: column; gap: 10px;
  }}
  .sc-card-head {{ display: flex; align-items: center; gap: 10px; }}
  .sc-avatar {{
    width: 44px; height: 44px; border-radius: 50%; flex: 0 0 44px;
    background-color: var(--sc-primary);
    background-size: cover; background-position: center;
    color: #fff; font-weight: 700; font-size: 18px;
    display: flex; align-items: center; justify-content: center;
  }}
  .sc-name {{ font-weight: 650; color: var(--sc-text); line-height: 1.2; }}
  .sc-handle {{ color: var(--sc-muted); font-size: .84rem; }}
  .sc-chip {{
    display: inline-block; padding: 1px 8px; border-radius: 999px;
    background: var(--sc-surface-alt); border: 1px solid var(--sc-border);
    color: var(--sc-muted); font-size: .72rem; margin-left: auto;
    text-transform: capitalize; white-space: nowrap;
  }}
  .sc-stats {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .sc-stat {{
    background: var(--sc-surface-alt); border: 1px solid var(--sc-border);
    border-radius: 8px; padding: 4px 8px; font-size: .76rem;
    color: var(--sc-text);
  }}
  .sc-stat b {{ color: var(--sc-muted); font-weight: 500; }}
  .sc-bio {{
    color: var(--sc-muted); font-size: .82rem; line-height: 1.4;
    max-height: 3.5em; overflow: hidden;
  }}
  .sc-card a, .sc-row a {{ color: var(--sc-primary); font-size: .8rem; }}

  /* ---- List ---- */
  .sc-row {{
    display: flex; align-items: center; gap: 12px;
    background: var(--sc-surface); border: 1px solid var(--sc-border);
    border-radius: 10px; padding: 9px 12px; margin-bottom: 7px;
  }}
  .sc-row .sc-avatar {{ width: 32px; height: 32px; flex: 0 0 32px; font-size: 14px; }}
  .sc-row-main {{ min-width: 0; flex: 1; }}
  .sc-row-sub {{
    color: var(--sc-muted); font-size: .78rem;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
</style>
""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_count(val: Any) -> str:
    """1500000 -> '1.5M'. Mirrors the dashboard's table formatting."""
    try:
        n = int(float(str(val).replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return str(val or "")
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _first(rec: dict, *keys: str, default: str = "") -> Any:
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _avatar(rec: dict) -> str:
    """Avatar bubble: image when we have one, initial as the built-in fallback.

    The initial sits behind the background-image, so a hotlink-blocked or dead
    avatar URL degrades to the letter instead of a broken-image icon.
    """
    url = str(_first(rec, "avatar_url", "profile_pic_hd", "profile_pic"))
    name = str(_first(rec, "username", "full_name", "title", default="?"))
    initial = html.escape(name[:1].upper() or "?")
    style = f' style="background-image:url(&quot;{html.escape(url, quote=True)}&quot;)"' if url else ""
    return f'<div class="sc-avatar"{style}>{initial}</div>'


def _stat_chips(rec: dict) -> str:
    pairs = (
        ("Followers", _first(rec, "followers")),
        ("Following", _first(rec, "following")),
        ("Likes", _first(rec, "likes")),
        ("Avg views", _first(rec, "avg_views")),
    )
    chips = [
        f'<span class="sc-stat"><b>{lbl}</b> {html.escape(fmt_count(val))}</span>'
        for lbl, val in pairs
        if val not in (None, "", 0, "0")
    ]
    return f'<div class="sc-stats">{"".join(chips)}</div>' if chips else ""


def _title_of(rec: dict) -> str:
    name = str(_first(rec, "full_name", "title", "username", default="(untitled)"))
    if rec.get("is_verified"):
        name += " ✓"
    if rec.get("is_private"):
        name += " 🔒"
    return name


def _card_html(rec: dict) -> str:
    url = str(_first(rec, "profile_url", "url"))
    handle = str(_first(rec, "username"))
    platform = str(_first(rec, "platform"))
    bio = str(_first(rec, "biography", "bio", "description"))
    emails = rec.get("emails") or []

    parts = [
        '<div class="sc-card">',
        '<div class="sc-card-head">',
        _avatar(rec),
        "<div>",
        f'<div class="sc-name">{html.escape(_title_of(rec))}</div>',
    ]
    if handle:
        parts.append(f'<div class="sc-handle">@{html.escape(handle)}</div>')
    parts.append("</div>")
    if platform:
        parts.append(f'<span class="sc-chip">{html.escape(platform)}</span>')
    parts.append("</div>")

    parts.append(_stat_chips(rec))
    if bio:
        parts.append(f'<div class="sc-bio">{html.escape(bio[:180])}</div>')
    if emails:
        joined = ", ".join(str(e) for e in emails[:2])
        parts.append(f'<div class="sc-handle">✉️ {html.escape(joined)}</div>')
    if rec.get("error"):
        parts.append(f'<div class="badge-err">⚠ {html.escape(str(rec["error"])[:90])}</div>')
    if url:
        safe = html.escape(url, quote=True)
        parts.append(f'<a href="{safe}" target="_blank" rel="noopener">Open profile ↗</a>')
    parts.append("</div>")
    return "".join(parts)


def _row_html(rec: dict) -> str:
    url = str(_first(rec, "profile_url", "url"))
    platform = str(_first(rec, "platform"))
    followers = _first(rec, "followers")
    bits = [b for b in (
        f"@{_first(rec, 'username')}" if _first(rec, "username") else "",
        f"{fmt_count(followers)} followers" if followers else "",
        str(_first(rec, "biography", "bio"))[:80],
    ) if b]

    parts = [
        '<div class="sc-row">',
        _avatar(rec),
        '<div class="sc-row-main">',
        f'<div class="sc-name">{html.escape(_title_of(rec))}</div>',
        f'<div class="sc-row-sub">{html.escape(" · ".join(bits))}</div>',
        "</div>",
    ]
    if platform:
        parts.append(f'<span class="sc-chip">{html.escape(platform)}</span>')
    if url:
        parts.append(f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">↗</a>')
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def _excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Results")
    return buf.getvalue()


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    # Everything is stringified first: scraped rows mix ints, None and lists in
    # one column, which Arrow refuses to infer a type for.
    df.astype(str).to_parquet(buf, index=False)
    return buf.getvalue()


def _jsonl(records: list[dict]) -> bytes:
    return "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in records).encode("utf-8")


def _txt(df: pd.DataFrame) -> bytes:
    return df.to_string(index=False).encode("utf-8")


def _html_table(df: pd.DataFrame) -> bytes:
    doc = (
        "<!doctype html><meta charset='utf-8'><title>Scrapling results</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ddd;padding:6px 9px;text-align:left;font-size:14px}"
        "th{background:#f3f0fa}tr:nth-child(even){background:#fafafa}</style>"
        + df.to_html(index=False, escape=True)
    )
    return doc.encode("utf-8")


# label -> (extension, mime, builder(df, records) -> bytes)
_EXPORTERS: dict[str, tuple[str, str, Callable[[pd.DataFrame, list], bytes]]] = {
    "CSV": ("csv", "text/csv", lambda df, rec: df.to_csv(index=False).encode("utf-8")),
    "JSON": ("json", "application/json",
             lambda df, rec: json.dumps(rec, ensure_ascii=False, indent=2, default=str).encode("utf-8")),
    "JSON Lines": ("jsonl", "application/x-ndjson", lambda df, rec: _jsonl(rec)),
    "Excel": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              lambda df, rec: _excel_bytes(df)),
    "Markdown": ("md", "text/markdown", lambda df, rec: df.to_markdown(index=False).encode("utf-8")),
    "HTML": ("html", "text/html", lambda df, rec: _html_table(df)),
    "Parquet": ("parquet", "application/octet-stream", lambda df, rec: _parquet_bytes(df)),
    "Text": ("txt", "text/plain", lambda df, rec: _txt(df)),
}


def export_bar(df: pd.DataFrame, records: list[dict], basename: str, key: str) -> None:
    """Format picker + download button covering every supported format."""
    if df is None or df.empty:
        return
    col_fmt, col_btn = st.columns([2, 1])
    with col_fmt:
        choice = st.selectbox(
            "Export format", list(_EXPORTERS), key=f"{key}_fmt",
            help="Excel and Markdown need openpyxl / tabulate; both ship in requirements.txt.",
        )
    ext, mime, build = _EXPORTERS[choice]
    try:
        payload = build(df, records)
    except ImportError as exc:
        st.warning(f"{choice} export unavailable: {exc}")
        return
    except Exception as exc:  # a malformed row shouldn't kill the whole tab
        st.warning(f"Could not build {choice} export: {exc}")
        return
    with col_btn:
        st.write("")  # nudge the button down to align with the selectbox
        st.download_button(
            f"📥 Download .{ext}",
            data=payload,
            file_name=f"{basename}.{ext}",
            mime=mime,
            use_container_width=True,
            key=f"{key}_dl",
        )


# ---------------------------------------------------------------------------
# Result views
# ---------------------------------------------------------------------------
def render_results(
    df: pd.DataFrame,
    records: list[dict],
    key: str,
    basename: str = "results",
    title: str = "Results",
) -> None:
    """Show results as a table, card grid or compact list, plus exports."""
    st.markdown(f"### {title}")
    if df is None or df.empty:
        st.info("No results to show.")
        return

    view = st.radio(
        "View",
        options=["Table", "Cards", "List"],
        horizontal=True,
        label_visibility="collapsed",
        key=f"{key}_view",
    )

    if view == "Table":
        st.dataframe(df, use_container_width=True, hide_index=True)
    elif view == "Cards":
        cards = "".join(_card_html(r) for r in records)
        st.markdown(f'<div class="sc-grid">{cards}</div>', unsafe_allow_html=True)
    else:
        rows = "".join(_row_html(r) for r in records)
        st.markdown(rows, unsafe_allow_html=True)

    st.divider()
    export_bar(df, records, basename, key)


# ---------------------------------------------------------------------------
# Upload & discover
# ---------------------------------------------------------------------------
def urls_from_upload(uploaded: Any) -> list[str]:
    """Pull URLs out of an uploaded .txt/.csv/.json file.

    Accepts a bare URL per line, any CSV column that looks like a link, or a
    JSON array of strings/objects -- callers shouldn't have to care which.
    """
    if uploaded is None:
        return []
    name = (getattr(uploaded, "name", "") or "").lower()
    try:
        raw = uploaded.getvalue()
    except Exception:
        raw = uploaded.read()
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

    found: list[str] = []
    if name.endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = []
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, str):
                found.append(item)
            elif isinstance(item, dict):
                val = _first(item, "url", "profile_url", "link", "href")
                if val:
                    found.append(str(val))
    elif name.endswith(".csv"):
        try:
            frame = pd.read_csv(io.StringIO(text))
        except Exception:
            frame = pd.DataFrame()
        for col in frame.columns:
            series = frame[col].astype(str)
            if series.str.startswith(("http://", "https://")).any():
                found.extend(series.tolist())
    else:
        found.extend(text.splitlines())

    seen: set[str] = set()
    urls: list[str] = []
    for candidate in found:
        candidate = str(candidate).strip().strip('",')
        if not candidate.startswith(("http://", "https://")) or candidate in seen:
            continue
        seen.add(candidate)
        urls.append(candidate)
    return urls
