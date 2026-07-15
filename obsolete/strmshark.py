import os
import re
import sys
import asyncio
from urllib.parse import quote
from functools import partial

from selectolax.parser import HTMLParser
from playwright.async_api import async_playwright

# Fix imports when run as script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "SHARK"

BASE_URL = os.getenv("SHARK_BASE_URL")
if not BASE_URL:
    raise RuntimeError("SHARK_BASE_URL secret not set")

OUTPUT_FILE = "strmshark_tivimate.m3u8"

USER_AGENT_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
USER_AGENT = quote(USER_AGENT_RAW, safe="")

CACHE_FILE = Cache("shark.json", exp=10800)


# ---------------- JS RENDER ----------------

async def fetch_rendered_html() -> str | None:
    log.info(f"Launching browser for JS-rendered HTML from {BASE_URL}")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox", 
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                ],
            )
            
            try:
                context = await browser.new_context(
                    user_agent=USER_AGENT_RAW,
                    viewport={"width": 1920, "height": 1080},
                )
                page = await context.new_page()

                # Set default timeout
                page.set_default_timeout(45000)
                
                # Try to navigate with timeout
                try:
                    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    log.error(f"Failed to load page: {str(e)[:100]}")
                    await browser.close()
                    return None
                
                # Wait for content with fallback
                try:
                    await page.wait_for_selector("a.hd-link.secondary", timeout=15000)
                except:
                    log.warning("No .hd-link.secondary found, checking for alternative content")
                    try:
                        await page.wait_for_selector(".row", timeout=10000)
                    except:
                        log.warning("No .row found, page might be empty or different structure")
                        # Try to wait for any content
                        await page.wait_for_timeout(5000)
                
                # Wait a bit more for dynamic content
                await page.wait_for_timeout(2000)
                
                html = await page.content()
                await browser.close()
                
                # Validate HTML has content
                if not html or len(html) < 100:
                    log.error("Retrieved HTML is too short, likely empty")
                    return None
                    
                log.info(f"Successfully retrieved HTML ({len(html)} bytes)")
                return html
                
            except Exception as e:
                log.error(f"Error during page rendering: {str(e)[:100]}")
                await browser.close()
                return None
                
    except Exception as e:
        log.error(f"Failed to launch browser: {str(e)[:100]}")
        return None


async def fetch_stream(api_url: str) -> str | None:
    """Fetch stream URL from API endpoint"""
    try:
        r = await network.request(api_url, log=log, timeout=20)
        if not r:
            return None

        try:
            data = r.json()
            urls = data.get("urls")
            if not urls or not isinstance(urls, list):
                return None
            
            # Return first valid URL
            stream_url = urls[0]
            if stream_url and isinstance(stream_url, str):
                # Clean URL if needed
                return stream_url.split("?st")[0] if "?st" in stream_url else stream_url
            
            return None
        except Exception as e:
            log.error(f"Failed to parse stream data: {str(e)[:50]}")
            return None
    except Exception as e:
        log.error(f"Failed to fetch stream: {str(e)[:50]}")
        return None


# ---------------- UPDATER ----------------

async def scrape_events() -> dict:
    html = await fetch_rendered_html()
    if not html:
        log.error("Failed to retrieve HTML content")
        return {}

    try:
        soup = HTMLParser(html)
    except Exception as e:
        log.error(f"Failed to parse HTML: {str(e)[:100]}")
        return {}

    events = {}
    now = Time.clean(Time.now())
    
    # Extended time window to catch more events
    start_dt = now.delta(hours=-24)
    end_dt = now.delta(hours=12)

    # Find all event rows
    rows = soup.css(".row")
    if not rows:
        log.warning("No .row elements found in HTML")
        # Try alternative selectors
        rows = soup.css(".ch-row") or soup.css(".event-row") or soup.css("[class*='row']")
        if not rows:
            log.warning("No event rows found with alternative selectors either")
            return events
    
    log.info(f"Found {len(rows)} rows to process")

    for row in rows:
        try:
            # Extract event nodes - try different selectors
            date_node = row.css_first(".ch-date") or row.css_first(".date")
            cat_node = row.css_first(".ch-category") or row.css_first(".category") or row.css_first(".sport")
            name_node = row.css_first(".ch-name") or row.css_first(".name") or row.css_first(".event-name")
            
            if not (date_node and cat_node and name_node):
                continue

            # Parse date
            try:
                date_text = date_node.text(strip=True)
                if not date_text:
                    continue
                event_dt = Time.from_str(date_text, timezone="EST")
            except Exception as e:
                log.debug(f"Failed to parse date: {str(e)[:30]}")
                continue

            # Check if event is within time window
            if not (start_dt <= event_dt <= end_dt):
                continue

            sport = cat_node.text(strip=True)
            event_name = name_node.text(strip=True)

            # Find embed button
            embed_btn = row.css_first("a.hd-link.secondary") or row.css_first(".hd-link") or row.css_first("a[onclick*='openEmbed']")
            if not embed_btn:
                continue

            onclick = embed_btn.attributes.get("onclick", "")
            if not onclick:
                continue

            # Extract player URL
            pattern = re.compile(r"openEmbed\('([^']+)'\)", re.I)
            match = pattern.search(onclick)
            if not match:
                continue

            player_url = match.group(1)
            api_url = player_url.replace("player.php", "get-stream.php")
            
            # Ensure full URL
            if not api_url.startswith("http"):
                api_url = f"{BASE_URL}/{api_url.lstrip('/')}"

            key = f"[{sport}] {event_name} ({TAG})"

            # Check if already in cache
            if key in events:
                log.debug(f"Skipping duplicate: {key}")
                continue

            events[key] = {
                "sport": sport,
                "event": event_name,
                "event_ts": event_dt.timestamp(),
                "api": api_url,
                "timestamp": event_dt.timestamp(),
            }
        except Exception as e:
            log.debug(f"Error processing row: {str(e)[:50]}")
            continue

    log.info(f"📺 Parsed {len(events)} events from rendered DOM")
    return events


