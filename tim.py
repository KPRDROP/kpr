import os
import asyncio
from typing import Any
from functools import partial
from typing import Any
from urllib.parse import urljoin, quote

from playwright.async_api import Browser, async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIMSTRMS"

CACHE_FILE = Cache(TAG, exp=10_800)
API_FILE = Cache(f"{TAG}-api", exp=19_800)

API_URL = os.environ.get("TIM_API_URL")
BASE_URL = os.environ.get("TIM_BASE_URL")

if not API_URL:
    raise RuntimeError("Missing TIM_API_URL secret")

if not BASE_URL:
    raise RuntimeError("Missing TIM_BASE_URL secret")


SPORT_GENRES = {
    1: "Soccer",
    2: "Motorsport",
    3: "MMA",
    4: "Fight",
    5: "Boxing",
    6: "Wrestling",
    7: "Basketball",
    9: "Baseball",
    10: "Tennis",
    11: "Hockey",
}


# -------------------------------------------------
# GET EVENTS
# -------------------------------------------------

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(API_URL, log=log):
            api_data: list[dict] = r.json()
            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    events = []

    for info in api_data:

        if info.get("category") != "Events":
            continue

        stream_events: list[dict[str, Any]] = info["events"]

        for ev in stream_events:

            if (genre := ev["genre"]) not in SPORT_GENRES:
                continue

            name: str = ev["name"]
            url_id: str = ev["URL"]
            logo: str | None = ev.get("logo")

            sport = SPORT_GENRES[genre]

            if f"[{sport}] {name} ({TAG})" in cached_keys:
                continue

            if not (streams := ev["streams"]) or not (url := streams[0].get("url")):
                continue

            events.append(
                {
                    "sport": sport,
                    "event": name,
                    "link": urljoin(BASE_URL, f"watch?id={url_id}"),
                    "ref": url,
                    "logo": logo,
                    "timestamp": now.timestamp(),
                }
            )

    return events


# -------------------------------------------------
# SCRAPER
# -------------------------------------------------

async def scrape(browser: Browser) -> None:

    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:

            for i, ev in enumerate(events, start=1):

                async with network.event_page(context) as page:

                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
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

                    sport, event, logo, ref, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                        ev["ref"],
                        ev["timestamp"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": ref,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1
                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)

    write_playlists(urls)


# -------------------------------------------------
# PLAYLIST GENERATOR
# -------------------------------------------------

def write_playlists(entries: dict):

    log.info("Writing playlists")

    ua = quote(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0"
    )

    referer = "https://hmembeds.one/"
    origin = "https://hmembeds.one"

    with open("tim_vlc.m3u8", "w", encoding="utf8") as vlc, \
         open("tim_tivimate.m3u8", "w", encoding="utf8") as tivimate:

        vlc.write("#EXTM3U\n")
        tivimate.write("#EXTM3U\n")

        for name, data in entries.items():

            url = data["url"]
            logo = data["logo"]
            tvg_id = data["id"]

            if not url:
                continue

            vlc.write(
                f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}",{name}\n{url}\n'
            )

            tivimate_url = (
                f"{url}|referer={referer}|origin={origin}|user-agent={ua}"
            )

            tivimate.write(
                f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}",{name}\n{tivimate_url}\n'
            )

    log.info("Playlists written successfully")

# -------------------------------------------------
# MAIN
# -------------------------------------------------

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
