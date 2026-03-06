#!/usr/bin/env python3
import asyncio
import os
from typing import Any
from functools import partial
from pathlib import Path
from urllib.parse import quote urljoin

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

TAG = "TIMSTRMS"

API_URL = os.environ.get("TIM_API_URL")
BASE_URL = os.environ.get("TIM_BASE_URL")

if not API_URL:
    raise RuntimeError("Missing TIM_API_URL secret")

if not BASE_URL:
    raise RuntimeError("Missing TIM_BASE_URL secret")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)

UA_ENC = quote(USER_AGENT)

OUT_VLC = Path("tim_vlc.m3u8")
OUT_TIVI = Path("tim_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=10800)

urls: dict[str, dict] = {}

# --------------------------------------------------
# GENRES
# --------------------------------------------------

SPORT_GENRES = {
    1: "Soccer",
    2: "Motorsport",
    3: "MMA",
    4: "Fight",
    5: "Boxing",
    6: "Wrestling",
    7: "Basketball",
    8: "American Football",
    9: "Baseball",
    10: "Tennis",
    11: "Hockey",
    12: "Darts",
}

# --------------------------------------------------
# PLAYLIST WRITER
# --------------------------------------------------

def build_playlists(data: dict):

    vlc = ["#EXTM3U"]
    tiv = ["#EXTM3U"]

    for name, e in data.items():

        if not e.get("url"):
            continue

        vlc.extend([
            f'#EXTINF:-1 tvg-id="{e["id"]}" tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={e['base']}",
            f"#EXTVLCOPT:http-origin={e['base']}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        tiv.extend([
            f'#EXTINF:-1 tvg-id="{e["id"]}" tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f'{e["url"]}|referer={e["base"]}|origin={e["base"]}|user-agent={UA_ENC}',
        ])

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tiv), encoding="utf-8")

    log.info("Playlists written successfully")

# --------------------------------------------------
# API EVENTS
# --------------------------------------------------

async def get_events():

    import requests

    log.info("Fetching TIM API")

    r = requests.get(API_URL, timeout=20)
    api_data = r.json()

    events = []

    for block in api_data:

        if block.get("category") != "Events":
            continue

        for ev in block.get("events", []):

            name = ev.get("name")
            genre = ev.get("genre")

            sport = SPORT_GENRES.get(genre, "Live Event")

            logo = ev.get("logo")

            embed_url = ev["streams"][0]["url"]

            events.append({
                "sport": sport,
                "event": name,
                "link": embed_url,
                "logo": logo,
                "timestamp": Time.now().timestamp(),
            })

    return events

# --------------------------------------------------
# NETWORK M3U8 CAPTURE
# --------------------------------------------------

async def capture_stream(page, url, url_num):

    captured = None

    def interceptor(request):
        nonlocal captured
        if ".m3u8" in request.url and not captured:
            captured = request.url

    page.context.on("request", interceptor)

    try:

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        await page.wait_for_timeout(5000)

        # trigger players
        for _ in range(2):
            try:
                await page.mouse.click(400, 300)
                await asyncio.sleep(1)
            except:
                pass

        waited = 0

        while waited < 20 and not captured:
            await asyncio.sleep(1)
            waited += 1

    finally:
        try:
            page.context.remove_listener("request", interceptor)
        except:
            pass

    if captured:
        log.info(f"URL {url_num}) Captured M3U8")

    else:
        log.warning(f"URL {url_num}) Stream not found")

    return captured

# --------------------------------------------------
# SCRAPER
# --------------------------------------------------

async def scrape(browser):

    cached_urls = CACHE_FILE.load() or {}

    log.info(f"Loaded {len(cached_urls)} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events()

    if not events:
        log.info("No events from API")
        build_playlists(cached_urls)
        return

    log.info(f"Processing {len(events)} events")

    context = await browser.new_context(user_agent=USER_AGENT)

    for i, ev in enumerate(events, start=1):

        page = await context.new_page()

        stream = await capture_stream(page, ev["link"], i)

        await page.close()

        if not stream:
            continue

        sport = ev["sport"]
        event = ev["event"]
        logo = ev["logo"]

        key = f"[{sport}] {event} ({TAG})"

        tvg_id, pic = leagues.get_tvg_info(sport, event)

        cached_urls[key] = {
            "url": stream,
            "logo": logo or pic,
            "base": ev["link"],
            "timestamp": ev["timestamp"],
            "id": tvg_id or "Live.Event.us",
        }

    await context.close()

    CACHE_FILE.write(cached_urls)

    build_playlists(cached_urls)

# --------------------------------------------------
# MAIN
# --------------------------------------------------

async def main():

    log.info("Starting TIM Streams updater")

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        await scrape(browser)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