# ---------------- OUTPUT ----------------

def build_playlist(events: dict) -> str:
    lines = ["#EXTM3U"]
    
    if not events:
        log.warning("No events to build playlist")
        return "#EXTM3U\n"

    # Sort by timestamp
    sorted_events = sorted(
        events.items(), 
        key=lambda x: x[1].get("event_ts", x[1].get("timestamp", 0))
    )

    for title, ev in sorted_events:
        # Skip events without URL
        if "url" not in ev or not ev["url"]:
            continue
            
        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        name = f"[{ev['sport']}] {ev['event']} ({TAG})"

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id or "Live.Event.us"}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{logo}" '
            f'group-title="Live Events",{name}'
        )

        # Add stream URL with parameters
        stream_url = ev["url"]
        lines.append(
            f'{stream_url}'
            f'|referer={BASE_URL}'
            f'|origin={BASE_URL}'
            f'|user-agent={USER_AGENT}'
        )

    return "\n".join(lines) + "\n"


async def process_event_with_timeout(api_url: str, url_num: int) -> str | None:
    """Process a single event with timeout handling"""
    try:
        return await asyncio.wait_for(
            fetch_stream(api_url),
            timeout=30
        )
    except asyncio.TimeoutError:
        log.warning(f"URL {url_num}) Timeout fetching stream")
        return None
    except Exception as e:
        log.error(f"URL {url_num}) Error: {str(e)[:50]}")
        return None


async def main():
    log.info("Starting SharkStreams updater")
    
    cached = CACHE_FILE.load() or {}
    log.info(f"Loaded {len(cached)} cached events")

    events = await scrape_events()
    
    if not events:
        log.warning("No events found, using cached events if available")
        if cached:
            log.info(f"Using {len(cached)} cached events")
            playlist = build_playlist(cached)
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(playlist)
            log.info(f"Saved {OUTPUT_FILE} with {len(cached)} cached entries")
        else:
            # Create empty playlist
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
            log.info(f"Saved empty {OUTPUT_FILE}")
        return

    log.info(f"Processing {len(events)} events")

    processed_count = 0
    failed_count = 0

    # Process events concurrently with semaphore
    semaphore = asyncio.Semaphore(3)  # Limit concurrent requests
    
    async def process_single(key: str, ev: dict) -> tuple[str, dict, bool]:
        nonlocal processed_count, failed_count
        
        async with semaphore:
            # Check if already cached and not expired
            if key in cached:
                cached_entry = cached[key]
                # Keep if not too old (within 6 hours)
                if cached_entry.get("timestamp", 0) > Time.now().timestamp() - 21600:
                    log.debug(f"Skipping {key} - already cached")
                    return key, cached_entry, False
            
            stream = await process_event_with_timeout(ev["api"], processed_count + 1)
            
            if stream:
                ev["url"] = stream
                ev["timestamp"] = Time.now().timestamp()
                processed_count += 1
                return key, ev, True
            else:
                failed_count += 1
                return key, None, False

    # Process all events
    tasks = []
    for key, ev in events.items():
        tasks.append(process_single(key, ev))
    
    if tasks:
        results = await asyncio.gather(*tasks)
        
        # Update cache with results
        updated_count = 0
        for key, ev, success in results:
            if success and ev:
                cached[key] = ev
                updated_count += 1
            elif not success and key in cached:
                # Keep existing entry if it exists
                pass

    # Clean old cache entries (older than 48 hours)
    if cached:
        now_ts = Time.now().timestamp()
        expired_keys = []
        for key, ev in cached.items():
            if ev.get("timestamp", 0) < now_ts - 172800:  # 48 hours
                expired_keys.append(key)
        
        if expired_keys:
            log.info(f"Removing {len(expired_keys)} expired cache entries")
            for key in expired_keys:
                del cached[key]

    CACHE_FILE.write(cached)

    playlist = build_playlist(cached)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(playlist)

    log.info(f"Saved {OUTPUT_FILE} ({len(cached)} entries)")
    log.info(f"Processed {processed_count} new events, {failed_count} failed")


if __name__ == "__main__":
    asyncio.run(main())
