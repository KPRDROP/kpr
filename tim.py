#!/usr/bin/env python3
import asyncio
from functools import partial
from pathlib import Path
from urllib.parse import quote
import os

from playwright.async_api import async_playwright, Browser

from utils import Cache, Time, get_logger, leagues, network

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
API_FILE = Cache(f"{TAG}-api", exp=19800)

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
    13: "Cricket",
    14: "Cycling",
    15: "Rugby",
}

# --------------------------------------------------
# PLAYLIST BUILDER
# --------------------------------------------------

def build_playlists(data: dict):

    vlc = ["#EXTM3U"]
    tiv = ["#EXTM3U"]

    for name, e in data.items():

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

async def get_events(cached_keys):

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        if r := await network.request(API_URL, log=log):
            api_data = r.json()
            api_data[-1]["timestamp"] = now.timestamp()

            API_FILE.write(api_data)

    events = []

    start_dt = now.delta(minutes=-60)
    end_dt = now.delta(hours=6)

    for info in api_data:

        if info.get("category") != "Events":
            continue

        for ev in info.get("events", []):

            genre = ev.get("genre")

            sport = SPORT_GENRES.get(genre, "Live Event")

            event_dt = Time.from_str(ev["time"], timezone="EST")

            if not start_dt <= event_dt <= end_dt:
                continue

            name = ev["name"]

            embed_url = ev["streams"][0]["url"]

            logo = ev.get("logo")

            key = f"[{sport}] {name} ({TAG})"

            if key in cached_keys:
                continue

            events.append({
                "sport": sport,
                "event": name,
                "link": embed_url,
                "logo": logo,
                "timestamp": event_dt.timestamp(),
            })

    return events

# --------------------------------------------------
# SCRAPER
# --------------------------------------------------

async def scrape(browser: Browser):

    cached_urls = CACHE_FILE.load() or {}

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    if not events:
        log.info("No new events found")
        build_playlists(cached_urls)
        return

    log.info(f"Processing {len(events)} new URL(s)")

    async with network.event_context(browser, stealth=False) as context:

        for i, ev in enumerate(events, start=1):

            async with network.event_page(context) as page:

                handler = partial(
                    network.process_event,
                    url=ev["link"],
                    url_num=i,
                    page=page,
                    log=log,
                )

                url = await network.safe_process(
                    handler,
                    url_num=i,
                    semaphore=network.PW_S,
                    log=log,
                )

                sport = ev["sport"]
                event = ev["event"]
                logo = ev["logo"]
                ts = ev["timestamp"]

                key = f"[{sport}] {event} ({TAG})"

                tvg_id, pic = leagues.get_tvg_info(sport, event)

                entry = {
                    "url": url,
                    "logo": logo or pic,
                    "base": ev["link"],
                    "timestamp": ts,
                    "id": tvg_id or "Live.Event.us",
                    "link": ev["link"],
                }

                cached_urls[key] = entry

                if url:
                    valid_count += 1
                    urls[key] = entry

    CACHE_FILE.write(cached_urls)

    build_playlists(cached_urls)

    log.info(f"Collected {valid_count - cached_count} new event(s)")

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
