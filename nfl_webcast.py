#!/usr/bin/env python3
import asyncio
import re
import urllib.parse
from playwright.async_api import async_playwright

BASE = "https://nflwebcast.com/"
EVENT_KEYWORD = "live-stream"
M3U8_RE = re.compile(r"\.m3u8(\?|$)")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

async def scrape():
    print("🚀 Starting NFL Webcast scraper (STABLE FIX)...")

    found_events = set()
    streams = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        context = await browser.new_context(user_agent=UA)
        page = await context.new_page()

        def on_response(resp):
            url = resp.url
            if M3U8_RE.search(url):
                streams.add(url)

        page.on("response", on_response)

        print("🌐 Opening homepage…")
        await page.goto(BASE, timeout=60000)

        # ---- WAIT FOR CLOUDFLARE TO CLEAR ----
        for i in range(30):
            await page.wait_for_timeout(1000)
            html = await page.content()
            if "live-stream" in html:
                print(f"✅ Cloudflare cleared after {i+1}s")
                break
        else:
            print("❌ Cloudflare not cleared")
            await browser.close()
            return

        # ---- FORCE LAZY LOAD ----
        for _ in range(4):
            await page.mouse.wheel(0, 4000)
            await page.wait_for_timeout(1200)

        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href)"
        )

        print(f"🔎 Found {len(links)} links")

        for link in links:
            if EVENT_KEYWORD in link and link.startswith(BASE):
                found_events.add(link)

        if not found_events:
            print("❌ No event pages found")
            await browser.close()
            return

        print(f"🏈 Found {len(found_events)} event pages")

        # ---- VISIT EVENTS ----
        for event in sorted(found_events):
            print(f"▶ Loading event: {event}")
            await page.goto(event, timeout=60000)
            await page.wait_for_timeout(8000)

            # click play if exists
            for text in ("Play", "Watch"):
                try:
                    await page.click(f"text={text}", timeout=2000)
                    break
                except:
                    pass

            await page.wait_for_timeout(6000)

        await browser.close()

    if not streams:
        print("❌ No streams captured")
        return

    streams = list(streams)
    print(f"🎯 Captured {len(streams)} m3u8 streams")

    # ---- EXPORT PLAYLISTS ----
    ua_enc = urllib.parse.quote(UA)

    with open("NFLWebcast_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, s in enumerate(streams, 1):
            f.write(f"#EXTINF:-1,NFL Stream {i}\n{s}\n")

    with open("NFLWebcast_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, s in enumerate(streams, 1):
            f.write(
                f"#EXTINF:-1,NFL Stream {i}\n"
                f"{s}|User-Agent={ua_enc}\n"
            )

    print("📁 Exported:")
    print(" - NFLWebcast_VLC.m3u8")
    print(" - NFLWebcast_TiviMate.m3u8")
    print("🎉 Done.")

if __name__ == "__main__":
    asyncio.run(scrape())
