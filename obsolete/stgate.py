import asyncio
import json
import os
import re
from functools import partial
from itertools import chain
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, quote, urlparse

from playwright.async_api import async_playwright, Browser
from selectolax.lexbor import LexborHTMLParser as HTMLParser

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
    "nfl",
    "nba",
    "cfb",
    "mlb",
    #"nhl",
    "ufc",
    "box",
    "f1",
]

urls: dict[str, dict[str, Any]] = {}

# --------------------------------------------------
# Regex patterns (revised from original working code)
# --------------------------------------------------

VALID_M3U8 = re.compile(
    r"""(?:file|source|streamurls?|stream_url|url)\s*[:=]\s*['"]([^'"]+)['"]""",
    re.I,
)

VALID_M3U8_ARRAY = re.compile(
    r"""(?:streamurls|sources|0x31c4)\s*[:=]\s*\[\s*['"]([^'"]+)['"]""",
    re.I,
)

VALID_M3U8_ALT = re.compile(
    r"""(?:file|source|streamurls?)\s*(?::|=)\s*(?:'|")([^"']*)(?:'|")""",
    re.I,
)

# --------------------------------------------------
def extract_stream_id(stream_url: str) -> str | None:
    """Extract stream ID from the M3U8 URL"""
    if not stream_url:
        return None

    patterns = [
        r"/US/([^/]+)/index\.m3u8",
        r"/([A-Z0-9]+)/index\.m3u8",
        r"stream=([A-Z0-9]+)",
        r"/stream/([A-Z0-9]+)\.m3u8",
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

    parsed = urlparse(stream_url)
    if parsed.path:
        parts = parsed.path.split("/")
        if len(parts) > 2 and parts[1].upper() in ["US", "CA", "UK"]:
            if len(parts) > 2:
                return f"https://instream.click/jwp-us.php?stream={parts[2]}"

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
        "soccer": "Football",
        "nfl": "American Football",
        "nba": "Basketball",
        "cfb": "NCAA Football",
        "mlb": "Baseball",
        "nhl": "Hockey",
        "ufc": "Fight MMA",
        "box": "Boxing",
        "f1": "Motor Sport",
        "olympics": "Olympics",
    }
    return sport_map.get(sport.lower(), sport)


def clean_m3u(s: str) -> str:
    return re.sub(r"\.live\n", ".pro", s)


# --------------------------------------------------
def parse_m3u8_from_text(text: str) -> str | None:
    """Extract M3U8 URL from page text using multiple regex strategies."""
    # Strategy 1: primary pattern with named group
    if match := VALID_M3U8.search(text):
        try:
            return json.loads(f'"{match[1]}"')
        except (json.JSONDecodeError, IndexError):
            return match[1]

    # Strategy 2: array style (streamurls = ["..."])
    if match := VALID_M3U8_ARRAY.search(text):
        try:
            return json.loads(f'"{match[1]}"')
        except (json.JSONDecodeError, IndexError):
            return match[1]

    # Strategy 3: fallback alternate pattern
    if match := VALID_M3U8_ALT.search(text):
        try:
            return json.loads(f'"{match[1]}"')
        except (json.JSONDecodeError, IndexError):
            return match[1]

    return None


# --------------------------------------------------
async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    """Extract M3U8 stream URL and referer from an event page.

    Works for both direct iframe URLs and page URLs containing an iframe.
    Returns (m3u8_url, iframe_src) or (None, None) on failure.
    """
    nones = None, None

    if not (event_data := await network.request(url, url_num, log=log)):
        return nones

    if re.search(r"^https?://instreams?", url.lower()):
        # Direct iframe URL: page content is the iframe data itself
        ifr_src, ifr_src_data_text = url, event_data.text
    else:
        soup = HTMLParser(event_data.content)

        ifr = soup.css_first("iframe")

        if not ifr or not (src := ifr.attributes.get("src")):
            log.warning(f"URL {url_num}) No iframe element found.")
            return nones

        ifr_src = network.ensure_https(src)

        if not (
            ifr_src_data := await network.request(
                ifr_src,
                url_num,
                headers={"Referer": url},
                log=log,
            )
        ):
            return nones

        ifr_src_data_text = ifr_src_data.text

    if stream_url := parse_m3u8_from_text(ifr_src_data_text):
        log.info(f"URL {url_num}) Captured M3U8")
        return stream_url, ifr_src

    log.warning(f"URL {url_num}) No source found.")
    return nones


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

        for ev in js:
            if "timestamp" in ev:
                ev["ts"] = ev.pop("timestamp")
            ev["_sport"] = clean_sport_name(sport)

        data.extend(js)

    if not data:
        return [{"timestamp": now_ts}]

    data[-1]["timestamp"] = now_ts
    return data


