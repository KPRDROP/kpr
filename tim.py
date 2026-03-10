import asyncio
from functools import partial
from urllib.parse import urljoin, quote
import os
import re

from playwright.async_api import Browser, Page, Response
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

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

SPORT_GENRES = {
    1: "Soccer",
    2: "Motorsport",
    3: "MMA",
    4: "Fight",
    5: "Boxing",
    6: "Wrestling",
    7: "Basketball",
    8: "American Football",
    9: "Baseball",
    10: "Tennis",
    11: "Hockey",
    12: "Darts",
    13: "Cricket",
    14: "Cycling",
    15: "Rugby",
    16: "Live Shows",
    17: "Other",
}


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
            await asyncio.wait_for(wait_task, timeout=15)  # Increased timeout
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
    
    # Try multiple selectors to find event cards
    card_selectors = [
        "#eventsSection .card",
        ".card",
        "[class*='event']",
        ".event-item",
        "article",
        ".match-card",
        ".game-card",
    ]
    
    cards = []
    for selector in card_selectors:
        cards = soup.css(selector)
        if cards:
            log.info(f"Found {len(cards)} cards with selector: {selector}")
            break
    
    if not cards:
        log.error("No cards found on the page")
        # Debug: Print page structure
        body = soup.css_first("body")
        if body:
            log.debug(f"Page body classes: {body.attributes.get('class', 'None')}")
        return events

    for card in cards:
        try:
            card_attrs = card.attributes
            
            # Try to get sport/genre from data attributes or text
            sport = None
            
            # Method 1: data-genre attribute
            if sport_id := card_attrs.get("data-genre"):
                try:
                    sport = SPORT_GENRES.get(int(sport_id))
                except (ValueError, TypeError):
                    pass
            
            # Method 2: Look for sport in card text
            if not sport:
                card_text = card.text()
                for genre_name in SPORT_GENRES.values():
                    if genre_name.lower() in card_text.lower():
                        sport = genre_name
                        break
            
            # Method 3: Default to "Other" if no sport found
            if not sport:
                sport = "Other"
            
            # Get event name
            event_name = None
            
            # Try data-search attribute
            event_name = card_attrs.get("data-search")
            
            # Try title attribute
            if not event_name:
                event_name = card_attrs.get("title")
            
            # Try to find title in card
            if not event_name:
                title_selectors = ["h3", "h4", "h5", ".title", ".event-title", ".match-title"]
                for selector in title_selectors:
                    if elem := card.css_first(selector):
                        event_name = elem.text(strip=True)
                        if event_name:
                            break
            
            if not event_name:
                continue

            # Check if already cached
            if f"[{sport}] {event_name} ({TAG})" in cached_keys:
                continue

            # Skip if event has countdown (not started)
            if badge := card.css_first(".badge"):
                if "data-countdown" in badge.attributes:
                    continue
                # Also check if badge text indicates future event
                badge_text = badge.text(strip=True).lower()
                if any(word in badge_text for word in ['start', 'soon', 'later', 'est']):
                    continue

            # Find watch button/link
            link = None
            link_selectors = [
                "a.btn-watch",
                "a[href*='event']",
                "a[href*='stream']",
                "a[href*='watch']",
                "a.button",
                ".watch-btn a",
                "a"
            ]
            
            for selector in link_selectors:
                if watch_btn := card.css_first(selector):
                    if href := watch_btn.attributes.get("href"):
                        link = urljoin(BASE_URL, href)
                        break
            
            if not link:
                continue

            # Get logo/image
            logo = None
            img_selectors = [
                ".card-thumb img",
                "img",
                ".event-image img",
                ".thumb img"
            ]
            
            for selector in img_selectors:
                if img := card.css_first(selector):
                    if src := img.attributes.get("src") or img.attributes.get("data-src"):
                        if src.startswith("http"):
                            logo = src
                        else:
                            logo = urljoin(BASE_URL, src)
                        break

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
                        timeout=30,  # Overall timeout of 30 seconds
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
