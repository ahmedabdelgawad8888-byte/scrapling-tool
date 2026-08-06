#!/usr/bin/env python3
"""
Bulk Scraper — a fast, concurrent web scraping tool built on Scrapling.

Features:
  • Reads URLs from a file or stdin (one per line)
  • Concurrent async HTTP fetching (AsyncFetcher)
  • Optional browser fallback for JS‑heavy sites (StealthyFetcher)
  • Instagram‑aware mode — extracts profile bio, follower count, posts
  • Structured output: Markdown + JSON side‑by‑side
  • Resume: skips already‑fetched URLs
  • Configurable concurrency, delays, retries, proxies
  • Rich live progress bar

Usage:
  python bulk_scraper.py urls.txt
  python bulk_scraper.py urls.txt --instagram --concurrency 5 --output-dir ./pages
  cat urls.txt | python bulk_scraper.py --instagram
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from scrapling.fetchers import AsyncFetcher, StealthyFetcher
from scrapling.parser import Selector


def _safe(val) -> str:
    """Convert any value to a plain string (TextHandler → str)."""
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)

console = Console(stderr=True)

# ── Result model ────────────────────────────────────────────────────────────

@dataclass
class ScrapeResult:
    url: str
    username: str = ""
    status: int = 0
    error: str = ""
    title: str = ""
    description: str = ""
    text_content: str = ""
    links: list[str] = field(default_factory=list)
    # Instagram‑specific
    is_instagram: bool = False
    bio: str = ""
    followers: str = ""
    following: str = ""
    posts_count: str = ""
    full_name: str = ""
    is_verified: bool = False
    profile_pic: str = ""

    def to_dict(self) -> dict:
        return {
            "url": _safe(self.url),
            "username": _safe(self.username),
            "status": self.status,
            "error": _safe(self.error),
            "title": _safe(self.title),
            "description": _safe(self.description),
            "text_length": len(self.text_content),
            "links_count": len(self.links),
            "is_instagram": self.is_instagram,
            "bio": _safe(self.bio),
            "followers": _safe(self.followers),
            "following": _safe(self.following),
            "posts_count": _safe(self.posts_count),
            "full_name": _safe(self.full_name),
            "is_verified": self.is_verified,
            "profile_pic": _safe(self.profile_pic),
        }


# ── Instagram parser ────────────────────────────────────────────────────────

def _parse_instagram(response: Selector, url: str) -> ScrapeResult:
    username = url.rstrip("/").split("/")[-1]
    r = ScrapeResult(url=url, username=username, is_instagram=True, status=200)

    raw = _safe(response.body)

    # ── JSON data embedded in <script type="application/ld+json"> ──
    json_blocks = re.findall(
        r'<script type="application/ld+json">(.*?)</script>', raw, re.DOTALL
    )
    for block in json_blocks:
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                r.full_name = data.get("name") or r.full_name
                r.description = data.get("description") or r.description
                if data.get("url"):
                    r.profile_pic = (
                        data.get("image", {})
                        .get("contentUrl", "")
                        if isinstance(data.get("image"), dict)
                        else ""
                    )
        except json.JSONDecodeError:
            pass

    # ── Meta tags ──
    og_title = _safe(response.css('meta[property="og:title"]::attr(content)').get())
    og_desc = _safe(response.css('meta[property="og:description"]::attr(content)').get())
    r.title = og_title or r.full_name or username
    r.description = og_desc or r.description

    # ── Profile picture from og:image or schema ──
    if not r.profile_pic:
        r.profile_pic = _safe(
            response.css('meta[property="og:image"]::attr(content)').get()
        )

    # ── Stats from meta description like "89 Posts, 2.2M Followers, 997 Following" ──
    if og_desc:
        stats = re.findall(
            r"([\d.,]+[KMBkmb]?)\s*(Posts|Followers|Following)", _safe(og_desc)
        )
        for raw_val, label in stats:
            label_lower = label.lower()
            if "post" in label_lower:
                r.posts_count = _safe(raw_val)
            elif "follower" in label_lower:
                r.followers = _safe(raw_val)
            elif "following" in label_lower:
                r.following = _safe(raw_val)

    # ── Bio from og:description (first line before any stats) ──
    if og_desc:
        lines = og_desc.strip().split("\n")
        non_empty = [line.strip() for line in lines if line.strip()]
        if non_empty:
            r.bio = non_empty[0]

    # ── Grab structured text content (meta description has it) ──
    r.text_content = og_desc or ""

    if not r.bio:
        r.bio = r.description

    return r


# ── Generic parser ──────────────────────────────────────────────────────────

def _parse_generic(response: Selector, url: str) -> ScrapeResult:
    r = ScrapeResult(url=url, status=200)
    r.title = _safe(
        response.css("title::text").get()
        or response.css('meta[property="og:title"]::attr(content)').get()
        or ""
    )
    r.description = _safe(
        response.css('meta[name="description"]::attr(content)').get()
        or response.css('meta[property="og:description"]::attr(content)').get()
        or ""
    )
    txt = response.get_all_text(strip=True)
    r.text_content = _safe(txt)[:50000] if txt else ""

    links = response.css("a::attr(href)").getall()
    r.links = [link for link in (links or []) if link.startswith("http")]
    return r


# ── Core fetcher ────────────────────────────────────────────────────────────

async def fetch_one(
    url: str,
    use_browser: bool = False,
    timeout: int = 30,
    proxy: str = "",
) -> ScrapeResult:
    result = ScrapeResult(url=url)
    result.username = url.rstrip("/").split("/")[-1]

    try:
        if use_browser:
            browser_kwargs: dict = {"headless": True, "network_idle": True, "timeout": timeout * 1000}
            if proxy:
                browser_kwargs["proxy"] = proxy
            resp = await StealthyFetcher.async_fetch(url, **browser_kwargs)
        else:
            http_kwargs: dict = {"timeout": timeout, "follow_redirects": True}
            if proxy:
                http_kwargs["proxy"] = proxy
            resp = await AsyncFetcher.get(url, **http_kwargs)

        result.status = resp.status

        is_ig = "instagram.com" in url.lower()
        if is_ig and resp.status == 200:
            parsed = _parse_instagram(resp, url)
            parsed.status = resp.status
            return parsed

        if resp.status == 200:
            return _parse_generic(resp, url)

        result.error = f"HTTP {resp.status}"
        return result

    except Exception as e:
        result.error = str(e)[:200]
        return result


# ── Batch runner ────────────────────────────────────────────────────────────

async def run_batch(
    urls: list[str],
    concurrency: int = 5,
    use_browser: bool = False,
    timeout: int = 30,
    proxy: str = "",
    output_dir: Path = Path("scraped_pages"),
    resume: bool = True,
) -> list[ScrapeResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    results: list[ScrapeResult] = []

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )
    task = progress.add_task("[cyan]Scraping...", total=len(urls))

    known_fields = {f.name for f in fields(ScrapeResult)}

    already = set()
    if resume:
        for f in output_dir.glob("*.json"):
            already.add(f.stem)

    with Live(progress, refresh_per_second=10):
        async def _worker(url: str):
            async with sem:
                username = url.rstrip("/").split("/")[-1]
                if resume and username in already:
                    progress.advance(task)
                    try:
                        with (output_dir / f"{username}.json").open(
                            encoding="utf-8"
                        ) as fh:
                            prev = json.load(fh)
                        filtered = {k: v for k, v in prev.items() if k in known_fields}
                        r = ScrapeResult(**filtered)
                        results.append(r)
                        return
                    except Exception:
                        pass

                r = await fetch_one(url, use_browser=use_browser, timeout=timeout, proxy=proxy)
                results.append(r)
                _save_result(r, output_dir)
                progress.advance(task)

        tasks = [_worker(url) for url in urls]
        await asyncio.gather(*tasks)

    return results


def _save_result(r: ScrapeResult, output_dir: Path):
    safe = _safe(r.username) or re.sub(r"[^\w.-]", "_", r.url.split("/")[2] if "//" in r.url else r.url.split("/")[0])
    json_path = output_dir / f"{safe}.json"
    md_path = output_dir / f"{safe}.md"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(r.to_dict(), f, ensure_ascii=False, indent=2)

    lines = [f"# {r.title}", ""]
    if r.is_instagram:
        lines.extend([
            f"**Username:** {r.username}",
            f"**Full Name:** {r.full_name}" if r.full_name else "",
            f"**Verified:** {'Yes' if r.is_verified else 'No'}",
            f"**Posts:** {r.posts_count}" if r.posts_count else "",
            f"**Followers:** {r.followers}" if r.followers else "",
            f"**Following:** {r.following}" if r.following else "",
            f"**Bio:** {r.bio}" if r.bio else "",
            f"**Profile Pic:** {r.profile_pic}" if r.profile_pic else "",
        ])
    else:
        lines.append(f"**URL:** {r.url}")
        if r.description:
            lines.append(f"**Description:** {r.description}")
        lines.append(f"**Links:** {len(r.links)}")

    if r.text_content:
        lines.extend(["", "## Content", "", r.text_content[:20000]])

    if r.error:
        lines.extend(["", "## Error", "", r.error])

    lines.append("")
    with md_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(filter(None, lines)))


# ── Summary table ───────────────────────────────────────────────────────────

def print_summary(results: list[ScrapeResult]):
    table = Table(title="Scraping Results")
    table.add_column("URL", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Title", style="green", max_width=50)
    table.add_column("Content", justify="right")
    table.add_column("Error", style="red", max_width=30)

    ok = 0
    for r in results:
        status_str = f"[green]{r.status}[/]" if r.status == 200 else f"[red]{r.status}[/]"
        content_len = len(r.text_content) if not r.error else 0
        table.add_row(
            r.url,
            status_str,
            (r.title or "")[:50],
            str(content_len) if content_len else "-",
            r.error[:30] if r.error else "",
        )
        if r.status == 200 and not r.error:
            ok += 1

    console.print(table)
    console.print(f"\n[bold green]Success: {ok}/{len(results)}[/]")


# ── CLI ─────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Bulk web scraper powered by Scrapling — concurrent, Instagram-aware, resume-friendly.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python bulk_scraper.py urls.txt\n"
            "  python bulk_scraper.py urls.txt --instagram --concurrency 10\n"
            "  cat urls.txt | python bulk_scraper.py --instagram -o ./pages\n"
        ),
    )
    p.add_argument("urls_file", nargs="?", help="File with one URL per line (reads stdin if omitted)")
    p.add_argument("-o", "--output-dir", default="scraped_pages", help="Output directory (default: scraped_pages)")
    p.add_argument("-c", "--concurrency", type=int, default=5, help="Concurrent requests (default: 5)")
    p.add_argument("-t", "--timeout", type=int, default=30, help="Request timeout in seconds (default: 30)")
    p.add_argument("--instagram", action="store_true", help="Enable Instagram-aware parsing mode")
    p.add_argument("--browser", action="store_true", help="Use StealthyFetcher (browser) for all URLs")
    p.add_argument("--proxy", help="Proxy URL (e.g., http://user:pass@host:port)")
    p.add_argument("--no-resume", action="store_true", help="Re-fetch already scraped URLs")
    return p.parse_args()


def main():
    args = _parse_args()
    if args.urls_file:
        path = Path(args.urls_file)
        if not path.exists():
            console.print(f"[red]File not found: {args.urls_file}[/]")
            sys.exit(1)
        raw = path.read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()

    urls = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if not urls:
        console.print("[yellow]No URLs provided.[/]")
        sys.exit(0)

    mode = "instagram" if args.instagram else "browser" if args.browser else "http"
    console.print(
        f"[bold]Scraping {len(urls)} URLs[/] | mode: [cyan]{mode}[/] "
        f"| concurrency: [cyan]{args.concurrency}[/]"
    )

    results = asyncio.run(
        run_batch(
            urls,
            concurrency=args.concurrency,
            use_browser=args.browser,
            timeout=args.timeout,
            proxy=args.proxy or "",
            output_dir=Path(args.output_dir),
            resume=not args.no_resume,
        )
    )

    print_summary(results)


if __name__ == "__main__":
    main()
