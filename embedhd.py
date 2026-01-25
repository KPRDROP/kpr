import asyncio
import json
import re
import sys
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ================= CONFIG =================

BASE_URL = "https://embedhd.xyz"  # your secret/env can override this
CACHE_FILE = Path("cache/embedhd.json")
OUTPUT_FILE = Path("embedhd.m3u8")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)

MAX_WAIT_MS = 12000

# ================= LOGGING =================

def log(level, msg):
    ts = datetime.now().strftime("%Y-%m-%d | %H:%M:%S")
    print(f"[{ts}] {level:<8} {msg}")

# ================= CACHE =================

def load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_cache(data):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2))

# ================= CORE =================

async def resolve_stream(page, url):
    stream_url = None

    def on_request(req):
        nonlocal stream_url
        if ".m3u8" in req.url and not stream_url:
            stream_url = req.url

    page.on("request", on_request)

    try:
        await page.goto(url, timeout=MAX_WAIT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # force iframe execution
        for frame in page.frames:
            if "center" in frame.url or "play" in frame.url:
                try:
                    await frame.wait_for_timeout(2000)
                except Exception:
                    pass

        # wait a bit longer for HLS
        for _ in range(10):
            if stream_url:
                return stream_url
            await page.wait_for_timeout(500)

    except PWTimeout:
        pass

    return None

# ================= MAIN =================

async def main():
    log("INFO", "🚀 Starting EmbedHD scraper...")

    cache = load_cache()
    log("INFO", f"Loaded {len(cache)} event(s) from cache")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
        )

        page = await context.new_page()

        events = []
        for idx, slug in enumerate(cache.get("events", []), start=1):
            url = f"{BASE_URL}/{slug}"
            log("INFO", f"Scraping from \"{url}\"")

            stream = await resolve_stream(page, url)
            if not stream:
                log("WARNING", f"URL {idx}) Timed out after {MAX_WAIT_MS//1000}s, skipping event")
                continue

            events.append({
                "name": slug,
                "url": stream
            })

        await browser.close()

    # ================= OUTPUT =================

    OUTPUT_FILE.write_text("#EXTM3U\n")
    with OUTPUT_FILE.open("a") as f:
        for ev in events:
            f.write(f'#EXTINF:-1,{ev["name"]}\n')
            f.write(f'{ev["url"]}\n')

    log("INFO", f"Wrote {len(events)} total events")

# ================= ENTRY =================

if __name__ == "__main__":
    asyncio.run(main())
