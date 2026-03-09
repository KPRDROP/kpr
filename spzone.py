from functools import partial
from typing import Any
import os

from playwright.async_api import Browser

from utils import Cache, Time, get_logger, leagues, network


log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SPRTZONE"

CACHE_FILE = Cache(TAG, exp=5400)

API_FILE = Cache(f"{TAG}-api", exp=28800)

API_URL = os.environ.get("SPZONE_API_URL")

HOME_URL = os.environ.get("HOME_URL")


# ---------------------------------------------------------
# API CACHE
# ---------------------------------------------------------

async def refresh_api_cache(now_ts: float) -> list[dict[str, Any]]:

    api_data = [{"timestamp": now_ts}]

    if r := await network.request(API_URL, log=log):

        api_data: list[dict] = r.json().get("matches", [])

        if api_data:

            for event in api_data:
                event["ts"] = event.pop("timestamp")

        api_data[-1]["timestamp"] = now_ts

    return api_data


# ---------------------------------------------------------
# EVENT DISCOVERY
# ---------------------------------------------------------

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):

        log.info("Refreshing API cache")

        api_data = await refresh_api_cache(now.timestamp())

        API_FILE.write(api_data)

    events = []

    # SportZone publishes streams many hours early
    start_dt = now.delta(hours=-12)
    end_dt = now.delta(hours=12)

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

        # FIXED timestamp conversion
        event_dt = Time.from_ts(int(event_ts / 1000))

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


# ---------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------

async def scrape(browser: Browser) -> None:

    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{HOME_URL}"')

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        async with network.event_context(browser, stealth=False) as context:

            for i, ev in enumerate(events, start=1):

                async with network.event_page(context) as page:

                    link = ev["link"]

                    try:

                        await page.goto(link, wait_until="domcontentloaded")

                        # allow iframe + JS player to initialize
                        await page.wait_for_timeout(5000)

                        # click main page (virazo requires interaction)
                        for _ in range(2):
                            try:
                                await page.mouse.click(400, 300)
                                await page.wait_for_timeout(1000)
                            except:
                                pass

                        # click inside iframe players
                        for frame in page.frames:
                            try:
                                await frame.click("body", timeout=1500)
                                await page.wait_for_timeout(1000)
                            except:
                                pass

                    except Exception:
                        log.warning(f"URL {i}) Failed to initialize player")

                    handler = partial(
                        network.process_event,
                        url=link,
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
