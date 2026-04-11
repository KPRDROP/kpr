import asyncio
import os
import urllib.parse
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "CDNTV"

CACHE_FILE = Cache(TAG, exp=10_800)

API_CACHE = Cache(f"{TAG}-api", exp=19_800)

# Get API_URL from environment variable (secret) with validation
API_URL = os.environ.get("CDNTV_SP_API_URL")
# Ensure URL has protocol
if API_URL and not API_URL.startswith(('http://', 'https://')):
    API_URL = f"https://{API_URL}"

# Constants for output files
VLC_OUTPUT_FILE = "cdntv_vlc.m3u8"
TIVIMATE_OUTPUT_FILE = "cdntv_tivimate.m3u8"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
REFERER = "https://cdnlivetv.tv/"
ORIGIN = "https://cdnlivetv.tv"

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
        # Parse sport from key format "[Sport] Event (TAG)"
        sport_match = key.split("[")[1].split("]")[0] if "[" in key else "Live Events"
        sport = sport_match
        event_name = key.split("]")[-1].strip().replace(f"({TAG})", "").strip() if "]" in key else key
        logo = data.get("logo", "")
        tvg_id = data.get("id", "Live.Event.us")
        url = data.get("url", "")
        link = data.get("link", "")
        
        # CRITICAL FIX: Keep the full URL with token parameters - do NOT split on '?'
        # The token and signature are essential for playback
        full_url = url
        
        # Skip if no URL
        if not full_url:
            continue
        
        # For VLC referer, use the player page URL which contains the channel info
        vlc_referer = link if link else REFERER
        
        # EXTINF line (same for both formats)
        extinf = f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" tvg-name="{key}" tvg-logo="{logo}" group-title="{sport}",{event_name}\n'
        
        # VLC format
        vlc_content += extinf
        vlc_content += f"#EXTVLCOPT:http-referrer={vlc_referer}\n"
        vlc_content += f"#EXTVLCOPT:http-origin={ORIGIN}\n"
        vlc_content += f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n"
        vlc_content += f"{full_url}\n\n"
        
        # TiviMate format (with pipe and encoded user agent)
        encoded_ua = encode_user_agent(USER_AGENT)
        tivimate_url = f"{full_url}|referer={REFERER}|origin={ORIGIN}|user-agent={encoded_ua}"
        
        tivimate_content += extinf
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

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())
    
    events = []
    
    api_data = API_CACHE.load(per_entry=False)
    
    if not api_data:
        log.info("Refreshing API cache")
        
        api_url = urljoin(API_URL, "api/v1/events/sports")
        log.info(f"Fetching from API: {api_url}")
        
        if r := await network.request(
            api_url,
            log=log,
            params={"user": "cdnlivetv", "plan": "free"},
            headers={
                "Referer": REFERER,
                "Origin": ORIGIN,
                "User-Agent": USER_AGENT
            }
        ):
            try:
                api_data = r.json().get("cdn-live-tv", {})
                if api_data:
                    api_data["timestamp"] = now.timestamp()
                log.info(f"API returned data with {len(api_data)} sports categories")
            except Exception as e:
                log.error(f"Error parsing API response: {e}")
                api_data = {"timestamp": now.timestamp()}
        else:
            log.error("Failed to fetch from API")
            api_data = {"timestamp": now.timestamp()}
        
        API_CACHE.write(api_data)
    
    # Extended time window to capture more events (2 hours before to 2 hours after)
    start_dt = now.delta(minutes=-120)
    end_dt = now.delta(minutes=120)
    
    # Filter out metadata keys (like 'timestamp')
    sports = [key for key in api_data.keys() if key != "timestamp" and not key.startswith("_")]
    
    log.info(f"Found {len(sports)} sport categories in API data")
    
    for sport in sports:
        event_info = api_data.get(sport, [])
        
        if not isinstance(event_info, list):
            log.debug(f"Skipping non-list data for sport {sport}")
            continue
            
        log.info(f"Processing {len(event_info)} events for sport: {sport}")
        
        for event in event_info:
            try:
                t1 = event.get("awayTeam", "")
                t2 = event.get("homeTeam", "")
                
                if not (t1 and t2):
                    continue
                
                # Clean team names
                t1 = t1.strip()
                t2 = t2.strip()
                
                name = f"{t1} vs {t2}"
                
                league = event.get("tournament", sport)
                
                key = f"[{league}] {name} ({TAG})"
                
                if key in cached_keys:
                    log.debug(f"Event already in cache: {key}")
                    continue
                
                # Parse event time
                event_time_str = event.get("start", "")
                if not event_time_str:
                    log.debug(f"No start time for event: {key}")
                    continue
                
                try:
                    event_dt = Time.from_str(event_time_str, timezone="UTC")
                except Exception as e:
                    log.error(f"Error parsing time '{event_time_str}': {e}")
                    continue
                
                # Check if event is within our time window
                if not (start_dt <= event_dt <= end_dt):
                    log.debug(f"Event outside time window: {key} at {event_dt}")
                    continue
                
                # Get channels
                channels = event.get("channels", [])
                if not channels:
                    log.debug(f"No channels for event: {key}")
                    continue
                
                # Find the first valid channel URL
                event_links = []
                for channel in channels:
                    channel_url = channel.get("url", "")
                    if channel_url and isinstance(channel_url, str) and channel_url.startswith(('http://', 'https://')):
                        event_links.append(channel_url)
                
                if not event_links:
                    log.debug(f"No valid channel URLs for event: {key}")
                    continue
                
                link = event_links[0]
                
                events.append({
                    "sport": league,
                    "event": name,
                    "link": link,
                    "timestamp": event_dt.timestamp(),
                })
                
                log.info(f"Found new event: {key} at {event_dt}")
                
            except Exception as e:
                log.error(f"Error processing event: {e}")
                continue
    
    log.info(f"Total new events found: {len(events)}")
    return events


