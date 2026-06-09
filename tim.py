import asyncio
import os
import re
import json
from urllib.parse import quote

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIM"

CACHE_FILE = Cache(TAG, exp=10_800)

API_URL = "https://api.saduvisvesvaraya.workers.dev/api/live-upcoming"

HEADERS = {
    "Referer": "https://junkieembeds.pages.dev/",
    "Origin": "https://junkieembeds.pages.dev/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


def generate_playlists():
    """Generate VLC and TiviMate M3U8 playlists from captured streams."""
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

        safe_name = name.replace('"', '').replace("'", "")
        extinf = (
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" '
            f'tvg-name="{safe_name}" tvg-logo="{logo}" group-title="Live Events",{safe_name}'
        )

        vlc_lines.append(extinf)
        vlc_lines.append(f"#EXTVLCOPT:http-referrer={base}")
        vlc_lines.append(f"#EXTVLCOPT:http-origin={base}")
        vlc_lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc_lines.append(url)

        tivimate_lines.append(extinf)
        tiv_url = f"{url}|referer={base}|origin={base}|user-agent={ua_encoded}"
        tivimate_lines.append(tiv_url)

    with open("tim_vlc.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(vlc_lines))
    with open("tim_tivimate.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(tivimate_lines))

    log.info(f"Playlists generated: {len(urls)} streams -> tim_vlc.m3u8 / tim_tivimate.m3u8")


async def capture_m3u8_from_embed(page, embed_url: str, url_num: int, timeout: int = 45) -> str | None:
    """
    Navigate to embed URL and capture the m3u8 stream URL.
    Listens for fetch requests to /fetch endpoint and extracts m3u8 URL.
    """
    captured_m3u8 = []
    got_m3u8 = asyncio.Event()
    seen_urls = set()

    def is_valid_m3u8(url: str) -> bool:
        url_lower = url.lower()
        return (
            ".m3u8" in url_lower and
            "manifest" not in url_lower and
            "analytics" not in url_lower and
            "tracking" not in url_lower and
            "hmembeds" not in url_lower and
            "junkieembeds" not in url_lower
        )

    async def handle_request(request):
        req_url = request.url
        # Check for fetch requests to /fetch endpoint - this returns the m3u8 URL
        if "/fetch" in req_url:
            log.info(f"URL {url_num}) Fetch request detected: {req_url[:120]}...")
            try:
                # Wait for response to get the actual m3u8 URL
                response = await request.response()
                if response:
                    body = await response.text()
                    # The response body is the m3u8 URL
                    if body and is_valid_m3u8(body) and body not in seen_urls:
                        seen_urls.add(body)
                        captured_m3u8.append(body)
                        got_m3u8.set()
                        log.info(f"URL {url_num}) M3U8 from fetch response: {body[:120]}...")
            except:
                pass
        
        if is_valid_m3u8(req_url) and req_url not in seen_urls:
            seen_urls.add(req_url)
            captured_m3u8.append(req_url)
            got_m3u8.set()
            log.info(f"URL {url_num}) M3U8 request: {req_url[:120]}...")

    async def handle_response(response):
        resp_url = response.url
        if is_valid_m3u8(resp_url) and resp_url not in seen_urls:
            seen_urls.add(resp_url)
            captured_m3u8.append(resp_url)
            got_m3u8.set()
            log.info(f"URL {url_num}) M3U8 response: {resp_url[:120]}...")

        # Check response body for m3u8 URLs
        try:
            content_type = response.headers.get("content-type", "").lower()
            if "json" in content_type or "text" in content_type:
                body = await response.text()
                # Look for m3u8 URLs in the response body
                matches = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', body)
                for match in matches:
                    if is_valid_m3u8(match) and match not in seen_urls:
                        seen_urls.add(match)
                        captured_m3u8.append(match)
                        got_m3u8.set()
                        log.info(f"URL {url_num}) M3U8 in response body: {match[:120]}...")
        except:
            pass

        try:
            content_type = response.headers.get("content-type", "").lower()
            if "mpegurl" in content_type or "application/vnd.apple.mpegurl" in content_type:
                if is_valid_m3u8(resp_url) and resp_url not in seen_urls:
                    seen_urls.add(resp_url)
                    captured_m3u8.append(resp_url)
                    got_m3u8.set()
                    log.info(f"URL {url_num}) M3U8 by content-type: {resp_url[:120]}...")
        except:
            pass

    page.on("request", handle_request)
    page.on("response", handle_response)

    try:
        log.info(f"URL {url_num}) Navigating to embed: {embed_url}")

        # Navigate to embed page
        await page.goto(embed_url, wait_until="domcontentloaded", timeout=15000)

        # Wait for page to load and player to initialize
        await asyncio.sleep(3)

        # Execute JavaScript to monitor JWPlayer and extract m3u8 URL
        js_code = """
        () => {
            const results = [];
            
            // Check if JWPlayer is available
            if (typeof jwplayer !== 'undefined') {
                try {
                    const player = jwplayer();
                    if (player && player.getConfig) {
                        const config = player.getConfig();
                        if (config.file) results.push(config.file);
                        if (config.sources) {
                            config.sources.forEach(s => {
                                if (s.file && s.file.includes('.m3u8')) results.push(s.file);
                            });
                        }
                    }
                    // Get current playlist
                    const playlist = player.getPlaylist();
                    if (playlist && playlist.length) {
                        playlist.forEach(item => {
                            if (item.file && item.file.includes('.m3u8')) results.push(item.file);
                            if (item.sources) {
                                item.sources.forEach(s => {
                                    if (s.file && s.file.includes('.m3u8')) results.push(s.file);
                                });
                            }
                        });
                    }
                } catch(e) {}
            }
            
            // Check all script tags for embedded m3u8 URLs
            document.querySelectorAll('script').forEach(script => {
                if (script.textContent) {
                    const matches = script.textContent.match(/https?:\\/\\/[^\\s"']+\\.m3u8[^\\s"']*/gi);
                    if (matches) results.push(...matches);
                }
            });
            
            // Check video elements
            document.querySelectorAll('video').forEach(video => {
                if (video.src && video.src.includes('.m3u8')) results.push(video.src);
            });
            
            // Remove duplicates
            return [...new Set(results)];
        }
        """
        
        try:
            js_results = await page.evaluate(js_code)
            for url in js_results:
                if is_valid_m3u8(url) and url not in seen_urls:
                    seen_urls.add(url)
                    captured_m3u8.append(url)
                    got_m3u8.set()
                    log.info(f"URL {url_num}) Found m3u8 in JWPlayer: {url[:120]}...")
        except Exception as e:
            log.debug(f"URL {url_num}) JS evaluation error: {e}")

        # Wait for m3u8 capture with timeout
        try:
            await asyncio.wait_for(got_m3u8.wait(), timeout=timeout)
            log.info(f"URL {url_num}) M3U8 captured!")
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timeout waiting for M3U8 after {timeout}s")

        if captured_m3u8:
            return captured_m3u8[0]

        # Fallback: Search full HTML content
        try:
            html = await page.content()
            patterns = [
                r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
                r'file:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'src:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
            ]
            for pattern in patterns:
                matches = re.findall(pattern, html)
                for match in matches:
                    if is_valid_m3u8(match) and match not in seen_urls:
                        log.info(f"URL {url_num}) Found in HTML: {match[:120]}...")
                        return match
        except Exception as e:
            log.debug(f"URL {url_num}) HTML search error: {e}")

        log.warning(f"URL {url_num}) No m3u8 found")
        return None

    except Exception as e:
        log.error(f"URL {url_num}) Error: {e}")
        return None

    finally:
        page.remove_listener("request", handle_request)
        page.remove_listener("response", handle_response)


async def fetch_events_from_api(cached_keys: set) -> list[dict]:
    """Fetch live and upcoming events from the API."""
    events = []
    log.info(f"Fetching events from API: {API_URL}")

    try:
        response = await network.request(API_URL, headers=HEADERS, log=log, timeout=30)
        if not response:
            log.error("Failed to fetch from API - no response")
            return events

        try:
            data = json.loads(response.content)
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse JSON: {e}")
            return events

        events_list = data.get("events", [])
        if not events_list:
            log.warning("No events found in API response")
            return events

        genres = data.get("genres", {})
        log.info(f"Found {len(events_list)} events in API response")

        for event in events_list:
            event_name = event.get("name", "")
            event_logo = event.get("logo", "")
            genre_id = event.get("genre", 0)
            streams = event.get("streams", [])
            
            if not event_name or not streams:
                continue

            sport = genres.get(str(genre_id), f"Genre_{genre_id}")
            sport_map = {
                "Soccer": "Soccer",
                "Motorsport": "Motorsport",
                "MMA (Mixed Martial Arts)": "MMA",
                "FCC (Full-Contact Combat Sports)": "Fight",
                "Boxing": "Boxing",
                "Wrestling": "Wrestling",
                "Basketball": "Basketball",
                "American Football": "Football",
                "Baseball": "Baseball",
                "Tennis": "Tennis",
                "Hockey": "Hockey",
            }
            sport = sport_map.get(sport, sport)

            for stream in streams:
                stream_name = stream.get("name", "")
                stream_url = stream.get("url", "")
                
                if not stream_url:
                    continue
                
                if not stream_url.startswith("http"):
                    embed_full_url = f"https://junkieembeds.pages.dev/{stream_url}"
                else:
                    embed_full_url = stream_url
                
                stream_suffix = f" - {stream_name}" if stream_name else ""
                key = f"[{sport}] {event_name}{stream_suffix} ({TAG})"
                
                if key in cached_keys:
                    continue
                
                events.append({
                    "key": key,
                    "sport": sport,
                    "event": event_name,
                    "stream_name": stream_name,
                    "url": embed_full_url,
                    "logo": event_logo,
                })
                
                log.info(f"New event: [{sport}] {event_name} - {stream_name or 'Main'}")

    except Exception as e:
        log.error(f"Error fetching events: {e}")

    return events


def get_tvg_info(sport: str, event_name: str) -> tuple[str, str]:
    """Get TVG ID and logo for event."""
    try:
        tvg_id, logo = leagues.get_tvg_info(sport, event_name)
        return tvg_id, logo
    except Exception:
        return "Live.Event.us", ""


async def scrape(browser: Browser) -> None:
    """Main scraping function."""
    cached_urls = CACHE_FILE.load()
    cached_keys = set(cached_urls.keys())
    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}
    urls.update(valid_urls)
    log.info(f"Loaded {len(valid_urls)} valid event(s) from cache")

    events = await fetch_events_from_api(cached_keys)

    if not events:
        log.info("No new events to process")
        generate_playlists()
        return

    log.info(f"Processing {len(events)} new event(s)")

    now = Time.clean(Time.now())
    successful_count = 0

    async with network.event_context(browser, stealth=False) as context:
        for i, event in enumerate(events, start=1):
            log.info(f"--- [{i}/{len(events)}]: {event['event']} ({event['stream_name']}) ---")

            async with network.event_page(context) as page:
                # Set extra headers
                await page.set_extra_http_headers(HEADERS)

                m3u8_url = await capture_m3u8_from_embed(
                    page=page,
                    embed_url=event["url"],
                    url_num=i,
                    timeout=50,
                )

                tvg_id, logo = get_tvg_info(event["sport"], event["event"])
                final_logo = event["logo"] or logo

                entry = {
                    "url": m3u8_url,
                    "logo": final_logo,
                    "base": "https://junkieembeds.pages.dev/",
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                    "link": event["url"],
                    "sport": event["sport"],
                    "stream_name": event["stream_name"],
                }

                cached_urls[event["key"]] = entry

                if m3u8_url:
                    successful_count += 1
                    urls[event["key"]] = entry
                    log.info(f"✓ [{i}] Stream captured!")
                    log.info(f"   M3U8: {m3u8_url[:100]}...")
                else:
                    log.warning(f"✗ [{i}] No stream captured")
                
                # Small delay between requests
                await asyncio.sleep(2)

    log.info(f"Scraping complete: {successful_count}/{len(events)} streams captured")
    CACHE_FILE.write(cached_urls)
    generate_playlists()


from playwright.async_api import async_playwright


async def main():
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
