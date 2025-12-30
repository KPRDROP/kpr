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
    if not text:
        return "NFL Live"
    text = re.sub(r"\s+", " ", text)
    return text.replace("@", "vs").strip()


async def capture_m3u8(page, timeout=8):
    found = None

    def on_response(resp):
        nonlocal found
        if ".m3u8" in resp.url and not found:
            found = resp.url

    page.on("response", on_response)
    await asyncio.sleep(timeout)
    return found


async def scrape_nfl():
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        log("🌐 Loading NFLWebcast homepage...")
        await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)

        # WATCH buttons (this is the key fix)
        watch_buttons = page.locator("button:has-text('WATCH')")
        count = await watch_buttons.count()

        log(f"🔍 Found {count} WATCH buttons")

        if count == 0:
            await browser.close()
            return []

        for i in range(count):
            try:
                log(f"➡️ Opening game {i + 1}/{count}")

                # Extract visible game title
                card = watch_buttons.nth(i).locator("xpath=ancestor::tr | ancestor::div")
                title_text = await card.inner_text()
                title = clean_title(title_text.split("\n")[0])

                await watch_buttons.nth(i).click(force=True)
                await asyncio.sleep(2)

                m3u8 = await capture_m3u8(page)

                if m3u8:
                    log(f"✅ Stream found: {m3u8}")
                    results.append((title, m3u8))
                else:
                    log("⚠️ No stream captured")

                # Reset to homepage for next game
                await page.goto(BASE, wait_until="domcontentloaded")
                await asyncio.sleep(3)

            except Exception as e:
                log(f"❌ Error processing game: {e}")

        await browser.close()

    return results


def write_playlists(entries):
    if not entries:
        log("❌ No streams captured.")
        return

    # VLC playlist
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

    # TiviMate playlist
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
