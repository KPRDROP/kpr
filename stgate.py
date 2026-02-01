import asyncio
from functools import partial
from typing import Any
from urllib.parse import quote
from pathlib import Path
import os

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

DATA_BASE = f"{BASE_URL.rstrip('/')}/data"

REFERER = "https://instreams.click/"
ORIGIN = "https://instreams.click/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)
UA_ENC = quote(USER_AGENT)

OUT_VLC = Path("stgate_vlc.m3u8")
OUT_TIVI = Path("stgate_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=10_800)
API_FILE = Cache(f"{TAG}-api", exp=10_800)

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
]

# --------------------------------------------------
def build_event_name(home: str, away: str) -> str:
    home = (home or "").strip()
    away = (away or "").strip()
    if not home or not away:
        return "TBD"
    return f"{home} vs {away}"

# --------------------------------------------------
async def refresh_api_cache(now_ts: float) -> list[dict[str, Any]]:
    log.info("Refreshing JSON API cache")

    events: list[dict[str, Any]] = []

    for sport in SPORT_ENDPOINTS:
        url = f"{DATA_BASE}/{sport}.json"
        res = await network.request(url, log=log)

        if not res:
            log.warning(f"No response for {sport}.json")
            continue

        try:
            data = res.json()
        except Exception as e:
            log.warning(f"Invalid JSON in {sport}.json: {e}")
            continue

        if not isinstance(data, list):
            continue

        log.info(f"{sport}.json → {len(data)} events")
        events.extend(data)

    if not events:
        log.warning("No events found in any JSON endpoint")
        return []

    events.append({"_cache_ts": now_ts})
    return events

# --------------------------------------------------
async def get_events(cached_keys: list[str]) -> list[dict[str, Any]]:
    now = Time.clean(Time.now())

    api_data = API_FILE.load(per_entry=False, index=-1)
    if not api_data:
        api_data = await refresh_api_cache(now.timestamp())
        API_FILE.write(api_data)

    events = []
    start_dt = now.delta(hours=-1)
    end_dt = now.delta(minutes=15)

    for ev in api_data:
        if "_cache_ts" in ev:
            continue

        home = ev.get("home")
        away = ev.get("away")
        league = ev.get("league")
        ts = ev.get("timestamp")
        streams = ev.get("streams") or []

        if not (home and away and league and ts and streams):
            continue

        event_name = build_event_name(home, away)
        event_dt = Time.from_ts(ts)

        if not start_dt <= event_dt <= end_dt:
            continue

        for idx, s in enumerate(streams, start=1):
            link = s.get("url")
            if not link:
                continue

            key = f"[{league}] {event_name} #{idx} ({TAG})"
            if key in cached_keys:
                continue

            events.append({
                "sport": league,
                "event": event_name,
                "link": link,
                "timestamp": event_dt.timestamp(),
                "key": key,
            })

    return events

# --------------------------------------------------
async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load() or {}
    cached_count = len(cached_urls)

    log.info(f"Loaded {cached_count} cached event(s)")
    log.info(f'Scraping JSON from "{DATA_BASE}"')

    events = await get_events(list(cached_urls.keys()))
    log.info(f"Processing {len(events)} new stream URL(s)")

    if not events:
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

                tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

                cached_urls[ev["key"]] = {
                    "url": stream_url,
                    "logo": logo,
                    "base": BASE_URL,
                    "timestamp": ev["timestamp"],
                    "id": tvg_id or "Live.Event.us",
                    "link": ev["link"],
                }

    CACHE_FILE.write(cached_urls)
    build_playlists(cached_urls)

    log.info(f"Collected {len(cached_urls) - cached_count} new event(s)")

# --------------------------------------------------
def build_playlists(data: dict[str, dict]):
    vlc = ["#EXTM3U"]
    tm = ["#EXTM3U"]

    ch = 1
    for name, e in data.items():
        vlc.extend([
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={REFERER}",
            f"#EXTVLCOPT:http-origin={ORIGIN}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        tm.extend([
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f'{e["url"]}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}',
        ])

        ch += 1

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tm), encoding="utf-8")
    log.info("Playlists written successfully")

# --------------------------------------------------
async def main():
    log.info("🚀 Starting STGATE scraper")
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
