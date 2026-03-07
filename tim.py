import asyncio
import os
from functools import partial
from typing import Any
from urllib.parse import urljoin, quote

from playwright.async_api import async_playwright, Browser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "TIMSTRMS"

CACHE_FILE = Cache(TAG, exp=10800)
API_FILE = Cache(f"{TAG}-api", exp=19800)

API_URL = os.environ.get("TIM_API_URL")
BASE_URL = os.environ.get("TIM_BASE_URL")

if not API_URL:
    raise RuntimeError("Missing TIM_API_URL secret")

if not BASE_URL:
    raise RuntimeError("Missing TIM_BASE_URL secret")

urls: dict[str, dict[str, str | float]] = {}

USER_AGENT = network.UA
UA_ENC = quote(USER_AGENT)

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


# --------------------------------------------------
# PLAYLISTS
# --------------------------------------------------

def write_playlists(data):

    log.info("Writing playlists")

    vlc = ["#EXTM3U"]
    tiv = ["#EXTM3U"]

    for name, e in data.items():

        if not e.get("url"):
            continue

        base = e["base"]

        vlc.extend([
            f'#EXTINF:-1 tvg-id="{e["id"]}" tvg-logo="{e["logo"]}" group-title="Live",{name}',
            f"#EXTVLCOPT:http-referrer={base}",
            f"#EXTVLCOPT:http-origin={base}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"]
        ])

        tiv.extend([
            f'#EXTINF:-1 tvg-id="{e["id"]}" tvg-logo="{e["logo"]}" group-title="Live",{name}',
            f'{e["url"]}|referer={base}|origin={base}|user-agent={UA_ENC}'
        ])

    with open("tim_vlc.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(vlc))

    with open("tim_tivimate.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(tiv))

    log.info("Playlists written successfully")


# --------------------------------------------------
# EVENTS
# --------------------------------------------------

async def get_events(cached_keys):

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):

        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(API_URL, log=log):
            api_data = r.json()
            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    events = []

    for info in api_data:

        if info.get("category") != "Events":
            continue

        for ev in info["events"]:

            genre = ev["genre"]

            if genre not in SPORT_GENRES:
                continue

            name = ev["name"]
            logo = ev.get("logo")
            url_id = ev["URL"]

            streams = ev.get("streams")

            if not streams:
                continue

            embed = streams[0].get("url")

            sport = SPORT_GENRES[genre]

            key = f"[{sport}] {name} ({TAG})"

            if key in cached_keys:
                continue

            events.append({
                "sport": sport,
                "event": name,
                "link": urljoin(BASE_URL, f"watch?id={url_id}"),
                "ref": embed,
                "logo": logo,
                "timestamp": now.timestamp()
            })

    return events


# --------------------------------------------------
# PLAYER TRIGGER
# --------------------------------------------------

async def trigger_player(page):

    await page.wait_for_timeout(8000)

    # click main page
    for _ in range(3):
        try:
            await page.mouse.click(640, 360)
            await asyncio.sleep(1)
        except:
            pass

    # trigger iframe player
    for frame in page.frames:

        if "embed" in frame.url or "player" in frame.url:

            try:
                await frame.click("body", timeout=3000)
                await asyncio.sleep(1)

                await frame.dblclick("body")
                await asyncio.sleep(1)

                await frame.press("body", "Space")

            except:
                pass

    # scroll to activate focus
    try:
        await page.mouse.wheel(0, 800)
    except:
        pass


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

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=True) as context:

            for i, ev in enumerate(events, start=1):

                async with network.event_page(context) as page:

                    await page.goto(ev["ref"], wait_until="domcontentloaded")

                    await trigger_player(page)

                    handler = partial(
                        network.process_event,
                        url=ev["ref"],
                        url_num=i,
                        page=page,
                        timeout=35,
                        log=log
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        timeout=50,
                        log=log
                    )

                    sport, event, logo, ref, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                        ev["ref"],
                        ev["timestamp"]
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": ref,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": ev["link"]
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


# --------------------------------------------------
# MAIN
# --------------------------------------------------

async def main():

    log.info("Starting TIM Streams updater")

    async with async_playwright() as p:

        browser = await p.firefox.launch(
            headless=True,
            args=["--no-sandbox"]
        )

        await scrape(browser)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
