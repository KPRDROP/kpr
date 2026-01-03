#!/usr/bin/env python3

import asyncio
import re
import sys
from urllib.parse import quote_plus
from playwright.async_api import async_playwright

BASE = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"


def log(msg):
    print(msg)
    sys.stdout.flush()


def clean_title(text):
    text = re.sub(r"\s+", " ", text)
    text = text.replace("@", "vs").replace(",", "")
    return text.strip() or "NFL Game"


# --------------------------------------------------
# CLOUDflare SAFE HOMEPAGE LOADER
# --------------------------------------------------

async def load_homepage_and_get_events(pw):
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )

    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 800},
    )

    page = await context.new_page()

    log("🌐 Loading NFLWebcast homepage (Cloudflare JS challenge)…")
    await page.goto(BASE, wait_until="load", timeout=90000)

    # 🔑 WAIT UNTIL CLOUDFLARE SETS CLEARANCE COOKIE
    for _ in range(60):
        cookies = await context.cookies()
        if any(c["name"] == "cf_clearance" for c in cookies):
            log("✅ Cloudflare clearance obtained")
            break
        await asyncio.sleep(1)
    else:
        log("❌ Cloudflare clearance NOT obtained")
        await browser.close()
        return []

    # 🔁 RELOAD REAL PAGE AFTER CLEARANCE
    await page.goto(BASE, wait_until="networkidle", timeout=60000)

    # NOW the Watch buttons exist
    await page.wait_for_selector(
        'a[href*="live-stream-online-free"]',
        timeout=30000
    )

    links = await page.eval_on_selector_all(
        'a[href*="live-stream-online-free"]',
        "els => [...new Set(els.map(e => e.href))]"
    )

    await browser.close()
    log(f"🔍 Found {len(links)} event pages")
    return links


# --------------------------------------------------
# CAPTURE STREAM
# --------------------------------------------------

async def capture_m3u8(pw, url):
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    m3u8 = None

    def on_resp(resp):
        nonlocal m3u8
        if ".m3u8" in resp.url and not m3u8:
            m3u8 = resp.url

    page.on("response", on_resp)

    log(f"🎯 Opening event page: {url}")
    await page.goto(url, wait_until="networkidle", timeout=60000)

    for _ in range(20):
        if m3u8:
            break
        await asyncio.sleep(0.5)

    title = clean_title(await page.title())
    await browser.close()

    return m3u8, title


# --------------------------------------------------
# PLAYLIST OUTPUT
# --------------------------------------------------

def write_playlists(entries):
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-logo="{LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"{url}\n\n")

    ua = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{url}|user-agent={ua}\n")

    log("✅ Playlists written")


# --------------------------------------------------
# MAIN
# --------------------------------------------------

async def main():
    log("🚀 Starting NFL Webcast scraper (REAL FIX)")

    async with async_playwright() as pw:
        events = await load_homepage_and_get_events(pw)

        if not events:
            log("❌ No event pages found")
            return

        results = []

        for url in events:
            m3u8, title = await capture_m3u8(pw, url)
            if m3u8:
                log(f"✅ Stream found: {title}")
                results.append((title, m3u8))
            else:
                log("⚠️ Stream not found")

        if results:
            write_playlists(results)
        else:
            log("❌ No streams captured")


if __name__ == "__main__":
    asyncio.run(main())
