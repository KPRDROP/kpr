#!/usr/bin/env python3

import asyncio
import httpx
import re
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

CACHE_FILE = Cache("streamfree.json", exp=19_800)

BASE_URL = "https://streamfree.to"
TAG = "STRMFR"

OUTPUT_VLC = "StreamFree_VLC.m3u8"
OUTPUT_TIVI = "StreamFree_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

HEADERS = {
    "referer": BASE_URL,
    "origin": BASE_URL,
    "user-agent": USER_AGENT,
}

urls: dict[str, dict[str, str | float]] = {}


# ----------------------------------------------------------------------
# PART 1 — API fetch
# ----------------------------------------------------------------------

async def refresh_api_cache(client: httpx.AsyncClient, url: str):
    try:
        r = await client.get(url, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{url}": {e}')
        return {}
    return r.json()


async def get_events(client: httpx.AsyncClient):
    api_url = f"{BASE_URL}/streams"
    api_data = await refresh_api_cache(client, api_url)
    events = {}

    now = Time.now().timestamp()

    for streams in api_data.get("streams", {}).values():
        if not streams:
            continue

        for stream in streams:
            sport = stream.get("league")
            name = stream.get("name")
            stream_key = stream.get("stream_key")

            if not (sport and name and stream_key):
                continue

            key = f"[{sport}] {name} ({TAG})"

            logo = (
                stream.get("thumbnail_url")
                and (BASE_URL + stream["thumbnail_url"])
            ) or None

            tvg_id, pic = leagues.get_tvg_info(sport, name)

            # Placeholder URL until Playwright confirms real URL
            proxy_url = network.build_proxy_url(
                tag=TAG,
                path=f"{stream_key}720p/index.m3u8",
                query={"stream_name": name},
            )

            events[key] = {
                "url": proxy_url,
                "logo": logo or pic,
                "base": BASE_URL,
                "timestamp": now,
                "id": tvg_id or "Live.Event.us",
                "page_url": f"{BASE_URL}/stream/{stream_key}"
            }

    return events


# ----------------------------------------------------------------------
# PART 2 — Extract real .m3u8 from stream page with Playwright
# ----------------------------------------------------------------------

async def extract_m3u8_from_page(playwright, page_url: str):
    """Open StreamFree player, capture the real m3u8 request."""
    browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    captured = None

    def handler(response):
        nonlocal captured
        url = response.url
        if ".m3u8" in url and "master" in url.lower():
            if not captured:
                captured = url

    page.on("response", handler)

    try:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass

    # Try clicking player elements
    try:
        for sel in ["video", ".player", "#player", "body"]:
            loc = page.locator(sel)
            if await loc.count() > 0:
                try:
                    await loc.first.click(timeout=1000, force=True)
                except Exception:
                    pass
    except Exception:
        pass

    # Wait for m3u8 capture
    for _ in range(25):
        if captured:
            break
        await asyncio.sleep(0.4)

    # Search manually in page HTML
    if not captured:
        html = await page.content()
        found = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
        if found:
            captured = found.group(0)

    await page.close()
    await context.close()
    await browser.close()

    return captured


# ----------------------------------------------------------------------
# PART 3 — Playlist writers
# ----------------------------------------------------------------------

def write_playlists(events: dict):
    # VLC playlist
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, data in events.items():
            f.write(
                f'#EXTINF:-1 tvg-id="{data["id"]}" tvg-logo="{data["logo"]}" '
                f'group-title="StreamFree",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={HEADERS['referer']}\n")
            f.write(f"#EXTVLCOPT:http-origin={HEADERS['origin']}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{data['url']}\n\n")

    # TiviMate playlist
    ua_enc = quote_plus(USER_AGENT)
    referer = HEADERS["referer"]
    origin = HEADERS["origin"]

    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, data in events.items():
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(
                f"{data['url']}|referer={referer}|origin={origin}|user-agent={ua_enc}\n"
            )

    log(f"✔ Playlists written: {OUTPUT_VLC}, {OUTPUT_TIVI}")


# ----------------------------------------------------------------------
# PART 4 — Main scraper logic
# ----------------------------------------------------------------------

async def scrape(client: httpx.AsyncClient):
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
        return

    log.info(f'Scraping "{BASE_URL}" via API')
    events = await get_events(client)

    # Extract real m3u8 URLs using Playwright
    async with async_playwright() as p:
        for title, entry in events.items():
            page_url = entry["page_url"]
            log.info(f"🔍 Capturing .m3u8 for: {title}")

            real = await extract_m3u8_from_page(p, page_url)

            if real:
                log.info(f"✔ Found m3u8: {real}")
                entry["url"] = real
            else:
                log.warning(f"⚠ No m3u8 found, using proxy fallback")

    urls.update(events)
    CACHE_FILE.write(urls)
    write_playlists(urls)
    log.info(f"✔ Completed with {len(urls)} events")


# For manual testing
async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        await scrape(client)


if __name__ == "__main__":
    asyncio.run(main())
