import asyncio
from functools import partial
from typing import Any
import os

from playwright.async_api import async_playwright, Browser

from utils import Cache, Time, get_logger, leagues, network


log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SPRTZONE"

CACHE_FILE = Cache(TAG, exp=5400)
API_FILE = Cache(f"{TAG}-api", exp=28800)

API_URL = os.environ.get("SPZONE_API_URL")
HOME_URL = os.environ.get("HOME_URL", "https://vividmosaica.com/")


# ---------------------------------------------------------
# API CACHE
# ---------------------------------------------------------

async def refresh_api_cache(now_ts: float) -> list[dict[str, Any]]:

    api_data = [{"timestamp": now_ts}]

    if r := await network.request(API_URL, log=log):

        api_data: list[dict] = r.json().get("matches", [])

        if api_data:
            for event in api_data:
                event["ts"] = event.pop("timestamp")

        api_data[-1]["timestamp"] = now_ts

    return api_data


# ---------------------------------------------------------
# EVENT DISCOVERY
# ---------------------------------------------------------

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):

        log.info("Refreshing API cache")

        api_data = await refresh_api_cache(now.timestamp())

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(hours=-12)
    end_dt = now.delta(hours=12)

    for stream_group in api_data:

        sport = stream_group.get("league")

        team_1 = stream_group.get("team1")
        team_2 = stream_group.get("team2")

        if not (sport and team_1 and team_2):
            continue

        event_name = f"{team_1} vs {team_2}"

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        if not (event_ts := stream_group.get("ts")):
            continue

        event_dt = Time.from_ts(int(event_ts / 1000))

        if not start_dt <= event_dt <= end_dt:
            continue

        if not (event_channels := stream_group.get("channels")):
            continue

        if not (event_links := event_channels[0].get("links")):
            continue

        # Use the CDN link directly
        event_url: str = event_links[0]

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": event_url,
            }
        )

    return events


# ---------------------------------------------------------
# ENHANCED M3U8 CAPTURE WITH CLICK INTERACTION
# ---------------------------------------------------------

async def capture_m3u8_with_clicks(page, url_num: int, log) -> str | None:
    """
    Enhanced function that clicks play buttons and waits for m3u8
    """
    captured = []
    got_one = asyncio.Event()
    
    def handle_request(request):
        url = request.url.lower()
        # Look for m3u8 in requests
        if '.m3u8' in url and not any(x in url for x in ['amazonaws', 'knitcdn', 'jwpltx']):
            captured.append(request.url)
            got_one.set()
            log.info(f"URL {url_num}) Captured m3u8 request: {request.url}")
    
    # Attach request handler
    page.on("request", handle_request)
    
    try:
        # Navigate to the page
        log.info(f"URL {url_num}) Loading page...")
        await page.goto(page.url, wait_until="domcontentloaded", timeout=10000)
        await page.wait_for_timeout(3000)  # Wait for initial load
        
        # Try multiple methods to click play button
        click_selectors = [
            "button:has-text('Play')",
            "button:has-text('play')",
            "button:has-text('Start')",
            "button:has-text('Watch')",
            ".play-button",
            "#play-button",
            "button.vjs-big-play-button",
            ".vjs-big-play-button",
            "button[aria-label='Play']",
            "button[title='Play']",
            "iframe + div",  # Some players have overlay div
            "body"  # Last resort - click body
        ]
        
        # Try each selector
        for selector in click_selectors:
            try:
                button = await page.wait_for_selector(selector, timeout=2000)
                if button:
                    log.info(f"URL {url_num}) Found play button with selector: {selector}")
                    await button.click()
                    await page.wait_for_timeout(2000)
                    break
            except:
                continue
        
        # Also try clicking at common positions where play button might be
        positions = [(500, 400), (800, 450), (640, 360)]
        for x, y in positions:
            try:
                await page.mouse.click(x, y)
                await page.wait_for_timeout(1000)
            except:
                pass
        
        # Check iframes for play buttons
        for frame in page.frames:
            try:
                await frame.click("button", timeout=1000)
                await page.wait_for_timeout(1000)
            except:
                try:
                    await frame.click("body", timeout=1000)
                    await page.wait_for_timeout(1000)
                except:
                    pass
        
        # Wait for m3u8 request (up to 25 seconds)
        try:
            await asyncio.wait_for(got_one.wait(), timeout=25)
            if captured:
                return captured[0]
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8 after clicks")
            return None
            
    except Exception as e:
        log.error(f"URL {url_num}) Error during capture: {e}")
        return None
    finally:
        page.remove_listener("request", handle_request)
    
    return None


# ---------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------

