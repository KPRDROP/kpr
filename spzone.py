import asyncio
import os
import re
import request
from functools import partial
from typing import Any

from playwright.async_api import async_playwright, Browser

from utils import Cache, Time, get_logger, leagues, network


log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SPZONE"

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

        event_dt = Time.from_ts(int(event_ts / 1000))

        if not start_dt <= event_dt <= end_dt:
            continue

        if not (event_channels := stream_group.get("channels")):
            continue

        if not (event_links := event_channels[0].get("links")):
            continue

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": event_links[0],
            }
        )

    return events


# ---------------------------------------------------------
# DOM FALLBACK
# ---------------------------------------------------------

async def extract_m3u8_dom(page):

    try:

        content = await page.content()

        match = re.search(r"https?://[^\"']+\.m3u8[^\"']*", content)

        if match:
            return match.group(0)

        video_src = await page.evaluate(
            """() => {
                const v = document.querySelector("video");
                return v ? v.src : null;
            }"""
        )

        if video_src and ".m3u8" in video_src:
            return video_src

    except:
        pass

    return None


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

                    captured = []

                    got_one = asyncio.Event()

                    handler = partial(
                        network.capture_req,
                        captured=captured,
                        got_one=got_one,
                    )

                    page.on("request", handler)

                    try:

                        await page.goto(link, wait_until="domcontentloaded")

                        await page.wait_for_timeout(6000)

                        # interaction triggers player start
                        for _ in range(2):
                            try:
                                await page.mouse.click(400, 300)
                                await page.wait_for_timeout(1200)
                            except:
                                pass

                        for frame in page.frames:
                            try:
                                await frame.click("body", timeout=1500)
                                await page.wait_for_timeout(1000)
                            except:
                                pass

                        try:
                            await asyncio.wait_for(got_one.wait(), timeout=20)
                        except asyncio.TimeoutError:
                            log.warning(f"URL {i}) M3U8 not captured via network")

                    except Exception:
                        log.warning(f"URL {i}) Player initialization failed")

                    finally:
                        page.remove_listener("request", handler)

                    url = captured[0] if captured else None

                    if not url:
                        url = await extract_m3u8_dom(page)

                        if url:
                            log.info(f"URL {i}) M3U8 found via DOM fallback")

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

                        log.info(f"URL {i}) Stream captured")

                    else:

                        log.warning(f"URL {i}) No stream found")

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:

        log.info("No new events found")

    CACHE_FILE.write(cached_urls)


# ------------------------------------------------
# MAIN
# ------------------------------------------------

async def main():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            await scrape(browser)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
