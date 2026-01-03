#!/usr/bin/env python3

import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ---------------- CONFIG ----------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)

BASE = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

VLC_LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"

EVENT_RE = re.compile(
    r"^https://nflwebcast\.com/.+-live-stream-",
    re.I
)

# ---------------- HELPERS ----------------

def log(*a):
    print(*a)
    sys.stdout.flush()


def clean_event_title(title: str) -> str:
    if not title:
        return "NFL Game"
    title = title.replace("@", "vs").replace(",", "")
    title = re.sub(r"\s{2,}", " ", title)
    return title.strip()


# ---------------- HOMEPAGE SCRAPER ----------------

async def get_event_links(playwright) -> list[str]:
    log("🌐 Loading NFLWebcast homepage (Cloudflare protected)...")

    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    try:
        await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(6)  # allow CF to clear

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        links = set()

        for a in soup.find_all("a", href=True):
            href = urljoin(BASE, a["href"].strip())
            if EVENT_RE.match(href):
                links.add(href)

        log(f"🔍 Found {len(links)} event pages")
        return sorted(links)

    finally:
        await context.close()
        await browser.close()


# ---------------- M3U8 CAPTURE ----------------

async def capture_m3u8(playwright, url: str):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    found = None

    def on_response(resp):
        nonlocal found
        if ".m3u8" in resp.url and not found:
            found = resp.url

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)

        if not found:
            html = await page.content()
            m = re.search(r'https?://[^\s"\']+\.m3u8[^\s"\']*', html)
            if m:
                found = m.group(0)

    except PlaywrightTimeoutError:
        log(f"⚠️ Timeout loading {url}")

    finally:
        await context.close()
        await browser.close()

    return found


# ---------------- PLAYLIST WRITER ----------------

def write_playlists(entries):
    ua_enc = quote_plus(USER_AGENT)

    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-logo="{VLC_LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{url}\n\n")

    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{url}|user-agent={ua_enc}\n")

    log("✅ Playlists written successfully")


# ---------------- MAIN ----------------

async def main():
    log("🚀 Starting NFL Webcast scraper (STABLE FIX)...")

    async with async_playwright() as p:
        event_links = await get_event_links(p)

        if not event_links:
            log("❌ No event pages found")
            return

        results = []

        for idx, url in enumerate(event_links, 1):
            log(f"🔎 [{idx}/{len(event_links)}] Capturing: {url}")
            m3u8 = await capture_m3u8(p, url)
            if m3u8:
                title = clean_event_title(
                    url.split("/")[-2].replace("-", " ")
                )
                results.append((title, m3u8))
                log(f"✅ Found m3u8")
            else:
                log("⚠️ No m3u8 found")

        if not results:
            log("❌ No streams captured")
            return

        write_playlists(results)
        log("🎉 Done")


if __name__ == "__main__":
    asyncio.run(main())
