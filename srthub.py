import asyncio
import os
import re
import urllib.parse
from functools import partial
from urllib.parse import urljoin
from datetime import datetime, timedelta

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SRTHUB"

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
        # "68c02a4465113",  # American Football
        # "68c02a446582f",  # Baseball
        "68c02a4466011",  # Basketball
        "68c02a4466f56",  # Hockey
        # "68c02a44674e9",  # MMA
        # "68c02a4467a48",  # Racing
        "68c02a4464a38",  # Soccer
        # "68c02a4468cf7",  # Tennis
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

def extract_time_from_text(text: str) -> float:
    """Extract event time from text (e.g., '04:00', 'starts in: 1 hours 05 min')"""
    now = datetime.now()
    
    # Check for time format like "04:00"
    time_match = re.search(r'(\d{1,2}):(\d{2})', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        event_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If the time has passed, assume it's for tomorrow
        if event_time < now:
            event_time = event_time + timedelta(days=1)
        
        return event_time.timestamp()
    
    # Check for "starts in: X hours Y min" format
    hours_match = re.search(r'starts in:\s*(\d+)\s*hours?\s*(\d+)?', text)
    if hours_match:
        hours = int(hours_match.group(1))
        minutes = int(hours_match.group(2)) if hours_match.group(2) else 0
        event_time = now + timedelta(hours=hours, minutes=minutes)
        return event_time.timestamp()
    
    # Check for "ends in: X hours Y min" format (for live events)
    ends_match = re.search(r'ends in:\s*(\d+)\s*hours?\s*(\d+)?', text)
    if ends_match:
        # These are currently live, so use current time
        return now.timestamp()
    
    # Default to current time for live events
    if "LIVE" in text:
        return now.timestamp()
    
    return now.timestamp()

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
        
        # Skip if no URL
        if not url:
            continue
        
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
        # Use a more lenient wait condition - don't wait for networkidle
        # Set a longer timeout and use domcontentloaded which is faster
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        
        # Wait a bit for dynamic content to load
        await page.wait_for_timeout(5000)
        
        # Get page content and parse with selectolax
        content = await page.content()
        soup = HTMLParser(content)
        
        # Look for all text content that might contain events
        page_text = soup.text()
        log.info(f"Page title: {soup.css_first('title').text() if soup.css_first('title') else 'No title'}")
        
        # Find all elements that might contain event information
        # Based on the actual site content, events are in divs with specific classes or patterns
        event_containers = []
        
        # Try different selectors that might be present on the site
        for selector in ['div', 'article', 'section', '.event', '.match', '.game', '.fixture']:
            elements = soup.css(selector)
            if elements:
                event_containers.extend(elements)
        
        # If no specific elements found, just look for any element containing "vs." and time
        if not event_containers:
            # Get all div elements
            event_containers = soup.css('div')
        
        current_sport = "Live Events"
        processed_keys = set()  # To avoid duplicates
        
        for element in event_containers:
            try:
                text = element.text(strip=True)
                if not text or len(text) < 10:  # Skip very short text
                    continue
                
                # Check for sport section headers
                if "UEFA Champions League" in text:
                    current_sport = "UEFA Champions League"
                    continue
                elif "Argentina Liga Profesional" in text:
                    current_sport = "Argentina Liga Profesional"
                    continue
                elif "Liga Profesional" in text:
                    current_sport = "Liga Profesional"
                    continue
                
                # Look for "vs." which indicates a match
                if "vs." in text and ("LIVE" in text or "starts in" in text or "ends in" in text or ":" in text):
                    # Extract team names
                    parts = text.split("vs.")
                    if len(parts) >= 2:
                        home = parts[0].strip()
                        # Clean up the away team
                        away_raw = parts[1].strip()
                        
                        # Remove time and status information
                        for pattern in [r'\d{2}:\d{2}', r'LIVE', r'starts in.*', r'ends in.*', r'Event ended']:
                            away_raw = re.sub(pattern, '', away_raw, flags=re.IGNORECASE).strip()
                        
                        # If away_raw is empty, try to extract from the full text
                        if not away_raw:
                            # Try to find team names in the full text
                            teams = re.findall(r'([A-Za-z\s]+)\s+vs\.\s+([A-Za-z\s]+)', text)
                            if teams:
                                home, away_raw = teams[0]
                        
                        if away_raw:
                            event_name = f"{home} vs {away_raw}"
                            
                            # Create a unique key
                            key = f"[{current_sport}] {event_name} ({TAG})"
                            
                            # Skip if already processed
                            if key in processed_keys:
                                continue
                            
                            # Extract event time
                            event_ts = extract_time_from_text(text)
                            
                            # Look for links in or near this element
                            event_url = None
                            
                            # Check if element itself has a link
                            link = element.css_first('a')
                            if link:
                                href = link.attributes.get("href", "")
                                if href:
                                    event_url = urljoin(BASE_URL, href)
                            
                            # If no link found, look for any link in parent
                            if not event_url:
                                parent = element.parent
                                if parent:
                                    parent_link = parent.css_first('a')
                                    if parent_link:
                                        href = parent_link.attributes.get("href", "")
                                        if href:
                                            event_url = urljoin(BASE_URL, href)
                            
                            # If still no link, look for any link on the page with event in the URL
                            if not event_url:
                                all_links = soup.css('a')
                                for link in all_links:
                                    href = link.attributes.get("href", "")
                                    if href and ('event' in href.lower() or 'match' in href.lower() or 'game' in href.lower()):
                                        # Check if this link is near our event text
                                        link_text = link.text(strip=True)
                                        if home in link_text or away_raw in link_text:
                                            event_url = urljoin(BASE_URL, href)
                                            break
                            
                            if event_url:
                                # Try to get logo based on sport
                                tvg_id, logo = leagues.get_tvg_info(current_sport, event_name)
                                
                                events.append({
                                    "key": key,
                                    "sport": current_sport,
                                    "event": event_name,
                                    "link": event_url,
                                    "event_ts": event_ts,
                                    "timestamp": datetime.now().timestamp(),
                                    "tvg_id": tvg_id or "Live.Event.us",
                                    "logo": logo
                                })
                                
                                processed_keys.add(key)
                                log.info(f"Found event: {key} at {datetime.fromtimestamp(event_ts)}")
            except Exception as e:
                log.debug(f"Error processing element: {e}")
                continue
        
        log.info(f"Found {len(events)} events on main page")
        
    except Exception as e:
        log.error(f"Error scraping main page: {e}")
        # Try to take a screenshot for debugging
        try:
            await page.screenshot(path="error_screenshot.png")
            log.info("Screenshot saved as error_screenshot.png")
        except:
            pass
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
                log.info(f"Processing event {i}/{len(events)}: {ev['key']}")
                
                handler = partial(
                    network.process_event,
                    url=ev["link"],
                    url_num=i,
                    page=page,
                    timeout=15,  # Increased timeout
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
                else:
                    log.warning(f"Failed to get URL for: {ev['key']}")
    
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
        new_events = []
        for ev in live_events:
            if ev["key"] not in cached_urls:
                new_events.append(ev)
            else:
                log.info(f"Event already in cache: {ev['key']}")
        
        if new_events:
            log.info(f"Found {len(new_events)} new events to process")
            
            # Scrape URLs for new events
            new_urls = await scrape_event_urls(browser, new_events)
            
            # Update caches
            for key, entry in new_urls.items():
                cached_urls[key] = entry
                if entry.get("url"):
                    urls[key] = entry
                    log.info(f"Added new event: {key}")
        else:
            log.info("No new events found - all events already in cache")
    else:
        log.info("No events found on main page")
    
    # Save updated cache
    CACHE_FILE.write(cached_urls)
    log.info(f"Cache updated with {len(cached_urls)} total events")
    
    # Generate output files
    generate_output_files()


async def main():
    """Main function to run the scraper"""
    log.info("Starting SrtHub scraper")
    
    # Validate BASE_URL
    if not BASE_URL or BASE_URL == "None":
        log.error("SRTHUB_BASE_URL environment variable is not set correctly")
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
