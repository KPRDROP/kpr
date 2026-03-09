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
# M3U8 CAPTURE WITH IFRAME TRAVERSAL
# ---------------------------------------------------------

async def capture_m3u8_from_virazo(page, url_num: int, log) -> str | None:
    """
    Specialized function to capture m3u8 from virazo.sx pages
    """
    captured = []
    got_one = asyncio.Event()
    
    def handle_response(response):
        url = response.url.lower()
        
        # Look for m3u8 in responses
        if '.m3u8' in url:
            # Filter out unwanted URLs
            if not any(x in url for x in ['amazonaws', 'knitcdn', 'jwpltx', 'analytics', 'bvtpk', 'al5sm']):
                captured.append(response.url)
                got_one.set()
                log.info(f"URL {url_num}) Found m3u8: {response.url}")
        
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
            if not any(x in url for x in ['amazonaws', 'knitcdn', 'jwpltx', 'analytics', 'bvtpk', 'al5sm']):
                captured.append(request.url)
                got_one.set()
                log.info(f"URL {url_num}) Found m3u8 in request: {request.url}")
    
    # Attach handlers
    page.on("response", handle_response)
    page.on("request", handle_request)
    
    try:
        # Navigate to the virazo page
        log.info(f"URL {url_num}) Loading virazo.sx page...")
        await page.goto(page.url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)
        
        # Look for the main iframe that contains the player
        log.info(f"URL {url_num}) Looking for player iframe...")
        
        # Wait for iframe to be present
        try:
            await page.wait_for_selector("iframe", timeout=10000)
        except:
            log.warning(f"URL {url_num}) No iframe found on page")
        
        # Get all iframes
        frames = page.frames
        log.info(f"URL {url_num}) Found {len(frames)} frames total")
        
        # Look for the vividmosaica iframe specifically
        vivid_frame = None
        for frame in frames:
            try:
                frame_url = frame.url.lower()
                if 'vividmosaica.com' in frame_url or 'embed2.php' in frame_url:
                    vivid_frame = frame
                    log.info(f"URL {url_num}) Found vividmosaica iframe: {frame_url}")
                    break
            except:
                continue
        
        # If we found the vividmosaica iframe, interact with it
        if vivid_frame:
            log.info(f"URL {url_num}) Interacting with vividmosaica iframe...")
            
            # Try to click play button in the iframe
            click_selectors = [
                "button",
                ".play-button",
                ".vjs-big-play-button",
                ".jw-icon-play",
                "video",
                ".fp-playbtn",
                "[aria-label='Play']",
                ".mejs-playpause-button",
            ]
            
            for selector in click_selectors:
                try:
                    element = await vivid_frame.wait_for_selector(selector, timeout=2000)
                    if element:
                        log.info(f"URL {url_num}) Clicking {selector} in iframe")
                        await element.click()
                        await page.wait_for_timeout(2000)
                        break
                except:
                    continue
            
            # Try clicking at center of iframe
            try:
                await vivid_frame.mouse.click(640, 360)
                await page.wait_for_timeout(2000)
            except:
                pass
            
            # Execute JavaScript in iframe to trigger play
            try:
                await vivid_frame.evaluate("""
                    () => {
                        // Try to play any video elements
                        const videos = document.querySelectorAll('video');
                        videos.forEach(v => {
                            try { v.play(); } catch(e) {}
                        });
                        
                        // Try to trigger player APIs
                        if (typeof jwplayer !== 'undefined') {
                            try { jwplayer().play(); } catch(e) {}
                        }
                        if (typeof videojs !== 'undefined') {
                            try { 
                                const players = videojs.getAllPlayers();
                                players.forEach(p => { try { p.play(); } catch(e) {} });
                            } catch(e) {}
                        }
                    }
                """)
                log.info(f"URL {url_num}) Executed JavaScript in iframe")
            except:
                pass
        
        else:
            # If no vividmosaica iframe, try all iframes
            log.info(f"URL {url_num}) No vividmosaica iframe found, trying all frames...")
            for i, frame in enumerate(frames[1:], 1):  # Skip first frame (main page)
                try:
                    # Try to click in iframe
                    await frame.click("body", timeout=2000)
                    await page.wait_for_timeout(1000)
                    
                    # Try to find and click play button
                    for selector in [".play-button", "button", "video"]:
                        try:
                            element = await frame.wait_for_selector(selector, timeout=1000)
                            if element:
                                await element.click()
                                await page.wait_for_timeout(1000)
                        except:
                            continue
                except:
                    continue
        
        # Also click on main page for good measure
        try:
            await page.mouse.click(640, 360)
            await page.wait_for_timeout(1000)
        except:
            pass
        
        # Wait for m3u8 (up to 30 seconds)
        log.info(f"URL {url_num}) Waiting for m3u8 stream...")
        try:
            await asyncio.wait_for(got_one.wait(), timeout=30)
            if captured:
                return captured[0]
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8")
            
    except Exception as e:
        log.error(f"URL {url_num}) Error: {e}")
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

        # Create context with proper settings
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://sportzone.su/",
            }
        )
        
        for i, ev in enumerate(events, start=1):
            
            page = await context.new_page()
            link = ev["link"]
            
            log.info(f"URL {i}) Opening {link} - {ev['event']}")
            
            try:
                # Navigate to virazo page
                await page.goto(link, wait_until="domcontentloaded", timeout=15000)
                
                # Capture m3u8 using specialized function
                url = await capture_m3u8_from_virazo(page, i, log)

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
                "--autoplay-policy=no-user-gesture-required",
            ],
        )

        await scrape(browser)

        await browser.close()


if __name__ == "__main__":

    asyncio.run(main())
