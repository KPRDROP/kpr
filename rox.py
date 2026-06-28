#!/usr/bin/env python3

import asyncio
from functools import partial
from urllib.parse import urljoin, quote
from datetime import datetime

from playwright.async_api import Browser, Page, TimeoutError
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "ROXIE"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://roxiestreams.info"

# Output files
VLC_OUTPUT = "rox_vlc.m3u8"
TIVIMATE_OUTPUT = "rox_tivimate.m3u8"

# Headers
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"
TIVIMATE_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"
REFERER = BASE_URL
ORIGIN = BASE_URL

SPORT_URLS = {
    "Racing": urljoin(BASE_URL, "motorsports"),
} | {
    sport: urljoin(BASE_URL, sport.lower())
    for sport in [
        "Fighting",
        "MLB",
        "Soccer",
        "NBA",
        "NFL",
        "NHL",
    ]
}


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> str | None:

    try:
        resp = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=6_000,
        )

        if not resp or resp.status != 200:
            log.warning(
                f"URL {url_num}) Status Code: {resp.status if resp else 'None'}"
            )
            return

        try:
            btn = page.locator("button.streambutton").first

            await btn.dblclick(force=True, timeout=3_000)

            await page.wait_for_function(
                "() => typeof clapprPlayer !== 'undefined'",
                timeout=6_000,
            )

            stream = await page.evaluate("() => clapprPlayer.options.source")
        except TimeoutError:
            log.warning(f"URL {url_num}) Could not find Clappr source")
            return

        log.info(f"URL {url_num}) Captured M3U8")
        return stream

    except Exception as e:
        log.warning(f"URL {url_num}) {e}")
        return


async def get_events() -> list[dict[str, str]]:
    tasks = [network.request(url, log=log) for url in SPORT_URLS.values()]

    results = await asyncio.gather(*tasks)

    events = []

    if not (
        soups := [(HTMLParser(html.content), html.url) for html in results if html]
    ):
        return events

    now = Time.clean(Time.now())

    for soup, url in soups:
        sport = next((k for k, v in SPORT_URLS.items() if v == url), "Live Event")

        for row in soup.css("table#eventsTable tbody tr"):
            if not (a_tag := row.css_first("td a")):
                continue

            event = a_tag.text(strip=True)

            if not (href := a_tag.attributes.get("href")):
                continue

            if not (event_time_elem := row.css_first("td.event-start-time")):
                continue

            event_dt = Time.from_str(event_time_elem.text(strip=True), timezone="EST")

            if event_dt.date() != now.date():
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event,
                    "link": urljoin(BASE_URL, href),
                    "timestamp": now.timestamp(),
                }
            )

    return events


async def scrape(browser: Browser) -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["url"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")
        return

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    sport, event, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["timestamp"],
                    )

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    key = f"[{sport}] {event} ({TAG})"

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": BASE_URL,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        urls[key] = entry

        log.info(f"Collected and cached {len(urls)} event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)


def generate_playlists() -> None:
    """Generate VLC and TiviMate playlist files from collected events"""
    if not urls:
        log.warning("No events to generate playlists")
        # Create empty playlists
        with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n# No events available\n")
        with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n# No events available\n")
        return

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n# Total Streams: {len(urls)}\n\n'

    # Generate VLC playlist
    try:
        with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
            f.write(header)
            
            ch_no = 1
            for event_name, event_data in urls.items():
                url = event_data.get("url")
                logo = event_data.get("logo", "https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png")
                tvg_id = event_data.get("id", "Live.Event.us")
                
                if not url:
                    continue
                
                # Write VLC format with EXTVLCOPT lines
                f.write(f'#EXTINF:-1 tvg-chno="{ch_no}" tvg-id="{tvg_id}" tvg-name="{event_name}" tvg-logo="{logo}" group-title="Live Events",{event_name}\n')
                f.write(f'#EXTVLCOPT:http-referrer={REFERER}\n')
                f.write(f'#EXTVLCOPT:http-origin={ORIGIN}\n')
                f.write(f'#EXTVLCOPT:http-user-agent={USER_AGENT}\n')
                f.write(f'{url}\n\n')
                
                ch_no += 1
        
        log.info(f"Generated VLC playlist: {VLC_OUTPUT} with {ch_no - 1} streams")
    except Exception as e:
        log.error(f"Error generating VLC playlist: {e}")

    # Generate TiviMate playlist
    try:
        ua_enc = quote(TIVIMATE_USER_AGENT, safe="")
        referer_enc = quote(REFERER, safe="")
        origin_enc = quote(ORIGIN, safe="")
        
        with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
            f.write(header)
            
            ch_no = 1
            for event_name, event_data in urls.items():
                url = event_data.get("url")
                logo = event_data.get("logo", "https://i.gyazo.com/4a5e9fa2525808ee4b65002b56d3450e.png")
                tvg_id = event_data.get("id", "Live.Event.us")
                
                if not url:
                    continue
                
                # Write TiviMate format with pipe-separated headers
                f.write(f'#EXTINF:-1 tvg-chno="{ch_no}" tvg-id="{tvg_id}" tvg-name="{event_name}" tvg-logo="{logo}" group-title="Live Events",{event_name}\n')
                f.write(f'{url}|referer={referer_enc}|origin={origin_enc}|user-agent={ua_enc}\n\n')
                
                ch_no += 1
        
        log.info(f"Generated TiviMate playlist: {TIVIMATE_OUTPUT} with {ch_no - 1} streams")
    except Exception as e:
        log.error(f"Error generating TiviMate playlist: {e}")


async def main() -> None:
    """Main function to run the scraper and generate playlists"""
    log.info("Starting ROXIE playlist generator")
    
    try:
        # Launch browser and scrape
        async with network.playwright_manager() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                await scrape(browser)
            finally:
                await browser.close()
        
        # Generate playlists
        generate_playlists()
        
        log.info("Playlist generation completed")
        print(f"\n ROXIE Playlists generated successfully!")
        print(f"    VLC: {VLC_OUTPUT}")
        print(f"    TiviMate: {TIVIMATE_OUTPUT}")
        print(f"    Total streams: {len(urls)}")
    except Exception as e:
        log.error(f"Error in main execution: {e}")
        print(f"\n Error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
