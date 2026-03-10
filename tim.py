import asyncio
from functools import partial
from urllib.parse import urljoin, quote
import os
import re

from playwright.async_api import Browser, Page, Response
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIMSTRMS"

CACHE_FILE = Cache(TAG, exp=10_800)

# Base URL from environment variable
BASE_URL = os.environ.get("TIM_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing TIM_BASE_URL secret")

# Output files
VLC_OUTPUT = "tim_vlc.m3u8"
TIVIMATE_OUTPUT = "tim_tivimate.m3u8"

# User agent for Tivimate (encoded)
TIVIMATE_UA = quote("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

# Sport mapping based on common categories
SPORT_KEYWORDS = {
    "Soccer": ["soccer", "football", "fútbol", "calcio", "bundesliga", "premier league", "la liga", "serie a"],
    "Basketball": ["basketball", "nba", "euroleague", "ncaa"],
    "Hockey": ["hockey", "nhl", "khl"],
    "Tennis": ["tennis", "atp", "wta", "grand slam"],
    "Baseball": ["baseball", "mlb"],
    "American Football": ["nfl", "football", "super bowl", "ncaa football"],
    "MMA": ["mma", "ufc", "bellator"],
    "Boxing": ["boxing", "boxe", "fight"],
    "Motorsport": ["f1", "formula", "motogp", "nascar", "racing"],
    "Rugby": ["rugby", "super rugby", "six nations"],
    "Cricket": ["cricket", "ipl", "ashes"],
    "Darts": ["darts", "pdc"],
    "Cycling": ["cycling", "tour de france"],
}


def detect_sport(event_name: str) -> str:
    """Detect sport from event name using keywords"""
    event_lower = event_name.lower()
    for sport, keywords in SPORT_KEYWORDS.items():
        for keyword in keywords:
            if keyword in event_lower:
                return sport
    return "Other"


def sift_xhr(resp: Response) -> bool:
    resp_url = resp.url
    return "hmembeds.one/embed" in resp_url and resp.status == 200


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> tuple[str | None, str | None]:

    nones = None, None

    captured: list[str] = []

    got_one = asyncio.Event()

    handler = partial(
        network.capture_req,
        captured=captured,
        got_one=got_one,
    )

    page.on("request", handler)

    try:
        try:
            async with page.expect_response(sift_xhr, timeout=5_000) as strm_resp:
                resp = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=10_000,
                )

                if not resp or resp.status != 200:
                    log.warning(
                        f"URL {url_num}) Status Code: {resp.status if resp else 'None'}"
                    )
                    return nones

                response = await strm_resp.value
                embed_url = response.url
                log.info(f"URL {url_num}) Found embed URL: {embed_url}")
                
        except TimeoutError:
            log.warning(f"URL {url_num}) No available stream links.")
            return nones

        wait_task = asyncio.create_task(got_one.wait())

        try:
            await asyncio.wait_for(wait_task, timeout=15)
            log.info(f"URL {url_num}) M3U8 capture triggered")
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return nones
        finally:
            if not wait_task.done():
                wait_task.cancel()
                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass

        if captured:
            log.info(f"URL {url_num}) Captured M3U8: {captured[0]}")
            return captured[0], embed_url

        log.warning(f"URL {url_num}) No M3U8 captured after waiting.")
        return nones

    except Exception as e:
        log.warning(f"URL {url_num}) Error: {e}")
        return nones

    finally:
        page.remove_listener("request", handler)


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    log.info(f"Fetching events from {BASE_URL}")
    
    if not (html_data := await network.request(BASE_URL, log=log)):
        log.error("Failed to fetch HTML data")
        return events

    soup = HTMLParser(html_data.content)
    
    # Find all event cards - based on actual website structure
    # The website shows "8 events scheduled" so we need to find those
    
    # Look for the events section
    events_section = None
    
    # Try to find section containing "Live & Upcoming Events"
    for heading in soup.css("h2, h3, h4"):
        if heading.text() and "Live & Upcoming Events" in heading.text():
            events_section = heading.parent
            log.info("Found Live & Upcoming Events section")
            break
    
    if not events_section:
        # Try alternative: look for any container with event cards
        events_section = soup
    
    # Find event cards - they might be in a grid or list
    card_selectors = [
        ".grid > div",
        ".cards > div",
        ".event-card",
        ".match-card",
        ".game-card",
        "main > div > div",
        ".space-y-4 > div",
        "div[class*='grid'] > div",
    ]
    
    cards = []
    for selector in card_selectors:
        cards = events_section.css(selector)
        if cards:
            log.info(f"Found {len(cards)} cards with selector: {selector}")
            break
    
    # If still no cards, try finding by event patterns
    if not cards:
        # Look for elements containing team names vs team names
        for element in soup.css("div, span, p, h3, h4"):
            text = element.text(strip=True)
            if text and " vs " in text or " vs. " in text:
                # This might be an event title
                parent = element.parent
                if parent and parent not in cards:
                    cards.append(parent)
        
        if cards:
            log.info(f"Found {len(cards)} potential events by 'vs' pattern")
    
    # If still no cards, check for the "8 events scheduled" text and try to find those events
    if not cards:
        # Look for the specific count text
        for element in soup.css("div, span, p"):
            text = element.text(strip=True)
            if text and "events scheduled" in text:
                # The parent might contain the events
                parent = element.parent
                # Look for event items in the same container
                event_items = parent.css("div, a")
                if event_items:
                    cards = [item for item in event_items if item.attributes.get("href") or item.css("h3, h4, p")]
                    log.info(f"Found {len(cards)} events near scheduled text")
                    break

    # Process found cards
    for card in cards:
        try:
            # Get event name - look for headings or text with vs pattern
            event_name = None
            
            # Try to find heading
            for heading_selector in ["h3", "h4", "h5", ".title", ".event-title", ".font-bold", "p.font-semibold"]:
                if heading := card.css_first(heading_selector):
                    event_name = heading.text(strip=True)
                    if event_name:
                        break
            
            # If no heading, look for any text with vs pattern
            if not event_name:
                for elem in card.css("div, span, p"):
                    text = elem.text(strip=True)
                    if text and len(text) > 10 and (" vs " in text or " vs. " in text):
                        event_name = text
                        break
            
            # If still no name, use card text
            if not event_name:
                event_name = card.text(strip=True)
                if len(event_name) > 50:  # Too long, likely contains other elements
                    # Try to get first meaningful line
                    lines = event_name.split('\n')
                    for line in lines:
                        if line and len(line) > 10 and (" vs " in line or any(team.isupper() for team in line.split())):
                            event_name = line.strip()
                            break
            
            if not event_name or len(event_name) < 10:
                continue

            # Check if already cached
            sport = detect_sport(event_name)
            key = f"[{sport}] {event_name} ({TAG})"
            if key in cached_keys:
                continue

            # Find link - look for anchor tags
            link = None
            if anchor := card.css_first("a[href]"):
                href = anchor.attributes.get("href")
                if href:
                    if href.startswith("/"):
                        link = urljoin(BASE_URL, href)
                    elif href.startswith("http"):
                        link = href
            
            # If no link in card, maybe the whole card is a link
            if not link and card.tag == "a" and card.attributes.get("href"):
                href = card.attributes.get("href")
                if href:
                    if href.startswith("/"):
                        link = urljoin(BASE_URL, href)
                    elif href.startswith("http"):
                        link = href
            
            if not link:
                continue

            # Find logo/image
            logo = None
            if img := card.css_first("img"):
                src = img.attributes.get("src") or img.attributes.get("data-src")
                if src:
                    if src.startswith("http"):
                        logo = src
                    else:
                        logo = urljoin(BASE_URL, src)

            events.append({
                "sport": sport,
                "event": event_name,
                "link": link,
                "logo": logo,
            })
            
            log.info(f"Found event: {sport} - {event_name}")

        except Exception as e:
            log.debug(f"Error processing card: {e}")
            continue

    log.info(f"Total events found: {len(events)}")
    return events


