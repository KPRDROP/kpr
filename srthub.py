import asyncio
import os
import re
import urllib.parse
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SRTMHUB"

CACHE_FILE = Cache(TAG, exp=10_800)

HTML_CACHE = Cache(f"{TAG}-html", exp=19_800)

# Get BASE_URL from environment variable (secret) with validation
BASE_URL = os.environ.get("SRTHUB_BASE_URL")
# Ensure URL has protocol
if BASE_URL and not BASE_URL.startswith(('http://', 'https://')):
    BASE_URL = f"https://{BASE_URL}"

# Sports mapping based on actual website content
SPORT_ENDPOINTS = [
    f"sport_{sport_id}"
    for sport_id in [
        "68c02a446582f",
        "68c02a4466011",
        "68c02a4466f56",
        "68c02a44674e9",
        "68c02a4467a48",
        "68c02a4464a38",
        "68c02a4468cf7",
        "68c02a4469422",
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

def detect_sport(title: str) -> str:
    """Detect sport from event title"""
    for keyword, sport in SPORT_KEYWORDS.items():
        if keyword.lower() in title.lower():
            return sport
    return "Live Events"

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
        sport = detect_sport(key)
        event_name = key
        logo = data.get("logo", "")
        tvg_id = data.get("id", "Live.Event.us")
        url = data.get("url", "")
        base = data.get("base", REFERER)
        
        # Clean URL - remove any query parameters that might cause issues
        url = url.split('?')[0] if url else ""
        
        # EXTINF line (same for both formats)
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
        log.info(f"Successfully wrote {VLC_OUTPUT_FILE} with {chno-1} events")
    except Exception as e:
        log.error(f"Error writing {VLC_OUTPUT_FILE}: {e}")
    
    # Write TiviMate file
    try:
        with open(TIVIMATE_OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(tivimate_content)
        log.info(f"Successfully wrote {TIVIMATE_OUTPUT_FILE} with {chno-1} events")
    except Exception as e:
        log.error(f"Error writing {TIVIMATE_OUTPUT_FILE}: {e}")

async def scrape_live_events(browser: Browser) -> list[dict]:
    """Scrape live events directly from the main page"""
    events = []
    
    log.info(f"Fetching main page from {BASE_URL}")
    
    page = await browser.new_page()
    try:
        # Set a reasonable timeout and wait for content
        await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
        
        # Get page content and parse with selectolax
        content = await page.content()
        soup = HTMLParser(content)
        
        # Look for event containers - based on actual website structure
        # The site shows events with team names and times
        event_elements = soup.css("div.flex.items-center.justify-between, div.bg-gray-50, div.rounded-lg")
        
        current_sport = "Live Events"
        
        for element in event_elements:
            text = element.text(strip=True)
            
            # Try to detect sport sections
            if "UEFA Champions League" in text or "Argentina Liga" in text:
                current_sport = text
                continue
            
            # Look for vs pattern which indicates a match
            if "vs." in text and "LIVE" in text:
                # Extract team names
                parts = text.split("vs.")
                if len(parts) >= 2:
                    home = parts[0].strip()
                    # Clean up the away team (remove time and status)
                    away_raw = parts[1].strip()
                    # Remove common suffixes
                    for suffix in ["LIVE", "starts in", "ends in", "Event ended"]:
                        if suffix in away_raw:
                            away_raw = away_raw.split(suffix)[0].strip()
                    
                    event_name = f"{home} vs {away_raw}"
                    
                    # Get any links in the event
                    links = element.css("a")
                    event_url = None
                    for link in links:
                        href = link.attributes.get("href", "")
                        if href and "event" in href.lower():
                            event_url = urljoin(BASE_URL, href)
                            break
                    
                    if event_url:
                        key = f"[{current_sport}] {event_name} ({TAG})"
                        
                        # Try to get logo based on sport
                        tvg_id, logo = leagues.get_tvg_info(current_sport, event_name)
                        
                        events.append({
                            "key": key,
                            "sport": current_sport,
                            "event": event_name,
                            "link": event_url,
                            "event_ts": Time.now().timestamp(),  # Use current time for live events
                            "timestamp": Time.now().timestamp(),
                            "tvg_id": tvg_id or "Live.Event.us",
                            "logo": logo
                        })
                        
                        log.info(f"Found live event: {key}")
        
        log.info(f"Found {len(events)} live events on main page")
        
    except Exception as e:
        log.error(f"Error scraping main page: {e}")
    finally:
        await page.close()
    
    return events

async def scrape_event_urls(browser: Browser, events: list[dict]) -> dict:
    """Scrape individual event pages for stream URLs"""
    results = {}
    
    if not events:
        return results
    
    log.info(f"Processing {len(events)} event URLs")
    
    async with network.event_context(browser, stealth=False) as context:
        for i, ev in enumerate(events, start=1):
            async with network.event_page(context) as page:
                handler = partial(
                    network.process_event,
                    url=ev["link"],
                    url_num=i,
                    page=page,
                    timeout=10,  # Increased timeout
                    log=log,
                )
                
                url = await network.safe_process(
                    handler,
                    url_num=i,
                    semaphore=network.PW_S,
                    log=log,
                )
                
                if url:
                    # Clean the URL
                    url = url.split("?")[0]
                    
                    entry = {
                        "url": url,
                        "logo": ev.get("logo", ""),
                        "base": REFERER,
                        "timestamp": ev["event_ts"],
                        "id": ev.get("tvg_id", "Live.Event.us"),
                        "link": ev["link"],
                    }
                    
                    results[ev["key"]] = entry
                    log.info(f"Successfully got URL for: {ev['key']}")
    
    return results

async def scrape(browser: Browser) -> None:
    """Main scraping function"""
    # Load cached URLs
    cached_urls = CACHE_FILE.load() or {}
    
    # Update global urls with cached ones that have valid URLs
    valid_cached = {k: v for k, v in cached_urls.items() if v.get("url")}
    urls.update(valid_cached)
    
    log.info(f"Loaded {len(valid_cached)} valid event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')
    
    # First scrape live events from main page
    live_events = await scrape_live_events(browser)
    
    if live_events:
        # Filter out events we already have cached
        new_events = [ev for ev in live_events if ev["key"] not in cached_urls]
        
        if new_events:
            log.info(f"Found {len(new_events)} new live events to process")
            
            # Scrape URLs for new events
            new_urls = await scrape_event_urls(browser, new_events)
            
            # Update caches
            for key, entry in new_urls.items():
                cached_urls[key] = entry
                if entry.get("url"):
                    urls[key] = entry
                    log.info(f"Added new event: {key}")
        else:
            log.info("No new live events found")
    else:
        log.info("No live events found on main page")
    
    # Save updated cache
    CACHE_FILE.write(cached_urls)
    log.info(f"Cache updated with {len(cached_urls)} total events")
    
    # Generate output files
    generate_output_files()


async def main():
    """Main function to run the scraper"""
    log.info("Starting StreamHub scraper")
    
    # Validate BASE_URL
    if not BASE_URL or BASE_URL == "None":
        log.error("STREAMHUB_BASE_URL environment variable is not set correctly")
        return
    
    log.info(f"Using base URL: {BASE_URL}")
    
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            await scrape(browser)
        finally:
            await browser.close()
    
    log.info("StreamHub scraper completed")


def run():
    """Synchronous entry point for the scraper"""
    asyncio.run(main())


if __name__ == "__main__":
    run()