# --------------------------------------------------
async def get_events(cached_keys: list[str]) -> list[dict[str, Any]]:
    now = Time.rn()

    api_data = API_FILE.load(per_entry=False, ts_index=-1)
    if not api_data:
        log.info("Refreshing API cache")
        api_data = await refresh_api_cache(now.timestamp())
        API_FILE.write(api_data)

    events: list[dict[str, Any]] = []

    start_dt = now.delta(hours=-48)
    end_dt = now.delta(hours=6)

    seen_events: set[str] = set()

    for ev in api_data:
        date = ev.get("ts") or ev.get("time")
        sport = ev.get("league") or ev.get("_sport")
        t1, t2 = ev.get("home"), ev.get("away")

        if not (date and sport and t1 and t2):
            continue

        if isinstance(date, (int, float)):
            event_dt = Time.from_ts(date)
        else:
            event_dt = Time.from_str(str(date), timezone="UTC")

        if not start_dt <= event_dt <= end_dt:
            continue

        event = get_event(t1, t2)
        sport = clean_sport_name(sport)
        key = f"[{sport}] {event} ({TAG})"

        if key in cached_keys:
            continue

        event_id = f"{sport}_{t1}_{t2}_{str(date)[:10]}"
        if event_id in seen_events:
            continue
        seen_events.add(event_id)

        streams = ev.get("streams") or []
        if not streams:
            continue

        # Collect stream URLs, skipping auto_source entries (matches original)
        stream_urls: list[str] = []
        for stream in streams:
            if "auto_source" in stream:
                continue
            url = stream.get("url")
            if url:
                stream_urls.append(url)

        if not stream_urls:
            continue

        events.append(
            {
                "sport": sport,
                "event": event,
                "link": stream_urls[0],
                "timestamp": event_dt.timestamp(),
                "stream_count": len(stream_urls),
                "all_streams": stream_urls,
            }
        )

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

    processed_count = 0
    failed_count = 0

    for i, ev in enumerate(events, start=1):
        try:
            handler = partial(
                process_event,
                url=ev["link"],
                url_num=i,
            )

            stream_url, iframe_src = await network.safe_process(
                handler,
                url_num=i,
                timeout_return=(None, None),
                semaphore=network.HTTP_S,
                log=log,
            )

            # Fallback: try alternate streams if primary fails
            if not stream_url and ev.get("all_streams") and len(ev["all_streams"]) > 1:
                log.info(f"Trying fallback streams for {ev['event']}")
                for fallback_url in ev["all_streams"][1:3]:
                    fallback_handler = partial(
                        process_event,
                        url=fallback_url,
                        url_num=i,
                    )
                    stream_url, iframe_src = await network.safe_process(
                        fallback_handler,
                        url_num=i,
                        timeout_return=(None, None),
                        semaphore=network.HTTP_S,
                        log=log,
                    )
                    if stream_url:
                        log.info(f"Fallback stream successful for {ev['event']}")
                        break

            if not stream_url:
                failed_count += 1
                continue

            referer = iframe_src or build_referer_from_stream(stream_url)

            key = f"[{ev['sport']}] {ev['event']} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

            clean_stream_url = clean_m3u(stream_url.split("?st")[0])

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
        now = Time.rn()
        expired_keys = [
            key
            for key, data in cached_urls.items()
            if data.get("timestamp", 0) < now.delta(hours=-48).timestamp()
        ]

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
def build_playlists(data: dict[str, dict]) -> None:
    if not data:
        log.warning("No data to build playlists")
        OUT_VLC.write_text("#EXTM3U\n", encoding="utf-8")
        OUT_TIVI.write_text("#EXTM3U\n", encoding="utf-8")
        return

    vlc = ["#EXTM3U"]
    tm = ["#EXTM3U"]
    ch = 1

    sorted_items = sorted(
        data.items(), key=lambda x: (x[1].get("sport", "ZZZ"), x[0])
    )

    for name, e in sorted_items:
        stream_url = e["url"]

        referer = e.get("referer")
        if not referer:
            referer = build_referer_from_stream(stream_url)

        stream_url = stream_url.split("?st")[0]

        vlc_lines = [
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={referer}",
            f"#EXTVLCOPT:http-origin={referer}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            stream_url,
            "",
        ]
        vlc.extend(vlc_lines)

        tm_lines = [
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f"{stream_url}|referer={referer}|origin={referer}|user-agent={UA_ENC}",
            "",
        ]
        tm.extend(tm_lines)

        ch += 1

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tm), encoding="utf-8")

    log.info(f"Playlists written successfully with {ch - 1} channels")
    log.info(f"  - {OUT_VLC}")
    log.info(f"  - {OUT_TIVI}")


# --------------------------------------------------
async def main() -> None:
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
        cached_urls = CACHE_FILE.load() or {}
        if cached_urls:
            build_playlists(cached_urls)
        raise


# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
