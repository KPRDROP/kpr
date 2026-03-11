import asyncio
import os
import urllib.parse
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRHUB"

CACHE_FILE = Cache(TAG, exp=10_800)

HTML_CACHE = Cache(f"{TAG}-html", exp=19_800)

BASE_URL = os.environ.get("SRTHUB_BASE_URL")

SPORT_ENDPOINTS = [
    f"sport_{sport_id}"
    for sport_id in [
        # "68c02a4465113",  # American Football
        "68c02a446582f",  # Baseball
        "68c02a4466011",  # Basketball
        "68c02a4466f56",  # Hockey
        "68c02a44674e9",  # MMA
        "68c02a4467a48",  # Racing
        "68c02a4464a38",  # Soccer
        "68c02a4468cf7",  # Tennis
        "68c02a4469422",  # Volleyball
    ]
]

# Constants for output files
VLC_OUTPUT_FILE = "srthub_vlc.m3u8"
TIVIMATE_OUTPUT_FILE = "srthub_tivimate.m3u8"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
REFERER = "https://storytrench.net/"
ORIGIN = "https://storytrench.net/"

def encode_user_agent(user_agent: str) -> str:
    """Encode user agent for TiviMate format"""
    return urllib.parse.quote(user_agent)

def generate_output_files():
    """Generate both VLC and TiviMate M3U8 files"""
    if not urls:
        log.info("No URLs to write to output files")
        return
    
    log.info(f"Generating output files with {len(urls)} events")
    
    # Generate VLC format
    vlc_content = "#EXTM3U\n"
    tivimate_content = "#EXTM3U\n"
    
    # Sort by timestamp to maintain order
    sorted_urls = sorted(urls.items(), key=lambda x: x[1].get("timestamp", 0))
    
    chno = 1  # Start channel number from 1
    for key, data in sorted_urls:
        if not data.get("url"):
            continue
            
        # Extract data
        sport = key.split("[")[1].split("]")[0] if "[" in key else "Unknown"
        event_name = key.split("]")[-1].strip() if "]" in key else key
        logo = data.get("logo", "")
        tvg_id = data.get("id", "Live.Event.us")
        url = data.get("url", "")
        base = data.get("base", REFERER)
        
        # EXTINF line
        extinf = f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" tvg-name="{key}" tvg-logo="{logo}" group-title="{sport}",{event_name}\n'
        
        # VLC format
        vlc_content += extinf
        vlc_content += f"#EXTVLCOPT:http-referrer={base}\n"
        vlc_content += f"#EXTVLCOPT:http-origin={ORIGIN}\n"
        vlc_content += f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n"
        vlc_content += f"{url}\n\n"
        
        # TiviMate format (with pipe and encoded user agent)
        encoded_ua = encode_user_agent(USER_AGENT)
        tivimate_url = f"{url}|referer={base}|origin={ORIGIN}|user-agent={encoded_ua}"
        
        tivimate_content += extinf
        tivimate_content += f"#EXTVLCOPT:http-referrer={base}\n"
        tivimate_content += f"#EXTVLCOPT:http-origin={ORIGIN}\n"
        tivimate_content += f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n"
        tivimate_content += f"{tivimate_url}\n\n"
        
        chno += 1
    
    # Write VLC file
    try:
        with open(VLC_OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(vlc_content)
        log.info(f"Successfully wrote {VLC_OUTPUT_FILE}")
    except Exception as e:
        log.error(f"Error writing {VLC_OUTPUT_FILE}: {e}")
    
    # Write TiviMate file
    try:
        with open(TIVIMATE_OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(tivimate_content)
        log.info(f"Successfully wrote {TIVIMATE_OUTPUT_FILE}")
    except Exception as e:
        log.error(f"Error writing {TIVIMATE_OUTPUT_FILE}: {e}")

async def refresh_html_cache(
    date: str,
    sport_id: str,
    ts: float,
) -> dict[str, dict[str, str | float]]:
    events = {}
    
    if not (
        html_data := await network.request(
            urljoin(BASE_URL, f"events/{date}"),
            log=log,
            params={"sport_id": sport_id},
        )
    ):
        return events
    
    soup = HTMLParser(html_data.content)
    
    for section in soup.css(".events-section"):
        if not (sport_node := section.css_first(".section-titlte")):
            continue
        
        sport = sport_node.text(strip=True)
        
        for event in section.css(".section-event"):
            event_name = "Live Event"
            
            if teams := event.css_first(".event-competitors"):
                home, away = teams.text(strip=True).split("vs.")
                event_name = f"{away} vs {home}"
            
            if not (event_button := event.css_first(".event-button a")) or not (
                href := event_button.attributes.get("href")
            ):
                continue
            
            event_date = event.css_first(".event-countdown").attributes.get(
                "data-start"
            )
            
            event_dt = Time.from_str(event_date, timezone="UTC")
            
            key = f"[{sport}] {event_name} ({TAG})"
            
            events[key] = {
                "sport": sport,
                "event": event_name,
                "link": href,
                "event_ts": event_dt.timestamp(),
                "timestamp": ts,
            }
    
    return events


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())
    
    if not (events := HTML_CACHE.load()):
        log.info("Refreshing HTML cache")
        
        tasks = [
            refresh_html_cache(
                date,
                sport_id,
                now.timestamp(),
            )
            for date in [now.date(), now.delta(days=1).date()]
            for sport_id in SPORT_ENDPOINTS
        ]
        
        results = await asyncio.gather(*tasks)
        
        events = {k: v for data in results for k, v in data.items()}
        
        HTML_CACHE.write(events)
    
    live = []
    
    start_ts = now.delta(hours=-1).timestamp()
    end_ts = now.delta(minutes=1).timestamp()
    
    for k, v in events.items():
        if k in cached_keys:
            continue
        
        if not start_ts <= v["event_ts"] <= end_ts:
            continue
        
        live.append(v)
    
    return live


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()
    
    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}
    
    valid_count = cached_count = len(valid_urls)
    
    urls.update(valid_urls)
    
    log.info(f"Loaded {cached_count} event(s) from cache")
    
    log.info(f'Scraping from "{BASE_URL}"')
    
    if events := await get_events(list(cached_urls.keys())):
        log.info(f"Processing {len(events)} new URL(s)")
        
        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        timeout=5,
                        log=log,
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
                        ev["event_ts"],
                    )
                    
                    key = f"[{sport}] {event} ({TAG})"
                    
                    tvg_id, logo = leagues.get_tvg_info(sport, event)
                    
                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": REFERER,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }
                    
                    cached_urls[key] = entry
                    
                    if url:
                        valid_count += 1
                        entry["url"] = url.split("?")[0]
                        urls[key] = entry
        
        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")
    
    else:
        log.info("No new events found")
    
    CACHE_FILE.write(cached_urls)
    
    # Generate output files after scraping
    generate_output_files()


async def main():
    """Main function to run the updater"""
    log.info("Starting SrtHub updater")
    
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            await scrape(browser)
        finally:
            await browser.close()
    
    log.info("StreamHub updater completed")


def run():
    """Synchronous entry point for the scraper"""
    asyncio.run(main())


if __name__ == "__main__":
    run()
