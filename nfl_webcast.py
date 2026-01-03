#!/usr/bin/env python3

import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus
from playwright.async_api import async_playwright

BASE = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

VLC_LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"

# ----------------------

def log(msg):
    print(msg)
    sys.stdout.flush()


def clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = text.replace("@", "vs").replace(",", "")
    return text.strip() or "NFL Game"


# ----------------------
# STEP 1: GET EVENT LINKS (REAL FIX)
# ----------------------

async def get_event_links(playwright):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    log("🌐 Loading NFLWebcast homepage (Cloudflare bypass)…")
    await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)

    # 🔥 CRITICAL FIX — WAIT FOR WATCH BUTTONS
    try:
        await page.wait_for_selector(
            'a.btn.btn-info[href*="live-stream"]',
            timeout=30000
        )
    except:
        log("❌ Watch buttons not found (Cloudflare still blocking)")
        await browser.close()
        return []

    links = await page.eval_on_selector_all(
        'a.btn.btn-info[href*="live-stream"]',
        "els => els.map(e => e.href)"
    )

    await browser.close()

    unique = list(dict.fromkeys(links))
    log(f"🔍 Found {len(unique)} event pages")
    return unique


# ----------------------
# STEP 2: CAPTURE M3U8
# ----------------------

async def capture_m3u8(playwright, url):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    m3u8 = None

    def on_response(resp):
        nonlocal m3u8
        if ".m3u8" in resp.url and not m3u8:
            m3u8 = resp.url

    page.on("response", on_response)

    log(f"🎯 Opening event page: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)

    # Try autoplay
    for sel in ["video", "body", ".player", "#player"]:
        try:
            await page.click(sel, timeout=2000, force=True)
        except:
            pass

    # Wait network
    for _ in range(20):
        if m3u8:
            break
        await asyncio.sleep(0.5)

    title = await page.title()
    await browser.close()

    return m3u8, clean_title(title)


# ----------------------
# PLAYLIST OUTPUT
# ----------------------

def write_playlists(entries):
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-logo="{VLC_LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"{url}\n\n")

    ua = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{url}|user-agent={ua}\n")

    log("✅ Playlists written")


# ----------------------
# MAIN
# ----------------------

async def main():
    log("🚀 Starting NFL Webcast scraper (REAL FIX)")

    async with async_playwright() as p:
        event_links = await get_event_links(p)

        if not event_links:
            log("❌ No event pages found")
            return

        results = []

        for url in event_links:
            m3u8, title = await capture_m3u8(p, url)
            if m3u8:
                log(f"✅ Stream found: {title}")
                results.append((title, m3u8))
            else:
                log("⚠️ No stream detected")

        if results:
            write_playlists(results)
        else:
            log("❌ No streams captured")


if __name__ == "__main__":
    asyncio.run(main())
