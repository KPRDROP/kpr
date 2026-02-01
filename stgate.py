import asyncio
import json
import os
from pathlib import Path
from typing import Dict, List
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page

from utils import Cache, Time, get_logger, network

log = get_logger(__name__)

TAG = "STGATE"

BASE_URL = os.environ.get("STGATE_BASE_URL", "https://streamingon.org")

JSON_ENDPOINTS = [
    "soccer.json",
    "nfl.json",
    "nba.json",
    "cfb.json",
    "mlb.json",
    "nhl.json",
    "ufc.json",
    "box.json",
    "f1.json",
]

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

EVENT_CACHE = Cache(CACHE_DIR / "stgate_events.json")
STREAM_CACHE = Cache(CACHE_DIR / "stgate_streams.json")


# --------------------------------------------------
# JSON LOADER
# --------------------------------------------------

async def load_events() -> List[Dict]:
    events = []

    async with network.session() as session:
        for name in JSON_ENDPOINTS:
            url = f"{BASE_URL}/data/{name}"
            try:
                async with session.get(url, timeout=15) as r:
                    if r.status != 200:
                        continue
                    data = await r.json()
                    items = data.get("events", []) or data
                    log.info(f"{name} → {len(items)} events")
                    events.extend(items)
            except Exception as e:
                log.warning(f"{name} failed: {e}")

    return events


# --------------------------------------------------
# PLAYER SCRAPER
# --------------------------------------------------

async def extract_m3u8(page: Page, event_url: str) -> str | None:
    found = None

    def on_request(req):
        nonlocal found
        if found:
            return
        url = req.url
        if ".m3u8" in url:
            found = url

    page.on("requestfinished", on_request)

    try:
        await page.goto(event_url, timeout=30_000, wait_until="domcontentloaded")

        # Momentum click (ads open first)
        await page.mouse.click(400, 300)
        await asyncio.sleep(1)
        await page.mouse.click(400, 300)

        # Wait for stream
        for _ in range(15):
            if found:
                return found
            await asyncio.sleep(1)

    except Exception as e:
        log.debug(f"Player error: {e}")

    return None


# --------------------------------------------------
# MAIN SCRAPER
# --------------------------------------------------

async def scrape():
    log.info("🚀 Starting STGATE scraper")

    cached_streams = STREAM_CACHE.load(default={})
    cached_events = EVENT_CACHE.load(default={})

    events = await load_events()

    log.info(f"Loaded {len(cached_events)} cached event(s)")

    new_event_urls = []

    for ev in events:
        path = ev.get("url") or ev.get("link")
        if not path:
            continue
        full_url = urljoin(BASE_URL, path)
        if full_url not in cached_events:
            new_event_urls.append(full_url)

    log.info(f"Processing {len(new_event_urls)} new stream URL(s)")

    if not new_event_urls:
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        for idx, url in enumerate(new_event_urls, 1):
            log.info(f"URL {idx}) Opening event page")

            m3u8 = await extract_m3u8(page, url)

            if not m3u8:
                log.warning(f"URL {idx}) No valid source")
                continue

            cached_streams[url] = {
                "m3u8": m3u8,
                "time": Time.now(),
            }

            cached_events[url] = True
            log.info(f"✅ Stream found")

        await browser.close()

    STREAM_CACHE.save(cached_streams)
    EVENT_CACHE.save(cached_events)


# --------------------------------------------------
# ENTRYPOINT
# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
