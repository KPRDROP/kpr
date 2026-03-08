import asyncio
import os
from functools import partial
from urllib.parse import quote

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls = {}

TAG = "SPZONE"

CACHE_FILE = Cache(TAG, exp=5400)
API_FILE = Cache(f"{TAG}-api", exp=28800)

API_URL = os.environ.get("SPZONE_API_URL")

if not API_URL:
    raise RuntimeError("Missing SPZONE_API_URL secret")

USER_AGENT = network.UA
UA_ENC = quote(USER_AGENT)


# -------------------------------------------------
# PLAYLIST WRITER
# -------------------------------------------------

def write_playlists(entries):

    log.info("Writing playlists")

    vlc = ["#EXTM3U"]
    tiv = ["#EXTM3U"]

    for name, e in entries.items():

        if not e.get("url"):
            continue

        base = e["base"]
        logo = e["logo"]
        tvg_id = e["id"]
        url = e["url"]

        vlc.extend([
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={base}",
            f"#EXTVLCOPT:http-origin={base}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            url
        ])

        tiv.extend([
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="Live Events",{name}',
            f"{url}|referer={base}|origin={base}|user-agent={UA_ENC}"
        ])

    with open("spzone_vlc.m3u8","w",encoding="utf8") as f:
        f.write("\n".join(vlc))

    with open("spzone_tivimate.m3u8","w",encoding="utf8") as f:
        f.write("\n".join(tiv))

    log.info("Playlists written successfully")


# -------------------------------------------------
# API REFRESH
# -------------------------------------------------

async def refresh_api_cache(now_ts):

    api_data = [{"timestamp": now_ts}]

    r = await network.request(API_URL, log=log)

    if r:
        api_data = r.json().get("matches", [])

        if api_data:
            for event in api_data:
                event["ts"] = event.pop("timestamp")

        api_data[-1]["timestamp"] = now_ts

    return api_data


# -------------------------------------------------
# EVENTS
# -------------------------------------------------

async def get_events(cached_keys):

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False,index=-1)):

        log.info("Refreshing API cache")

        api_data = await refresh_api_cache(now.timestamp())

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(hours=-12)
    end_dt = now.delta(hours=12)

    for g in api_data:

        sport = g.get("league")
        t1 = g.get("team1")
        t2 = g.get("team2")

        if not (sport and t1 and t2):
            continue

        event = f"{t1} vs {t2}"

        if f"[{sport}] {event} ({TAG})" in cached_keys:
            continue

        if not (event_ts := g.get("ts")):
            continue

        event_dt = Time.from_ts(int(str(event_ts)[:-3]))

        if not start_dt <= event_dt <= end_dt:
            continue

        if not (channels := g.get("channels")):
            continue

        if not (links := channels[0].get("links")):
            continue

        url = links[0]

        events.append({
            "sport": sport,
            "event": event,
            "link": url
        })

    return events


# -------------------------------------------------
# SCRAPER
# -------------------------------------------------

async def scrape(browser):

    cached_urls = CACHE_FILE.load()

    valid_urls = {k:v for k,v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "sportzone"')

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        async with network.event_context(browser, stealth=False) as context:

            for i,ev in enumerate(events,start=1):

                async with network.event_page(context) as page:

                    link = ev["link"]

                    try:
                        await page.goto(link, wait_until="domcontentloaded")

                        await page.mouse.move(400,300)
                        await page.mouse.click(400,300)

                        await page.wait_for_timeout(4000)

                    except:
                        pass

                    handler = partial(
                        network.process_event,
                        url=link,
                        url_num=i,
                        page=page,
                        log=log
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log
                    )

                    sport = ev["sport"]
                    event = ev["event"]

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id,logo = leagues.get_tvg_info(sport,event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://vividmosaica.com/",
                        "timestamp": now.timestamp(),
                        "id": tvg_id or "Live.Event.us",
                        "link": link
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1
                        urls[key] = entry

        log.info(f"Collected and cached {valid_count-cached_count} new event(s)")

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

        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-dev-shm-usage"]
        )

        await scrape(browser)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
