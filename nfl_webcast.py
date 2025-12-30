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
    "Chrome/142.0.0.0 Safari/537.36"
)

LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"


def log(*args):
    print(*args)
    sys.stdout.flush()


def clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    text = text.replace("@", "vs")
    return text.strip() or "NFL Live"


async def capture_m3u8(page, wait=10):
    found = None

    def on_response(resp):
        nonlocal found
        if ".m3u8" in resp.url and not found:
            found = resp.url

    page.on("response", on_response)
    await asyncio.sleep(wait)
    return found


async def scrape_nfl():
    streams = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        log("🌐 Loading NFLWebcast homepage...")
        await page.goto(BASE, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(5)

        # 🔥 FIX: button selector fallback list
        selectors = [
            "button.watch_btn",
            "a.watch_btn",
            ".watch_btn",
            "button[class*='watch']",
            "a[class*='watch']",
        ]

        buttons = None
        for sel in selectors:
            locator = page.locator(sel)
            if await locator.count() > 0:
                buttons = locator
                break

        if not buttons:
            log("⚠️ No clickable watch buttons found — trying auto-play capture...")
            auto_stream = await capture_m3u8(page, wait=12)
            if auto_stream:
                streams.append(("NFL Live", auto_stream))
            await browser.close()
            return streams

        count = await buttons.count()
        log(f"🔍 Found {count} clickable game entries")

        for i in range(count):
            try:
                log(f"➡️ Opening game {i + 1}/{count}")

                container = buttons.nth(i).locator("xpath=ancestor::tr | ancestor::div")
                title = clean_title((await container.inner_text()).split("\n")[0])

                await buttons.nth(i).click(force=True)
                await asyncio.sleep(3)

                m3u8 = await capture_m3u8(page)
                if m3u8:
                    log(f"✅ Stream found: {m3u8}")
                    streams.append((title, m3u8))
                else:
                    log("⚠️ No stream captured")

                await page.goto(BASE, wait_until="networkidle")
                await asyncio.sleep(4)

            except Exception as e:
                log(f"❌ Error: {e}")

        await browser.close()

    return streams


def write_playlists(entries):
    if not entries:
        log("❌ No streams captured.")
        return

    # VLC
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="NFL.Dummy.us" '
                f'tvg-logo="{LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={BASE}\n")
            f.write(f"#EXTVLCOPT:http-origin={BASE}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(url + "\n")

    # TiviMate
    ua = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{url}|referer={BASE}|origin={BASE}|user-agent={ua}\n")

    log(f"✅ Playlists written: {OUTPUT_VLC}, {OUTPUT_TIVI}")


async def main():
    log("🚀 Starting NFL Webcast scraper (fixed)...")
    streams = await scrape_nfl()
    write_playlists(streams)
    log("🎉 Done.")


if __name__ == "__main__":
    asyncio.run(main())