async def scrape(browser: Browser) -> None:

    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        # Create context with proper viewport for better button visibility
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        for i, ev in enumerate(events, start=1):
            
            page = await context.new_page()
            link = ev["link"]
            
            log.info(f"URL {i}) Opening {link} - {ev['event']}")
            
            try:
                # Navigate to the page
                await page.goto(link, wait_until="domcontentloaded", timeout=10000)
                
                # Use enhanced capture with clicks
                url = await capture_m3u8_with_clicks(page, i, log)

                sport, event = ev["sport"], ev["event"]

                key = f"[{sport}] {event} ({TAG})"

                tvg_id, logo = leagues.get_tvg_info(sport, event)

                entry = {
                    "url": url,
                    "logo": logo,
                    "base": HOME_URL,
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                    "link": link,
                }

                cached_urls[key] = entry

                if url:

                    log.info(f"URL {i}) Stream detected: {url}")

                    valid_count += 1
                    urls[key] = entry

                else:

                    log.warning(f"URL {i}) No stream found")

            except Exception as e:
                log.error(f"URL {i}) Failed: {e}")
            
            finally:
                await page.close()

        await context.close()

        log.info(f"Collected {valid_count - cached_count} new events")

    else:

        log.info("No new events found")

    CACHE_FILE.write(cached_urls)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

async def main():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1280,720",
            ],
        )

        await scrape(browser)

        await browser.close()


if __name__ == "__main__":

    asyncio.run(main())import asyncio
from functools import partial
from typing import Any
import os

from playwright.async_api import async_playwright, Browser

from utils import Cache, Time, get_logger, leagues, network


log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SPRTZONE"

CACHE_FILE = Cache(TAG, exp=5400)
API_FILE = Cache(f"{TAG}-api", exp=28800)

API_URL = os.environ.get("SPZONE_API_URL")
HOME_URL = os.environ.get("HOME_URL", "https://vividmosaica.com/")


# ---------------------------------------------------------
# API CACHE
# ---------------------------------------------------------

async def refresh_api_cache(now_ts: float) -> list[dict[str, Any]]:

    api_data = [{"timestamp": now_ts}]

    if r := await network.request(API_URL, log=log):

        api_data: list[dict] = r.json().get("matches", [])

        if api_data:
            for event in api_data:
                event["ts"] = event.pop("timestamp")

        api_data[-1]["timestamp"] = now_ts

    return api_data


# ---------------------------------------------------------
# EVENT DISCOVERY
# ---------------------------------------------------------

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):

        log.info("Refreshing API cache")

        api_data = await refresh_api_cache(now.timestamp())

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(hours=-12)
    end_dt = now.delta(hours=12)

    for stream_group in api_data:

        sport = stream_group.get("league")

        team_1 = stream_group.get("team1")
        team_2 = stream_group.get("team2")

        if not (sport and team_1 and team_2):
            continue

        event_name = f"{team_1} vs {team_2}"

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        if not (event_ts := stream_group.get("ts")):
            continue

        event_dt = Time.from_ts(int(event_ts / 1000))

        if not start_dt <= event_dt <= end_dt:
            continue

        if not (event_channels := stream_group.get("channels")):
            continue

        if not (event_links := event_channels[0].get("links")):
            continue

        # Use the CDN link directly - this is where the m3u8 stream originates
        event_url: str = event_links[0]

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": event_url,
            }
        )

    return events


# ---------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------

async def scrape(browser: Browser) -> None:

    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        # Use the proven event_context
        async with network.event_context(browser, stealth=False) as context:
            
            for i, ev in enumerate(events, start=1):
                
                # Use event_page which properly sets up the page
                async with network.event_page(context) as page:
                    
                    link = ev["link"]
                    
                    log.info(f"URL {i}) Opening {link}")
                    
                    # Create the handler using network.process_event
                    handler = partial(
                        network.process_event,
                        url=link,
                        url_num=i,
                        page=page,
                        log=log,
                        timeout=15,  # Increased timeout slightly to 15 seconds
                    )
                    
                    # Use safe_process for proper error handling and concurrency control
                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,  # Use the semaphore from network
                        timeout=20,  # Overall timeout of 20 seconds
                        log=log,
                    )

                    sport, event = ev["sport"], ev["event"]

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": HOME_URL,
                        "timestamp": now.timestamp(),
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:

                        log.info(f"URL {i}) Stream detected: {url}")

                        valid_count += 1

                        urls[key] = entry

                    else:

                        log.warning(f"URL {i}) No stream found")

        log.info(f"Collected {valid_count - cached_count} new events")

    else:

        log.info("No new events found")

    CACHE_FILE.write(cached_urls)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

async def main():

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
