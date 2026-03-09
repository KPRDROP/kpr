import asyncio
from functools import partial
from typing import Any
import os
import re

from playwright.async_api import async_playwright, Browser, Page

from utils import Cache, Time, get_logger, leagues, network


log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SPRTZONE"

CACHE_FILE = Cache(TAG, exp=5400)
API_FILE = Cache(f"{TAG}-api", exp=28800)

API_URL = os.environ.get("SPZONE_API_URL")
HOME_URL = os.environ.get("HOME_URL", "https://vividmosaica.com/")

BASE_URL = "https://sportzone.su"


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
        event_id = stream_group.get("id")
        team_1 = stream_group.get("team1")
        team_2 = stream_group.get("team2")

        if not (sport and team_1 and team_2 and event_id):
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

        # Store both the game page URL and the CDN link
        game_url = f"{BASE_URL}/game/{event_id}"
        cdn_link = event_links[0]

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "game_url": game_url,
                "cdn_link": cdn_link,
            }
        )

    return events


# ---------------------------------------------------------
# M3U8 DETECTOR WITH BETTER STREAM CAPTURE
# ---------------------------------------------------------

async def capture_m3u8_from_game(page: Page, game_url: str, cdn_link: str, timeout: int = 30) -> str | None:
    """
    Navigate to game page, wait for iframe player to load, and capture m3u8 stream
    """
    stream_url = None
    stream_event = asyncio.Event()
    
    def handle_response(response):
        nonlocal stream_url
        url = response.url.lower()
        
        # Check for m3u8 in URL or content-type
        if '.m3u8' in url:
            stream_url = response.url
            stream_event.set()
            log.debug(f"Found m3u8 in URL: {url}")
        
        try:
            content_type = response.headers.get('content-type', '').lower()
            if 'mpegurl' in content_type or 'application/vnd.apple.mpegurl' in content_type:
                stream_url = response.url
                stream_event.set()
                log.debug(f"Found m3u8 by content-type: {url}")
        except:
            pass
    
    # Attach response handler
    page.on("response", handle_response)
    
    try:
        # Navigate to game page
        log.debug(f"Navigating to game page: {game_url}")
        await page.goto(game_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)  # Wait for initial load
        
        # Wait for iframe player to appear
        try:
            # Look for iframe that might contain the player
            await page.wait_for_selector("iframe", timeout=10000)
            log.debug("Found iframe player")
            
            # Get all iframes
            frames = page.frames
            log.debug(f"Found {len(frames)} frames")
            
            # Try to interact with the main content to activate player
            await page.mouse.click(500, 400)
            await page.wait_for_timeout(2000)
            
        except Exception as e:
            log.debug(f"No iframe found or error interacting: {e}")
        
        # Wait for stream to be detected (up to timeout seconds)
        try:
            await asyncio.wait_for(stream_event.wait(), timeout=timeout)
            log.debug(f"Stream captured successfully: {stream_url}")
        except asyncio.TimeoutError:
            log.debug("Timeout waiting for m3u8 stream")
            
    except Exception as e:
        log.error(f"Error during stream capture: {e}")
    
    return stream_url


# ---------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------

async def scrape(browser: Browser) -> None:

    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        # Create context with proper viewport and headers
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # Set extra headers
        await context.set_extra_http_headers({
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE_URL,
        })

        for i, ev in enumerate(events, start=1):
            
            page = await context.new_page()
            
            try:
                game_url = ev["game_url"]
                cdn_link = ev["cdn_link"]
                
                log.info(f"URL {i}) Processing: {ev['event']}")
                log.info(f"URL {i}) Game page: {game_url}")
                log.info(f"URL {i}) CDN link: {cdn_link}")
                
                # Capture m3u8 stream from game page
                stream_url = await capture_m3u8_from_game(
                    page=page,
                    game_url=game_url,
                    cdn_link=cdn_link,
                    timeout=45
                )

                sport, event = ev["sport"], ev["event"]

                key = f"[{sport}] {event} ({TAG})"

                tvg_id, logo = leagues.get_tvg_info(sport, event)

                entry = {
                    "url": stream_url,
                    "logo": logo,
                    "base": HOME_URL,
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                    "link": game_url,
                    "cdn_link": cdn_link,
                }

                cached_urls[key] = entry

                if stream_url:

                    log.info(f"URL {i}) Stream detected: {stream_url}")

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
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--allow-running-insecure-content",
                "--window-size=1920,1080",
            ],
        )

        await scrape(browser)

        await browser.close()


if __name__ == "__main__":

    asyncio.run(main())