async def scrape(browser: Browser) -> None:
    """Main scraping function"""
    # Load cached URLs
    cached_urls = CACHE_FILE.load() or {}
    
    cached_count = len(cached_urls)
    
    # Update global urls with cached ones
    urls.update(cached_urls)
    
    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{API_URL}"')
    
    if events := await get_events(list(cached_urls.keys())):
        log.info(f"Processing {len(events)} new URL(s)")
        
        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    log.info(f"Processing event {i}/{len(events)}: {ev['sport']} - {ev['event']}")
                    
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        log=log,
                        timeout=15,
                    )
                    
                    # CRITICAL FIX: Get the full URL with token from the event page
                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )
                    
                    if url:
                        sport, event, ts = (
                            ev["sport"],
                            ev["event"],
                            ev["timestamp"],
                        )
                        
                        key = f"[{sport}] {event} ({TAG})"
                        
                        tvg_id, logo = leagues.get_tvg_info(sport, event)
                        
                        # CRITICAL FIX: Keep the full URL with token - do NOT split
                        # The token and signature are in the URL parameters
                        full_url = url
                        
                        entry = {
                            "url": full_url,  # Store the full URL with token
                            "logo": logo,
                            "base": REFERER,
                            "timestamp": ts,
                            "id": tvg_id or "Live.Event.us",
                            "link": link,  # Store the original channel link for referer
                        }
                        
                        urls[key] = cached_urls[key] = entry
                        log.info(f"Successfully added URL for: {key}")
                    else:
                        log.warning(f"Failed to get URL for event: {ev['sport']} - {ev['event']}")
        
        log.info(f"Collected and cached {len(cached_urls) - cached_count} new event(s)")
    
    else:
        log.info("No new events found")
    
    # Save updated cache
    CACHE_FILE.write(cached_urls)
    
    # Generate output files
    generate_output_files()


async def main():
    """Main function to run the updater"""
    log.info("Starting CDNTV updater")
    
    # Validate API_URL
    if not API_URL or API_URL == "None":
        log.error("CDNTV_SP_API_URL environment variable is not set correctly")
        return
    
    log.info(f"Using API URL: {API_URL}")
    
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            await scrape(browser)
        finally:
            await browser.close()
    
    log.info("CDNTV updater completed")


def run():
    """Synchronous entry point for the updater"""
    asyncio.run(main())


if __name__ == "__main__":
    run()
