import asyncio
import os
from urllib.parse import urljoin
from typing import Dict, List, Optional

from playwright.async_api import async_playwright, Page

from utils import Cache, Time, get_logger, network

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

TAG = "STGATE"

BASE_URL = os.environ.get("STGATE_BASE_URL", "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("STGATE_BASE_URL is not set")

REFERER = "https://instreams.click/"
ORIGIN = "https://instreams.click/"

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

# Cache names MUST be strings
EVENT_CACHE = Cache("stgate_events", exp=0)
STREAM_CACHE = Cache("stgate_streams", exp=0)

log = get_logger(__name__)

# --------------------------------------------------
# LOAD EVENTS FROM JSON
# --------------------------------------------------

async def load_events() -> List[str]:
    urls: List[str] = []

    async with network.session(headers={
        "Referer": REFERER,
        "Origin": ORIGIN,
        "User-Agent": "Mozilla/5.0",
    }) as session:

        for name in JSON_ENDPOINTS:
            api_url = f"{BASE_URL}/data/{name}"
            try:
                async with session.get(api_url, timeout=20) as r:
                    if r.status != 200:
                        log.warning(f"{name} → HTTP {r.status}")
                        continue

                    data = await r.json()
                    events = data.get("events", data)

                    log.info(f"{name} → {len(events)} events")

                    for ev in events:
                        streams = ev.get("streams") or []
                        if not streams:
                            continue

                        src = streams[0].get("url")
                        if not src:
                            continue

                        urls.append(urljoin(BASE_URL + "/", src.lstrip("/")))

            except Exception as e:
                log.warning(f"{name} failed: {e}")

    return urls


# --------------------------------------------------
# STREAM EXTRACTION
# --------------------------------------------------

async def extract_m3u8(page: Page, url: str) -> Optional[str]:
    found: Optional[str] = None

    def on_request(req):
        nonlocal found
        if found:
            return
        if ".m3u8" in req.url:
            found = req.url

    page.on("requestfinished", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # momentum clicks (ads → player)
        await page.mouse.click(400, 300)
        await asyncio.sleep(1)
        await page.mouse.click(400, 300)

        for _ in range(25):
            if found:
                return found
            await asyncio.sleep(0.8)

    except Exception as e:
        log.debug(f"Player error: {e}")

    return None


# --------------------------------------------------
# MAIN SCRAPER
# --------------------------------------------------

async def scrape():
    log.info("🚀 Starting STGATE scraper")

    cached_events = EVENT_CACHE.load()
    if not isinstance(cached_events, dict):
        cached_events = {}

    cached_streams = STREAM_CACHE.load()
    if not isinstance(cached_streams, dict):
        cached_streams = {}

    event_urls = await load_events()
    new_urls = [u for u in event_urls if u not in cached_events]

    log.info(f"Processing {len(new_urls)} new stream URL(s)")

    if not new_urls:
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            extra_http_headers={
                "Referer": REFERER,
                "Origin": ORIGIN,
                "User-Agent": "Mozilla/5.0",
            }
        )

        for idx, url in enumerate(new_urls, 1):
            log.info(f"{idx}/{len(new_urls)} Opening event")

            m3u8 = await extract_m3u8(page, url)
            cached_events[url] = True

            if not m3u8:
                log.warning("No stream found")
                continue

            cached_streams[url] = {
                "m3u8": m3u8,
                "timestamp": Time.now(),
                "tag": TAG,
            }

            log.info("✅ Stream captured")

        await browser.close()

    EVENT_CACHE.write(cached_events)
    STREAM_CACHE.write(cached_streams)


# --------------------------------------------------
# ENTRYPOINT
# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
