import asyncio
import os
import re
import json
from urllib.parse import urljoin, quote

from playwright.async_api import Browser, Page, Response, Frame
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIM"

CACHE_FILE = Cache(TAG, exp=10_800)

# New API endpoint
API_URL = "https://api.saduvisvesvaraya.workers.dev/api/live-upcoming"

# Headers for requests
HEADERS = {
    "Referer": "https://junkieembeds.pages.dev/",
    "Origin": "https://junkieembeds.pages.dev/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

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

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


# ---------------------------------------------------------
# PLAYLIST GENERATOR
# ---------------------------------------------------------

def generate_playlists():
    """
    Generate VLC and TiviMate M3U8 playlists from captured streams.
    """
    vlc_lines = ["#EXTM3U"]
    tivimate_lines = ["#EXTM3U"]

    ua_encoded = quote(USER_AGENT, safe="")

    for chno, (name, data) in enumerate(urls.items(), start=1):

        url = data.get("url")
        logo = data.get("logo") or ""
        tvg_id = data.get("id", "Live.Event.us")
        base = data.get("base", "https://junkieembeds.pages.dev/")

        if not url:
            continue

        # Sanitize name for playlist
        safe_name = name.replace('"', '').replace("'", "")

        extinf = (
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" '
            f'tvg-name="{safe_name}" tvg-logo="{logo}" group-title="Live Events",{safe_name}'
        )

        # VLC (no pipe encoding needed)
        vlc_lines.append(extinf)
        vlc_lines.append(f"#EXTVLCOPT:http-referrer={base}")
        vlc_lines.append(f"#EXTVLCOPT:http-origin={base}")
        vlc_lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc_lines.append(url)

        # TiviMate (pipe format with encoded user agent)
        tivimate_lines.append(extinf)

        # Build the pipe-formatted URL
        tiv_url = (
            f"{url}"
            f"|referer={base}"
            f"|origin={base}"
            f"|user-agent={ua_encoded}"
        )

        tivimate_lines.append(tiv_url)

    # Write VLC playlist
    with open("tim_vlc.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(vlc_lines))

    # Write TiviMate playlist
    with open("tim_tivimate.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(tivimate_lines))

    log.info(f"Playlists generated: {len(urls)} streams -> tim_vlc.m3u8 / tim_tivimate.m3u8")


# ---------------------------------------------------------
# CAPTURE M3U8 FROM EMBED PAGE
# ---------------------------------------------------------

async def capture_m3u8_from_embed(
    page: Page,
    embed_url: str,
    url_num: int,
    timeout: int = 45,
) -> str | None:
    """
    Navigate to embed URL and capture the m3u8 stream URL.
    Uses network sniffing and interaction with the player.
    """
    captured_m3u8 = []
    got_m3u8 = asyncio.Event()

    # Track seen URLs to avoid duplicates
    seen_urls = set()

    # Filter for valid m3u8 URLs
    def is_valid_m3u8(url: str) -> bool:
        url_lower = url.lower()
        return (
            ".m3u8" in url_lower and
            "manifest" not in url_lower and
            "analytics" not in url_lower and
            "tracking" not in url_lower and
            "collect" not in url_lower
        )

    # Request listener
    async def handle_request(request):
        req_url = request.url
        if is_valid_m3u8(req_url) and req_url not in seen_urls:
            seen_urls.add(req_url)
            captured_m3u8.append(req_url)
            got_m3u8.set()
            log.info(f"URL {url_num}) M3U8 request captured: {req_url[:100]}...")

    # Response listener
    async def handle_response(response):
        resp_url = response.url
        if is_valid_m3u8(resp_url) and resp_url not in seen_urls:
            seen_urls.add(resp_url)
            captured_m3u8.append(resp_url)
            got_m3u8.set()
            log.info(f"URL {url_num}) M3U8 response captured: {resp_url[:100]}...")

        # Also check content-type header for m3u8
        try:
            content_type = response.headers.get("content-type", "").lower()
            if "mpegurl" in content_type or "application/vnd.apple.mpegurl" in content_type:
                if is_valid_m3u8(resp_url) and resp_url not in seen_urls:
                    seen_urls.add(resp_url)
                    captured_m3u8.append(resp_url)
                    got_m3u8.set()
                    log.info(f"URL {url_num}) M3U8 by content-type: {resp_url[:100]}...")
        except:
            pass

    page.on("request", handle_request)
    page.on("response", handle_response)

    try:
        log.info(f"URL {url_num}) Navigating to embed: {embed_url}")

        # Navigate to embed page
        await page.goto(embed_url, wait_until="domcontentloaded", timeout=15000)

        # Wait for page to settle
        await asyncio.sleep(2)

        # Try multiple interaction methods to trigger video playback
        interaction_success = False

        # Method 1: Click on the embed frame content
        try:
            # Try to find and click on video element or play button
            click_selectors = [
                "video",
                ".vjs-big-play-button",
                ".jw-icon-play",
                ".play-button",
                "[aria-label='Play']",
                "button[aria-label*='Play']",
                ".fp-playbtn",
                ".mejs-playpause-button",
                ".plyr__control--overlaid",
                "[data-testid='play-button']",
                "div[class*='play']",
            ]

            for selector in click_selectors:
                try:
                    element = await page.wait_for_selector(selector, timeout=2000)
                    if element:
                        await element.click()
                        log.info(f"URL {url_num}) Clicked element: {selector}")
                        interaction_success = True
                        await asyncio.sleep(1)
                        break
                except:
                    continue

            # If no specific button found, click center of page
            if not interaction_success:
                await page.mouse.click(500, 300)
                log.info(f"URL {url_num}) Clicked center of page")
                interaction_success = True
                await asyncio.sleep(1)

        except Exception as e:
            log.debug(f"URL {url_num}) Click interaction error: {e}")

        # Method 2: Execute JavaScript to trigger playback
        try:
            js_result = await page.evaluate("""
                () => {
                    const results = [];
                    
                    // Find all video elements
                    const videos = document.querySelectorAll('video');
                    videos.forEach(v => {
                        try {
                            v.play();
                            results.push('played video');
                        } catch(e) {}
                    });
                    
                    // Find all iframes and try to interact with them
                    const frames = document.querySelectorAll('iframe');
                    frames.forEach(f => {
                        try {
                            const doc = f.contentDocument || f.contentWindow.document;
                            const vids = doc.querySelectorAll('video');
                            vids.forEach(v => {
                                try {
                                    v.play();
                                    results.push('played video in iframe');
                                } catch(e) {}
                            });
                        } catch(e) {}
                    });
                    
                    // Dispatch click event on body
                    document.body.dispatchEvent(new MouseEvent('click', {
                        view: window,
                        bubbles: true,
                        cancelable: true,
                        clientX: 500,
                        clientY: 300
                    }));
                    
                    return results.length;
                }
            """)
            log.info(f"URL {url_num}) JS execution result: {js_result}")
        except Exception as e:
            log.debug(f"URL {url_num}) JS execution error: {e}")

        # Method 3: Press space key to play
        try:
            await page.keyboard.press("Space")
            log.info(f"URL {url_num}) Pressed space key")
            await asyncio.sleep(0.5)
        except:
            pass

        # Wait for m3u8 capture with timeout
        try:
            await asyncio.wait_for(got_m3u8.wait(), timeout=timeout)
            log.info(f"URL {url_num}) M3U8 captured successfully!")
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timeout waiting for M3U8 after {timeout}s")

        # Return the first valid m3u8 URL
        if captured_m3u8:
            return captured_m3u8[0]

        # Fallback: Search HTML content for m3u8 URLs
        try:
            html = await page.content()
            # Pattern for m3u8 URLs (not containing manifest/tracking)
            pattern = r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*'
            matches = re.findall(pattern, html)
            for match in matches:
                if not any(x in match.lower() for x in ['manifest', 'analytics', 'tracking']):
                    log.info(f"URL {url_num}) Found m3u8 in HTML: {match[:100]}...")
                    return match
        except Exception as e:
            log.debug(f"URL {url_num}) HTML search error: {e}")

        log.warning(f"URL {url_num}) No m3u8 stream found")
        return None

    except Exception as e:
        log.error(f"URL {url_num}) Error in capture: {e}")
        return None

    finally:
        page.remove_listener("request", handle_request)
        page.remove_listener("response", handle_response)


# ---------------------------------------------------------
# FETCH EVENTS FROM API
# ---------------------------------------------------------

async def fetch_events_from_api(cached_keys: set) -> list[dict]:
    """
    Fetch live and upcoming events from the API.
    Returns list of events to process.
    """
    events = []

    log.info(f"Fetching events from API: {API_URL}")

    try:
        response = await network.request(
            API_URL,
            headers=HEADERS,
            log=log,
            timeout=30
        )

        if not response:
            log.error("Failed to fetch from API - no response")
            return events

        # Parse JSON response
        try:
            data = json.loads(response.content)
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse JSON response: {e}")
            return events

        # Extract items from response
        items = data.get("items", [])
        if not items:
            log.warning("No items found in API response")
            return events

        log.info(f"Found {len(items)} events in API response")

        for item in items:
            event_name = item.get("name", "")
            event_url = item.get("url", "")
            event_logo = item.get("logo", "")
            genre = item.get("genre", 0)
            event_id = item.get("id", "")
            time_str = item.get("time", "")

            if not event_name or not event_url:
                log.debug(f"Skipping event with missing name or URL: {event_name}")
                continue

            # Get sport name from genre ID
            sport = SPORT_GENRES.get(genre, "Sports")

            # Create cache key
            key = f"[{sport}] {event_name} ({TAG})"

            # Skip if already in cache
            if key in cached_keys:
                continue

            # Build full embed URL if needed
            if not event_url.startswith("http"):
                embed_full_url = f"https://junkieembeds.pages.dev/embed/{event_url}"
            else:
                embed_full_url = event_url

            events.append({
                "key": key,
                "sport": sport,
                "event": event_name,
                "url": embed_full_url,
                "logo": event_logo,
                "id": event_id,
                "time": time_str,
            })

            log.info(f"New event: [{sport}] {event_name}")

    except Exception as e:
        log.error(f"Error fetching events from API: {e}")

    return events


# ---------------------------------------------------------
# GET TVG ID AND LOGO
# ---------------------------------------------------------

def get_tvg_info(sport: str, event_name: str) -> tuple[str, str]:
    """
    Get TVG ID and logo for event using leagues utility.
    """
    try:
        tvg_id, logo = leagues.get_tvg_info(sport, event_name)
        return tvg_id, logo
    except Exception as e:
        log.debug(f"Error getting TVG info for {event_name}: {e}")
        return "Live.Event.us", ""


# ---------------------------------------------------------
# MAIN SCRAPER
# ---------------------------------------------------------

async def scrape(browser: Browser) -> None:
    """
    Main scraping function - fetches events from API and captures m3u8 streams.
    """
    # Load cached URLs
    cached_urls = CACHE_FILE.load()
    cached_keys = set(cached_urls.keys())

    # Keep valid cached URLs (with actual stream URLs)
    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}
    urls.update(valid_urls)

    log.info(f"Loaded {len(valid_urls)} valid event(s) from cache")

    # Fetch new events from API
    events = await fetch_events_from_api(cached_keys)

    if not events:
        log.info("No new events to process")
        generate_playlists()
        return

    log.info(f"Processing {len(events)} new event(s)")

    now = Time.clean(Time.now())
    successful_count = 0

    # Process each event with a dedicated page
    async with network.event_context(browser, stealth=False) as context:
        for i, event in enumerate(events, start=1):
            log.info(f"--- Processing [{i}/{len(events)}]: {event['event']} ---")

            async with network.event_page(context) as page:
                # Set extra headers
                await page.set_extra_http_headers(HEADERS)

                # Capture m3u8 from embed URL
                m3u8_url = await capture_m3u8_from_embed(
                    page=page,
                    embed_url=event["url"],
                    url_num=i,
                    timeout=45,
                )

                # Get TVG info
                tvg_id, logo = get_tvg_info(event["sport"], event["event"])

                # Use provided logo if available, otherwise fallback to league logo
                final_logo = event["logo"] or logo

                # Create cache entry
                entry = {
                    "url": m3u8_url,
                    "logo": final_logo,
                    "base": "https://junkieembeds.pages.dev/",
                    "timestamp": now.timestamp(),
                    "id": tvg_id or event["id"] or "Live.Event.us",
                    "link": event["url"],
                    "sport": event["sport"],
                }

                # Update cache and urls
                cached_urls[event["key"]] = entry

                if m3u8_url:
                    successful_count += 1
                    urls[event["key"]] = entry
                    log.info(f"✓ URL {i}) Stream captured successfully!")
                    log.info(f"   M3U8: {m3u8_url[:120]}...")
                else:
                    log.warning(f"✗ URL {i}) No stream captured for {event['event']}")

                # Small delay between requests
                await asyncio.sleep(1)

    log.info(f"Scraping complete: {successful_count}/{len(events)} streams captured")

    # Save cache and generate playlists
    CACHE_FILE.write(cached_urls)
    generate_playlists()


# ---------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------

from playwright.async_api import async_playwright


async def main():
    """
    Main async entry point.
    """
    log.info("=" * 50)
    log.info("Starting TIM Streams Updater")
    log.info(f"API URL: {API_URL}")
    log.info("=" * 50)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        try:
            await scrape(browser)
        except Exception as e:
            log.error(f"Scraping failed: {e}")
            raise
        finally:
            await browser.close()

    log.info("TIM Streams Updater finished")


if __name__ == "__main__":
    asyncio.run(main())
