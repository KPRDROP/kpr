import asyncio
import os
from functools import partial
from typing import Any
from urllib.parse import quote

from playwright.async_api import async_playwright, Browser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SPZONE"

CACHE_FILE = Cache(TAG, exp=5_400)

API_FILE = Cache(f"{TAG}-api", exp=28_800)

API_URL = os.environ.get("SPZONE_API_URL")

if not API_URL:
    raise RuntimeError("Missing SPZONE_API_URL secret")

USER_AGENT = network.UA
UA_ENC = quote(USER_AGENT)


# -------------------------------------------------
# PLAYLIST WRITER
# -------------------------------------------------

def write_playlists(entries: dict) -> None:

    log.info("Writing playlists")

    vlc_lines = ["#EXTM3U"]
    tiv_lines = ["#EXTM3U"]

    for name, data in entries.items():

        url = data.get("url")

        if not url:
            continue

        logo = data["logo"]
        tvg_id = data["id"]
        base = data["base"]

        # VLC
        vlc_lines.extend([
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={base}",
            f"#EXTVLCOPT:http-origin={base}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            url
        ])

        # TiviMate
        tiv_url = (
            f"{url}|referer={base}|origin={base}|user-agent={UA_ENC}"
        )

        tiv_lines.extend([
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="Live Events",{name}',
            tiv_url
        ])

    with open("spzone_vlc.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(vlc_lines))

    with open("spzone_tivimate.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(tiv_lines))

    log.info("Playlists written successfully")


# -------------------------------------------------
# API REFRESH
# -------------------------------------------------

async def refresh_api_cache(now_ts: float) -> list[dict[str, Any]]:
    api_data = [{"timestamp": now_ts}]

    if r := await network.request(API_URL, log=log):

        api_data: list[dict] = r.json().get("matches", [])

        if api_data:
            for event in api_data:
                event["ts"] = event.pop("timestamp")

        api_data[-1]["timestamp"] = now_ts

    return api_data


# -------------------------------------------------
# EVENTS
# -------------------------------------------------

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):

        log.info("Refreshing API cache")

        api_data = await refresh_api_cache(now.timestamp())

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(hours=-3)
    end_dt = now.delta(minutes=30)

    for stream_group in api_data:

        sport = stream_group.get("league")

        team_1 = stream_group.get("team1")
        team_2 = stream_group.get("team2")

        if not (sport and team_1 and team_2):
            continue

        event_name = f"{team_1} vs {team_2}"

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        if not (event_ts := stream_group.get("ts")):
            continue

        event_dt = Time.from_ts(int(f"{event_ts}"[:-3]))

        if not start_dt <= event_dt <= end_dt:
            continue

        if not (event_channels := stream_group.get("channels")):
            continue

        if not (event_links := event_channels[0].get("links")):
            continue

        event_url: str = event_links[0]

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": event_url,
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

    log.info('Scraping from "sportzone"')

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

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

                    sport, event = ev["sport"], ev["event"]

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://vividmosaica.com/",
                        "timestamp": now.timestamp(),
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

    write_playlists(cached_urls)


# -------------------------------------------------
# MAIN
# -------------------------------------------------

async def main():

    log.info("Starting SportZone updater")

    async with async_playwright() as p:

        browser = await p.firefox.launch(
            headless=True,
            args=["--no-sandbox"]
        )

        await scrape(browser)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
