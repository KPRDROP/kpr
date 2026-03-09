import asyncio
from functools import partial
from typing import Any
import os
import re

from playwright.async_api import async_playwright, Browser

from utils import Cache, Time, get_logger, leagues, network


log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SPRTZONE"

CACHE_FILE = Cache(TAG, exp=5400)
API_FILE = Cache(f"{TAG}-api", exp=28800)

API_URL = os.environ.get("SPZONE_API_URL")
HOME_URL = os.environ.get("HOME_URL")


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
# ROBUST M3U8 CAPTURE WITH MULTIPLE STRATEGIES
# ---------------------------------------------------------

async def capture_m3u8_robust(page, url_num: int, log) -> str | None:
    """
    Ultra-robust function that uses multiple strategies to capture m3u8
    """
    captured = []
    got_one = asyncio.Event()
    
    def handle_response(response):
        url = response.url.lower()
        # Check URL for m3u8
        if '.m3u8' in url:
            # Filter out known non-stream URLs
            if not any(x in url for x in ['amazonaws', 'knitcdn', 'jwpltx', 'analytics', 'tracking']):
                captured.append(response.url)
                got_one.set()
                log.info(f"URL {url_num}) Found m3u8 in response: {response.url}")
        
        # Check content-type header
        try:
            content_type = response.headers.get('content-type', '').lower()
            if 'mpegurl' in content_type or 'application/vnd.apple.mpegurl' in content_type:
                if not any(x in response.url.lower() for x in ['amazonaws', 'knitcdn', 'jwpltx']):
                    captured.append(response.url)
                    got_one.set()
                    log.info(f"URL {url_num}) Found m3u8 by content-type: {response.url}")
        except:
            pass
    
    def handle_request(request):
        url = request.url.lower()
        if '.m3u8' in url:
            if not any(x in url for x in ['amazonaws', 'knitcdn', 'jwpltx', 'analytics', 'tracking']):
                captured.append(request.url)
                got_one.set()
                log.info(f"URL {url_num}) Found m3u8 in request: {request.url}")
    
    # Attach both request and response handlers
    page.on("response", handle_response)
    page.on("request", handle_request)
    
    try:
        # Navigate to the page
        log.info(f"URL {url_num}) Loading page...")
        await page.goto(page.url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(5000)  # Wait for initial JavaScript execution
        
        # STRATEGY 1: Look for and click any play button with comprehensive selectors
        play_selectors = [
            # Common button text patterns
            "button:has-text('Play')",
            "button:has-text('play')",
            "button:has-text('PLAY')",
            "button:has-text('Start')",
            "button:has-text('Watch')",
            "button:has-text('Stream')",
            "button:has-text('Launch')",
            "button:has-text('►')",
            "button:has-text('▶')",
            
            # Common classes and IDs
            ".play-button",
            ".play-btn",
            ".play",
            ".vjs-play-button",
            ".vjs-big-play-button",
            ".mejs-play",
            ".mejs-playbutton",
            ".jw-icon-play",
            ".fp-play",
            ".play-icon",
            ".playpause",
            "#play-button",
            "#play",
            "#playbtn",
            "#playButton",
            "#start-button",
            
            # Video.js specific
            ".vjs-big-play-button",
            ".vjs-play-control",
            
            # JW Player specific
            ".jw-play",
            ".jw-icon-playback",
            
            # MediaElement.js
            ".mejs-playpause-button",
            
            # Flowplayer
            ".fp-playbtn",
            
            # General iframe players
            "iframe[src*='player']",
            "iframe[src*='embed']",
            "iframe[src*='stream']",
        ]
        
        # Try each selector
        clicked = False
        for selector in play_selectors:
            try:
                element = await page.wait_for_selector(selector, timeout=2000)
                if element and await element.is_visible():
                    log.info(f"URL {url_num}) Found play button with selector: {selector}")
                    await element.click()
                    await page.wait_for_timeout(3000)
                    clicked = True
                    break
            except:
                continue
        
        # STRATEGY 2: If no button found with selectors, try clicking at strategic positions
        if not clicked:
            log.info(f"URL {url_num}) No play button found with selectors, trying position clicks")
            positions = [
                (640, 360),  # Center
                (500, 400),  # Common video area
                (800, 450),  # Right side
                (300, 300),  # Top left of video
                (900, 400),  # Right side of video
            ]
            for x, y in positions:
                try:
                    await page.mouse.click(x, y)
                    await page.wait_for_timeout(1500)
                except:
                    pass
        
        # STRATEGY 3: Check all iframes and try to click inside them
        frames = page.frames
        log.info(f"URL {url_num}) Found {len(frames)} frames")
        
        for i, frame in enumerate(frames):
            if i == 0:  # Skip main page frame
                continue
            try:
                # Try to click common elements in iframe
                await frame.click("button", timeout=2000)
                log.info(f"URL {url_num}) Clicked button in iframe {i}")
                await page.wait_for_timeout(2000)
            except:
                try:
                    await frame.click("body", timeout=2000)
                    await page.wait_for_timeout(2000)
                except:
                    pass
        
        # STRATEGY 4: Look for and execute any play() functions in console
        try:
            await page.evaluate("""
                () => {
                    // Try to find and play video elements
                    const videos = document.querySelectorAll('video');
                    videos.forEach(v => {
                        try { v.play(); } catch(e) {}
                    });
                    
                    // Try to trigger play on any player instances
                    if (typeof jwplayer !== 'undefined') {
                        try { jwplayer().play(); } catch(e) {}
                    }
                    if (typeof videojs !== 'undefined') {
                        try { 
                            const players = videojs.getAllPlayers();
                            players.forEach(p => { try { p.play(); } catch(e) {} });
                        } catch(e) {}
                    }
                    if (typeof flowplayer !== 'undefined') {
                        try { flowplayer().play(); } catch(e) {}
                    }
                }
            """)
            log.info(f"URL {url_num}) Executed JavaScript play attempts")
            await page.wait_for_timeout(3000)
        except:
            pass
        
        # Wait for m3u8 request (up to 30 seconds)
        try:
            await asyncio.wait_for(got_one.wait(), timeout=30)
            if captured:
                return captured[0]
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8 after all strategies")
            
    except Exception as e:
        log.error(f"URL {url_num}) Error during capture: {e}")
    finally:
        page.remove_listener("response", handle_response)
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

        # Create context with proper viewport and headers
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            device_scale_factor=1,
            has_touch=False,
            locale="en-US",
            timezone_id="America/New_York",
            permissions=["geolocation"],
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Referer": "https://sportzone.su/",
            }
        )
        
        # Enable request/response interception for better capture
        await context.route("**/*", lambda route: route.continue_())
        
        for i, ev in enumerate(events, start=1):
            
            page = await context.new_page()
            link = ev["link"]
            
            log.info(f"URL {i}) Opening {link} - {ev['event']}")
            
            try:
                # Navigate to the page with longer timeout
                await page.goto(link, wait_until="domcontentloaded", timeout=30000)
                
                # Use robust capture with multiple strategies
                url = await capture_m3u8_robust(page, i, log)

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
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--window-size=1280,720",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ],
        )

        await scrape(browser)

        await browser.close()


if __name__ == "__main__":

    asyncio.run(main())
