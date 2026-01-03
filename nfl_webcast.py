#!/usr/bin/env python3
import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"
)

VLC_LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"


def log(*a):
    print(*a)
    sys.stdout.flush()


# ----------------------------
# EVENT REGEX (CRITICAL FIX)
# ----------------------------
EVENT_RE = re.compile(
    r"^https://nflwebcast\.com/[a-z0-9-]+-live-stream-online-free/?$",
    re.I
)


def title_from_url(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    slug = slug.replace("-live-stream-online-free", "")
    parts = slug.split("-")

    if len(parts) >= 2:
        mid = len(parts) // 2
        return f"{parts[0].title()} vs {parts[-1].title()}"

    return slug.replace("-", " ").title()


# ----------------------------
# PLAYWRIGHT SCRAPER
# ----------------------------
async def extract_event_pages(playwright):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    log("🌐 Loading NFLWebcast homepage (Cloudflare bypass)…")
    await page.goto(BASE, wait_until="networkidle", timeout=60000)
    await asyncio.sleep(4)

    links = await page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => e.href)"
    )

    await browser.close()

    events = []
    for href in links:
        if EVENT_RE.match(href):
            events.append(href)

    events = sorted(set(events))
    log(f"🔍 Found {len(events)} event pages")
    return events


async def capture_m3u8(playwright, url):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    m3u8_url = None

    def on_response(resp):
        nonlocal m3u8_url
        if ".m3u8" in resp.url and not m3u8_url:
            m3u8_url = resp.url

    page.on("response", on_response)

    await page.goto(url, wait_until="networkidle", timeout=60000)
    await asyncio.sleep(6)

    await browser.close()
    return m3u8_url


# ----------------------------
# PLAYLIST WRITER
# ----------------------------
def write_playlists(entries):
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-logo="{VLC_LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{url}\n\n")

    ua_enc = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{url}|user-agent={ua_enc}\n")

    log("✅ Playlists written")


# ----------------------------
# MAIN
# ----------------------------
async def main():
    log("🚀 Starting NFL Webcast scraper (REAL FIX)")

    async with async_playwright() as p:
        events = await extract_event_pages(p)

        if not events:
            log("❌ No event pages found")
            return

        results = []
        for url in events:
            log(f"▶ Capturing stream: {url}")
            m3u8 = await capture_m3u8(p, url)
            if m3u8:
                title = title_from_url(url)
                log(f"✅ Found m3u8: {m3u8}")
                results.append((title, m3u8))
            else:
                log("⚠️ No m3u8 found")

    if not results:
        log("❌ No streams captured")
        return

    write_playlists(results)
    log("🎉 Done")


if __name__ == "__main__":
    asyncio.run(main())
