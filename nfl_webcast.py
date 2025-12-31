#!/usr/bin/env python3
import asyncio
import re
import urllib.parse
from playwright.async_api import async_playwright

BASE_URL = "https://nflwebcast.com/"
EVENT_RE = re.compile(r"https://nflwebcast\.com/.+-live-stream-online-free/?$")
M3U8_RE = re.compile(r"\.m3u8(\?|$)")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

async def wait_for_real_page(page):
    """Wait until Cloudflare challenge is gone"""
    for _ in range(20):
        html = await page.content()
        if "Just a moment" not in html:
            return True
        await page.wait_for_timeout(1000)
    return False

async def scrape():
    print("🚀 Starting NFL Webcast scraper (FINAL FIX)...")

    event_links = set()
    streams = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        context = await browser.new_context(user_agent=UA)
        page = await context.new_page()

        print("🌐 Loading NFLWebcast homepage (Cloudflare protected)...")
        await page.goto(BASE_URL, timeout=60000)

        if not await wait_for_real_page(page):
            print("❌ Cloudflare not cleared")
            return

        # scroll to force lazy content
        for _ in range(3):
            await page.mouse.wheel(0, 3000)
            await page.wait_for_timeout(1500)

        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href)"
        )

        print(f"🔎 Found {len(links)} total links on page")

        for link in links:
            if EVENT_RE.match(link):
                event_links.add(link)

        if not event_links:
            print("❌ No event pages found")
            return

        print(f"🏈 Found {len(event_links)} event pages")

        async def on_response(resp):
            url = resp.url
            if M3U8_RE.search(url):
                streams.append(url)

        page.on("response", on_response)

        for event in sorted(event_links):
            print(f"▶ Opening event: {event}")
            await page.goto(event, timeout=60000)
            await wait_for_real_page(page)

            # try auto-play
            try:
                await page.click("text=Play", timeout=3000)
            except:
                pass

            await page.wait_for_timeout(6000)

        await browser.close()

    if not streams:
        print("❌ No streams captured")
        return

    streams = list(dict.fromkeys(streams))  # unique

    print(f"🎯 Captured {len(streams)} m3u8 streams")

    # ───── EXPORT PLAYLISTS ─────
    ua_encoded = urllib.parse.quote(UA)

    with open("NFLWebcast_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, url in enumerate(streams, 1):
            f.write(f"#EXTINF:-1,NFL Stream {i}\n{url}\n")

    with open("NFLWebcast_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, url in enumerate(streams, 1):
            f.write(
                f"#EXTINF:-1,NFL Stream {i}\n"
                f"{url}|User-Agent={ua_encoded}\n"
            )

    print("📁 Exported:")
    print(" - NFLWebcast_VLC.m3u8")
    print(" - NFLWebcast_TiviMate.m3u8")
    print("🎉 Done.")

if __name__ == "__main__":
    asyncio.run(scrape())
