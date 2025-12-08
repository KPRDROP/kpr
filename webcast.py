#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import re
import os
from bs4 import BeautifulSoup
import aiohttp
from playwright.async_api import async_playwright

OUTPUT_FILE = "mls_webcast.m3u"
HOME_URL = "https://mlswebcast.com/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0"
}


# --------------------------------------------------------
#   FETCH HTML PAGE (requests alternative for GitHub)
# --------------------------------------------------------
async def fetch_html(session, url):
    try:
        async with session.get(url, headers=HEADERS, timeout=20) as r:
            return await r.text()
    except Exception as e:
        print(f"⚠ Error fetching {url}: {e}")
        return ""


# --------------------------------------------------------
#   PARSE HOMEPAGE FOR EVENT LINKS
# --------------------------------------------------------
async def extract_event_links():
    print(f"🔍 Fetching homepage: {HOME_URL}")

    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, HOME_URL)
        if not html:
            print("❌ Failed to fetch homepage.")
            return []

    soup = BeautifulSoup(html, "html.parser")

    events = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        # only internal match pages
        if href.startswith("https://mlswebcast.com/") and ("live" in href or "stream" in href):
            title = a.text.strip() or "Live Stream"
            events.append((title, href))

    print(f"📌 Found {len(events)} event page(s) from homepage.")
    return events


# --------------------------------------------------------
#   PLAYWRIGHT NETWORK SCRAPER (PATCHED)
# --------------------------------------------------------
async def capture_m3u8(page, url):
    """
    Navigate to page & capture ANY .m3u8 request.
    No validation — all captured streams accepted.
    """

    found_stream = None

    async def on_request(request):
        nonlocal found_stream
        req_url = request.url

        if ".m3u8" in req_url:
            print(f" ↳ network captured candidate: {req_url}")
            found_stream = req_url

    page.on("request", on_request)

    print(f" ↳ Playwright navigating to {url}")

    try:
        await page.goto(url, timeout=60000, wait_until="networkidle")
        await page.wait_for_timeout(5000)
    except Exception as e:
        print(f"⚠ Navigation failed: {e}")

    return found_stream


# --------------------------------------------------------
#   PARSE TITLE FROM PAGE HTML
# --------------------------------------------------------
async def fetch_event_title(session, url):
    html = await fetch_html(session, url)
    if not html:
        return "Live Stream"

    soup = BeautifulSoup(html, "html.parser")

    # Try OG meta title first
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"]

    # fallback to page <title>
    if soup.title:
        return soup.title.text.strip()

    return "Live Stream"


# --------------------------------------------------------
#   MAIN SCRAPER LOGIC
# --------------------------------------------------------
async def main():
    print("🚀 Starting MLS Webcast scraper (patched)...")

    events = await extract_event_links()

    if not events:
        print("❌ No events found on homepage.")
        return

    results = []

    async with aiohttp.ClientSession() as session:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            for event_name, event_url in events:
                print(f"\n🔎 Processing event: {event_name} -> {event_url}")

                # extract clean event name from HTML metadata
                clean_name = await fetch_event_title(session, event_url)

                m3u8_url = await capture_m3u8(page, event_url)

                if m3u8_url:
                    print(f"✅ Stream found: {m3u8_url}")
                    results.append((clean_name, m3u8_url))
                else:
                    print(f"⚠ No m3u8 found for {event_url}")

            await browser.close()

    # --------------------------------------------------------
    #   WRITE M3U FILE
    # --------------------------------------------------------
    def clean_title(title: str) -> str:
    """
    Remove SEO garbage like:
    | MLS Live Stream Free Online No Sign-up | MLSStreams - MLS WebCast
    """
    if "|" in title:
        title = title.split("|")[0].strip()
    return title


def write_playlists(streams):
    if not streams:
        print("❌ No streams captured, skipping playlist write.")
        return

    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0"

    # ========== VLC FORMAT ==========
    with open("Webcast_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            title = clean_title(s["title"])
            f.write(f"#EXTINF:-1,{title}\n{s['url']}\n")

    # ========== TIVIMATE FORMAT ==========
    with open("Webcast_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            title = clean_title(s["title"])
            headers = (
                f"|referer=https://mlswebcast.com/"
                f"|origin=https://mlswebcast.com"
                f"|user-agent={UA.replace(' ', '%20')}"
            )
            f.write(f"#EXTINF:-1,{title}\n{s['url']}{headers}\n")

    print("✅ Playlist files written:")
    print("   - Webcast_VLC.m3u8")
    print("   - Webcast_TiviMate.m3u8")

# --------------------------------------------------------
#   RUN
# --------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
