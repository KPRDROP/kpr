import asyncio
import re
from typing import List, Dict
from urllib.parse import quote

from playwright.async_api import async_playwright

# ---------------- CONFIG ----------------

BASE_URL = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

STREAM_REGEX = re.compile(r"\.m3u8", re.I)

# ---------------- CORE SCRAPER ----------------

async def scrape_nfl() -> List[Dict]:
    streams: List[Dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        print("🌐 Loading NFLWebcast homepage...")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)

        # ✅ FIX: select by href pattern, not text
        watch_links = await page.query_selector_all('a[href*="live-stream"]')
        print(f"🔍 Found {len(watch_links)} WATCH links")

        event_urls = []
        for a in watch_links:
            href = await a.get_attribute("href")
            if href and href.startswith("https://nflwebcast.com/"):
                event_urls.append(href)

        await page.close()

        if not event_urls:
            print("❌ No event pages found.")
            await browser.close()
            return []

        print(f"📌 Processing {len(event_urls)} event pages")

        for event_url in event_urls:
            print(f"➡️ Opening event page: {event_url}")
            page = await context.new_page()
            captured = []

            def handle_request(req):
                if STREAM_REGEX.search(req.url) and "/sig/" in req.url:
                    if req.url not in captured:
                        print(f"✅ Captured stream: {req.url}")
                        captured.append(req.url)

            page.on("request", handle_request)

            try:
                await page.goto(event_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(12000)
            except Exception as e:
                print(f"⚠️ Page load error: {e}")

            if captured:
                title = await page.title()
                clean_title = (
                    title.replace("Live Stream Online Free", "")
                    .replace("NFL", "")
                    .strip()
                )

                streams.append({
                    "name": clean_title,
                    "url": captured[-1],
                    "ref": BASE_URL
                })

            page.remove_listener("request", handle_request)
            await page.close()

        await browser.close()

    return streams

# ---------------- PLAYLIST WRITERS ----------------

def write_vlc_playlist(streams: List[Dict]):
    if not streams:
        print("❌ No streams to write (VLC).")
        return

    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            f.write(f"#EXTINF:-1,{s['name']}\n")
            f.write(f"#EXTVLCOPT:http-referrer={s['ref']}\n")
            f.write(f"#EXTVLCOPT:http-origin={s['ref']}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(s["url"] + "\n")

    print(f"✅ VLC playlist saved: {OUTPUT_VLC}")

def write_tivimate_playlist(streams: List[Dict]):
    if not streams:
        print("❌ No streams to write (TiviMate).")
        return

    ua = quote(USER_AGENT, safe="")

    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            f.write(f"#EXTINF:-1,{s['name']}\n")
            f.write(
                f"{s['url']}"
                f"|referer={s['ref']}"
                f"|origin={s['ref']}"
                f"|user-agent={ua}\n"
            )

    print(f"✅ TiviMate playlist saved: {OUTPUT_TIVI}")

# ---------------- MAIN ----------------

async def main():
    print("🚀 Starting NFL Webcast scraper (fixed selector)...")
    streams = await scrape_nfl()

    if not streams:
        print("❌ No streams captured.")
        return

    write_vlc_playlist(streams)
    write_tivimate_playlist(streams)

    print(f"🎉 Done. Exported {len(streams)} NFL streams.")

if __name__ == "__main__":
    asyncio.run(main())