# ---------------------------------------------------------
# OUTPUT FILE GENERATORS
# ---------------------------------------------------------

def generate_vlc_output(entries: list[tuple], channel_number: int = 1) -> str:
    """
    Generate VLC format M3U8 with #EXTVLCOPT headers
    """
    output = ["#EXTM3U"]
    
    for key, entry in entries:
        if not entry.get("url"):
            continue
            
        # Format: [Sport] Event Name (TAG)
        display_name = key
        
        # Get tvg_id and logo
        tvg_id = entry.get("id", "Live.Event.us")
        logo = entry.get("logo", "")
        base_url = entry.get("base", "")
        
        # Add channel number (incrementing)
        output.append(f'#EXTINF:-1 tvg-chno="{channel_number}" tvg-id="{tvg_id}" tvg-name="{display_name}" tvg-logo="{logo}" group-title="Live Events",{display_name}')
        
        # Add VLC options
        output.append(f'#EXTVLCOPT:http-referrer={base_url}')
        output.append(f'#EXTVLCOPT:http-origin={base_url}')
        output.append(f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0')
        
        # Add stream URL
        output.append(entry["url"])
        output.append("")  # Empty line for readability
        
        channel_number += 1
    
    return "\n".join(output)


def generate_tivimate_output(entries: list[tuple], channel_number: int = 1) -> str:
    """
    Generate Tivimate format M3U8 with pipe-separated headers
    """
    output = ["#EXTM3U"]
    
    for key, entry in entries:
        if not entry.get("url"):
            continue
            
        # Format: [Sport] Event Name (TAG)
        display_name = key
        
        # Get tvg_id and logo
        tvg_id = entry.get("id", "Live.Event.us")
        logo = entry.get("logo", "")
        base_url = entry.get("base", "")
        
        # Add channel number (incrementing)
        output.append(f'#EXTINF:-1 tvg-chno="{channel_number}" tvg-id="{tvg_id}" tvg-name="{display_name}" tvg-logo="{logo}" group-title="Live Events",{display_name}')
        
        # Create Tivimate URL with pipe-separated headers
        # Format: stream_url|referer=base_url|origin=base_url|user-agent=encoded_ua
        tivimate_url = f'{entry["url"]}|referer={base_url}|origin={base_url}|user-agent={TIVIMATE_UA}'
        output.append(tivimate_url)
        output.append("")  # Empty line for readability
        
        channel_number += 1
    
    return "\n".join(output)


def write_output_files():
    """
    Write both VLC and Tivimate output files
    """
    if not urls:
        log.warning("No URLs to write to output files")
        return
    
    # Filter entries with valid URLs
    valid_entries = [(k, v) for k, v in urls.items() if v.get("url")]
    
    if not valid_entries:
        log.warning("No valid streams found to write to output files")
        return
    
    # Sort entries by key for consistent ordering
    sorted_entries = sorted(valid_entries, key=lambda x: x[0])
    
    # Generate VLC output
    vlc_content = generate_vlc_output(sorted_entries)
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(vlc_content)
    log.info(f"Written {len(sorted_entries)} streams to {VLC_OUTPUT}")
    
    # Generate Tivimate output
    tivimate_content = generate_tivimate_output(sorted_entries)
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(tivimate_content)
    log.info(f"Written {len(sorted_entries)} streams to {TIVIMATE_OUTPUT}")


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        async with network.event_context(browser, stealth=True) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    log.info(f"URL {i}) Processing: {ev['event']}")
                    
                    handler = partial(
                        process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                    )

                    url, iframe = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        timeout=30,
                        log=log,
                    )

                    sport, event, logo = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": iframe,
                        "timestamp": now.timestamp(),
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1
                        urls[key] = entry
                        log.info(f"URL {i}) Stream captured successfully")
                    else:
                        log.warning(f"URL {i}) No stream found")

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.warning("No new events found - check if website structure has changed")
        
        # Write any cached URLs to output files even if no new events
        if urls:
            log.info(f"Writing {len(urls)} cached streams to output files")

    CACHE_FILE.write(cached_urls)
    
    # Write output files after all processing
    write_output_files()


async def main():
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        
        await scrape(browser)
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
