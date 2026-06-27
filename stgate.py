import asyncio
from itertools import chain
from functools import partial
from typing import Any
from urllib.parse import urljoin, quote, urlparse
from pathlib import Path
import os
import re
import json

from playwright.async_api import async_playwright, Browser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

TAG = "STGATE"

BASE_URL = os.environ.get("STGATE_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing STGATE_BASE_URL secret")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"
)

UA_ENC = quote(USER_AGENT)

OUT_VLC = Path("stgate_vlc.m3u8")
OUT_TIVI = Path("stgate_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=10_800)
API_FILE = Cache(f"{TAG}-api", exp=19_800)

# Expanded sports endpoints for more events
SPORT_ENDPOINTS = [
    "soccer",
    #"nfl",
    #"nba",
    #"cfb",
    "mlb",
    #"nhl",
    "ufc",
    "box",
    "f1",
    #"olympics",
]

urls: dict[str, dict[str, Any]] = {}

# --------------------------------------------------
def extract_stream_id(stream_url: str) -> str | None:
    """Extract stream ID from the M3U8 URL"""
    if not stream_url:
        return None
    
    # Pattern to match stream ID from URL: /US/STREAM_ID/index.m3u8
    patterns = [
        r'/US/([^/]+)/index\.m3u8',
        r'/([A-Z0-9]+)/index\.m3u8',
        r'stream=([A-Z0-9]+)',
        r'/stream/([A-Z0-9]+)\.m3u8',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, stream_url, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return None


def build_referer_from_stream(stream_url: str) -> str:
    """Build the correct referer URL based on stream URL"""
    stream_id = extract_stream_id(stream_url)
    
    if stream_id:
        return f"https://instream.click/jwp-us.php?stream={stream_id}"
    
    # Try to extract from URL path
    parsed = urlparse(stream_url)
    if parsed.path:
        parts = parsed.path.split('/')
        if len(parts) > 2 and parts[1].upper() in ['US', 'CA', 'UK']:
            # /US/STREAM_ID/index.m3u8
            if len(parts) > 2:
                return f"https://instream.click/jwp-us.php?stream={parts[2]}"
    
    # Fallback to default
    return "https://instream.click/"


def get_event(t1: str, t2: str) -> str:
    if t1 == "RED ZONE":
        return "NFL RedZone"
    if t1 == "TBD":
        return "TBD"
    return f"{t1.strip()} vs {t2.strip()}"


def clean_sport_name(sport: str) -> str:
    """Clean and standardize sport names"""
    sport_map = {
        'soccer': 'Football',
        'nfl': 'American Football',
        'nba': 'Basketball',
        'cfb': 'NCAA Football',
        'mlb': 'Baseball',
        'nhl': 'Hockey',
        'ufc': 'Fight MMA',
        'box': 'Boxing',
        'f1': 'Motor Sport',
        'olympics': 'Olympics',
    }
    return sport_map.get(sport.lower(), sport)

# --------------------------------------------------
async def refresh_api_cache(now_ts: float) -> list[dict[str, Any]]:
    log.info("Refreshing JSON API cache")

    # Simple requests without retries parameter
    tasks = [
        network.request(
            urljoin(BASE_URL, f"data/{sport}.json"),
            log=log,
        )
        for sport in SPORT_ENDPOINTS
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    data: list[dict[str, Any]] = []

    for sport, r in zip(SPORT_ENDPOINTS, results):
        if isinstance(r, Exception):
            log.warning(f"{sport}.json → request failed: {str(r)[:50]}")
            continue
            
        if not r:
            continue

        try:
            js = r.json()
        except Exception as e:
            log.warning(f"{sport}.json → invalid JSON: {str(e)[:50]}")
            continue

        if not isinstance(js, list):
            continue

        log.info(f"{sport}.json → {len(js)} events")
        
        # Process and clean events
        for ev in js:
            if "timestamp" in ev:
                ev["ts"] = ev.pop("timestamp")
            # Store sport info for later
            ev["_sport"] = clean_sport_name(sport)
        
        data.extend(js)

    if not data:
        return [{"timestamp": now_ts}]

    data[-1]["timestamp"] = now_ts
    return data

# --------------------------------------------------
async def get_events(cached_keys: list[str]) -> list[dict[str, Any]]:
    now = Time.clean(Time.now())

    api_data = API_FILE.load()
    if not api_data:
        api_data = await refresh_api_cache(now.timestamp())
        API_FILE.write(api_data)

    events = []
    
    # Extended time window to catch more events
    start_dt = now.delta(hours=-48)  # Increased from -24 to -48
    end_dt = now.delta(hours=6)      # Increased from 12 minutes to 6 hours

    # Track events to avoid duplicates
    seen_events = set()

    for ev in api_data:
        date = ev.get("time")
        sport = ev.get("league") or ev.get("_sport")
        t1, t2 = ev.get("home"), ev.get("away")

        if not (date and sport and t1 and t2):
            continue

        event = get_event(t1, t2)
        key = f"[{sport}] {event} ({TAG})"

        # Skip if already cached
        if key in cached_keys:
            continue

        # Create a unique identifier for this event to avoid duplicates
        event_id = f"{sport}_{t1}_{t2}_{date[:10]}"
        if event_id in seen_events:
            continue
        seen_events.add(event_id)

        event_dt = Time.from_str(date, timezone="UTC")
        if not start_dt <= event_dt <= end_dt:
            continue

        streams = ev.get("streams") or []
        if not streams:
            continue

        # Try multiple streams if available
        stream_urls = []
        for stream in streams:
            url = stream.get("url")
            if url:
                stream_urls.append(url)
        
        if not stream_urls:
            continue

        # Use first valid stream
        link = stream_urls[0]

        # Add stream info for logging
        stream_count = len(stream_urls)
        if stream_count > 1:
            log.debug(f"Event {event} has {stream_count} streams available")

        events.append({
            "sport": clean_sport_name(sport),
            "event": event,
            "link": link,
            "timestamp": event_dt.timestamp(),
            "stream_count": stream_count,
            "all_streams": stream_urls,  # Store all streams for potential fallback
        })

    # Sort by timestamp, most recent first
    events.sort(key=lambda x: x["timestamp"], reverse=True)
    
    log.info(f"Found {len(events)} new events to process")
    return events

# --------------------------------------------------
async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load() or {}
    cached_count = len(cached_urls)

    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} cached event(s)")
    log.info(f'Scraping JSON from "{BASE_URL}/data"')

    events = await get_events(list(cached_urls.keys()))
    
    if not events:
        log.info("No new events found")
        build_playlists(cached_urls)
        return

    log.info(f"Processing {len(events)} new stream URL(s)")

    async with network.event_context(browser, stealth=False) as context:
        processed_count = 0
        failed_count = 0
        
        for i, ev in enumerate(events, start=1):
            try:
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=ev["link"],
                        url_num=i,
                        page=page,
                        log=log,
                        timeout=25,  # Increased timeout for better reliability
                    )

                    stream_url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    if not stream_url:
                        # Try fallback stream if available
                        if ev.get("all_streams") and len(ev["all_streams"]) > 1:
                            log.info(f"Trying fallback stream for {ev['event']}")
                            for fallback_url in ev["all_streams"][1:3]:  # Try next 2 streams
                                try:
                                    fallback_handler = partial(
                                        network.process_event,
                                        url=fallback_url,
                                        url_num=i,
                                        page=page,
                                        log=log,
                                        timeout=20,
                                    )
                                    stream_url = await network.safe_process(
                                        fallback_handler,
                                        url_num=i,
                                        semaphore=network.PW_S,
                                        log=log,
                                    )
                                    if stream_url:
                                        log.info(f"Fallback stream successful for {ev['event']}")
                                        break
                                except Exception:
                                    continue
                        
                        if not stream_url:
                            failed_count += 1
                            continue

                    # Build the correct referer from the stream URL
                    referer = build_referer_from_stream(stream_url)
                    
                    key = f"[{ev['sport']}] {ev['event']} ({TAG})"
                    tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

                    # Clean stream URL (remove tracking parameters)
                    clean_stream_url = stream_url.split("?st")[0]
                    
                    cached_urls[key] = {
                        "url": clean_stream_url,
                        "logo": logo,
                        "base": BASE_URL,
                        "timestamp": ev["timestamp"],
                        "id": tvg_id or "Live.Event.us",
                        "link": ev["link"],
                        "referer": referer,
                        "stream_count": ev.get("stream_count", 1),
                    }
                    
                    processed_count += 1

            except Exception as e:
                log.error(f"Error processing event {i}: {str(e)[:100]}")
                failed_count += 1
                continue

    # Clean old cache entries (older than 48 hours)
    if cached_urls:
        now = Time.clean(Time.now())
        expired_keys = []
        for key, data in cached_urls.items():
            if data.get("timestamp", 0) < now.delta(hours=-48).timestamp():
                expired_keys.append(key)
        
        if expired_keys:
            log.info(f"Removing {len(expired_keys)} expired cache entries")
            for key in expired_keys:
                del cached_urls[key]

    CACHE_FILE.write(cached_urls)
    build_playlists(cached_urls)

    log.info(f"Successfully processed {processed_count} new event(s)")
    if failed_count > 0:
        log.warning(f"Failed to process {failed_count} event(s)")

# --------------------------------------------------
def build_playlists(data: dict[str, dict]):
    if not data:
        log.warning("No data to build playlists")
        # Create empty playlists
        OUT_VLC.write_text("#EXTM3U\n", encoding="utf-8")
        OUT_TIVI.write_text("#EXTM3U\n", encoding="utf-8")
        return

    vlc = ["#EXTM3U"]
    tm = ["#EXTM3U"]
    ch = 1

    # Sort by sport for better organization
    sorted_items = sorted(data.items(), key=lambda x: (x[1].get("sport", "ZZZ"), x[0]))

    for name, e in sorted_items:
        stream_url = e["url"]
        
        # Get the referer from stored value or compute it
        referer = e.get("referer")
        if not referer:
            referer = build_referer_from_stream(stream_url)
        
        # Clean stream URL again just in case
        stream_url = stream_url.split("?st")[0]
        
        vlc_lines = [
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={referer}",
            f"#EXTVLCOPT:http-origin={referer}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            stream_url,
            "",  # Empty line for separation
        ]
        vlc.extend(vlc_lines)

        tm_lines = [
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f'{stream_url}|referer={referer}|origin={referer}|user-agent={UA_ENC}',
            "",  # Empty line for separation
        ]
        tm.extend(tm_lines)

        ch += 1

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tm), encoding="utf-8")

    log.info(f"Playlists written successfully with {ch-1} channels")
    log.info(f"  - {OUT_VLC}")
    log.info(f"  - {OUT_TIVI}")

# --------------------------------------------------
async def main():
    log.info("Starting STGATE updater")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--autoplay-policy=no-user-gesture-required",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
            await scrape(browser)
            await browser.close()
    except Exception as e:
        log.error(f"Fatal error in main: {str(e)[:200]}")
        # Try to save whatever we have
        cached_urls = CACHE_FILE.load() or {}
        if cached_urls:
            build_playlists(cached_urls)
        raise

# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
