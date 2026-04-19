import asyncio
from itertools import chain
from functools import partial
from typing import Any
from urllib.parse import urljoin, quote, urlparse
from pathlib import Path
import os
import re

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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)
UA_ENC = quote(USER_AGENT)

OUT_VLC = Path("stgate_vlc.m3u8")
OUT_TIVI = Path("stgate_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=10_800)
API_FILE = Cache(f"{TAG}-api", exp=19_800)

SPORT_ENDPOINTS = [
    "soccer",
    "nfl",
    "nba",
    "cfb",
    "mlb",
    "nhl",
    "ufc",
    "box",
    "f1",
    "olympics",
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
    
    # Fallback to default
    return "https://instream.click/"


def get_event(t1: str, t2: str) -> str:
    if t1 == "RED ZONE":
        return "NFL RedZone"
    if t1 == "TBD":
        return "TBD"
    return f"{t1.strip()} vs {t2.strip()}"

# --------------------------------------------------
async def refresh_api_cache(now_ts: float) -> list[dict[str, Any]]:
    log.info("Refreshing JSON API cache")

    tasks = [
        network.request(
            urljoin(BASE_URL, f"data/{sport}.json"),
            log=log,
        )
        for sport in SPORT_ENDPOINTS
    ]

    results = await asyncio.gather(*tasks)

    data: list[dict[str, Any]] = []

    for sport, r in zip(SPORT_ENDPOINTS, results):
        if not r:
            continue

        try:
            js = r.json()
        except Exception:
            log.warning(f"{sport}.json → invalid JSON (skipped)")
            continue

        if not isinstance(js, list):
            continue

        log.info(f"{sport}.json → {len(js)} events")
        data.extend(js)

    if not data:
        return [{"timestamp": now_ts}]

    for ev in data:
        if "timestamp" in ev:
            ev["ts"] = ev.pop("timestamp")

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
    start_dt = now.delta(hours=-24)
    end_dt = now.delta(minutes=12)

    for ev in api_data:
        date = ev.get("time")
        sport = ev.get("league")
        t1, t2 = ev.get("home"), ev.get("away")

        if not (date and sport and t1 and t2):
            continue

        event = get_event(t1, t2)
        key = f"[{sport}] {event} ({TAG})"

        if key in cached_keys:
            continue

        event_dt = Time.from_str(date, timezone="UTC")
        if not start_dt <= event_dt <= end_dt:
            continue

        streams = ev.get("streams") or []
        if not streams:
            continue

        link = streams[0].get("url")
        if not link:
            continue

        events.append({
            "sport": sport,
            "event": event,
            "link": link,
            "timestamp": event_dt.timestamp(),
        })

    return events

# --------------------------------------------------
async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load() or {}
    cached_count = len(cached_urls)

    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} cached event(s)")
    log.info(f'Scraping JSON from "{BASE_URL}/data"')

    events = await get_events(list(cached_urls.keys()))
    log.info(f"Processing {len(events)} new stream URL(s)")

    if not events:
        CACHE_FILE.write(cached_urls)
        return

    async with network.event_context(browser, stealth=False) as context:
        for i, ev in enumerate(events, start=1):
            async with network.event_page(context) as page:
                handler = partial(
                    network.process_event,
                    url=ev["link"],
                    url_num=i,
                    page=page,
                    log=log,
                )

                stream_url = await network.safe_process(
                    handler,
                    url_num=i,
                    semaphore=network.PW_S,
                    log=log,
                )

                if not stream_url:
                    continue

                # Build the correct referer from the stream URL
                referer = build_referer_from_stream(stream_url)
                
                key = f"[{ev['sport']}] {ev['event']} ({TAG})"
                tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

                cached_urls[key] = {
                    "url": stream_url,
                    "logo": logo,
                    "base": BASE_URL,
                    "timestamp": ev["timestamp"],
                    "id": tvg_id or "Live.Event.us",
                    "link": ev["link"],
                    "referer": referer,  # Store the computed referer
                }

    CACHE_FILE.write(cached_urls)
    build_playlists(cached_urls)

    log.info(f"Collected and cached {len(cached_urls) - cached_count} new event(s)")

# --------------------------------------------------
def build_playlists(data: dict[str, dict]):
    vlc = ["#EXTM3U"]
    tm = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        stream_url = e["url"]
        
        # Get the referer from stored value or compute it
        referer = e.get("referer")
        if not referer:
            referer = build_referer_from_stream(stream_url)
        
        vlc.extend([
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={referer}",
            f"#EXTVLCOPT:http-origin={referer}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            stream_url,
            "",  # Empty line for separation
        ])

        tm.extend([
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f'{stream_url}|referer={referer}|origin={referer}|user-agent={UA_ENC}',
            "",  # Empty line for separation
        ])

        ch += 1

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tm), encoding="utf-8")

    log.info(f"Playlists written successfully with {ch-1} channels")
    log.info(f"  - {OUT_VLC}")
    log.info(f"  - {OUT_TIVI}")

# --------------------------------------------------
async def main():
    log.info("Starting STGATE updater")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        await scrape(browser)
        await browser.close()

# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
