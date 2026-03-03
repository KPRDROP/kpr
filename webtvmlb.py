#!/usr/bin/env python3
import asyncio
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote, urljoin
import os

from selectolax.parser import HTMLParser
from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

TAG = "MLBCAST"

BASE_URL = os.environ.get("WEBTV_MLB_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing WEBTV_MLB_BASE_URL secret")

REFERER = BASE_URL
ORIGIN = BASE_URL.rstrip("/")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)
UA_ENC = quote(USER_AGENT)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Origin": ORIGIN,
}

OUT_VLC = Path("webtvmlb_vlc.m3u8")
OUT_TIVI = Path("webtvmlb_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=10_800)

# --------------------------------------------------
def fix_event(s: str) -> str:
    return " vs ".join(s.split("@"))

# --------------------------------------------------
async def process_event(url: str, url_num: int) -> str | None:

    event_data = await network.request(url, headers=HEADERS, log=log)
    if not event_data:
        log.warning(f"URL {url_num}) Failed to load event page.")
        return

    soup = HTMLParser(event_data.content)

    iframe = soup.css_first('iframe[name="srcFrame"]')
    if not iframe:
        log.warning(f"URL {url_num}) No iframe found.")
        return

    iframe_src = iframe.attributes.get("src")
    if not iframe_src:
        log.warning(f"URL {url_num}) No iframe src found.")
        return

    iframe_src = urljoin(BASE_URL, iframe_src)

    iframe_data = await network.request(
        iframe_src,
        headers=HEADERS,
        log=log,
    )

    if not iframe_data:
        log.warning(f"URL {url_num}) Failed loading iframe source.")
        return

    pattern = re.compile(r"source:\s+['\"](.*?)['\"]", re.I)

    match = pattern.search(iframe_data.text)
    if not match:
        log.warning(f"URL {url_num}) No stream source found.")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return match.group(1)

# --------------------------------------------------
async def get_events(cached_keys: list[str]) -> list[dict]:

    resp = await network.request(BASE_URL, headers=HEADERS, log=log)
    if not resp:
        return []

    soup = HTMLParser(resp.content)

    events = []

    rows = soup.css("tr.singele_match_date")
    log.info(f"Found {len(rows)} raw event row(s)")

    for row in rows:

        vs_node = row.css_first("td.teamvs a")
        if not vs_node:
            continue

        event_name = vs_node.text(strip=True)

        for span in vs_node.css("span.mtdate"):
            event_name = event_name.replace(span.text(strip=True), "").strip()

        href = vs_node.attributes.get("href")
        if not href:
            continue

        event = fix_event(event_name)

        key = f"[MLB] {event} ({TAG})"
        if key in cached_keys:
            continue

        events.append({
            "sport": "MLB",
            "event": event,
            "link": urljoin(BASE_URL, href),
        })

    return events

# --------------------------------------------------
async def scrape() -> None:

    cached_urls = CACHE_FILE.load() or {}
    cached_count = len(cached_urls)

    log.info(f"Loaded {cached_count} cached event(s)")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    if not events:
        log.info("No new events found")
        CACHE_FILE.write(cached_urls)
        return

    log.info(f"Processing {len(events)} new URL(s)")

    now = Time.clean(Time.now())

    for i, ev in enumerate(events, start=1):

        handler = partial(
            process_event,
            url=ev["link"],
            url_num=i,
        )

        stream_url = await network.safe_process(
            handler,
            url_num=i,
            semaphore=network.PW_S,
            log=log,
        )

        if not stream_url:
            continue

        sport, event = ev["sport"], ev["event"]
        key = f"[{sport}] {event} ({TAG})"

        tvg_id, logo = leagues.get_tvg_info(sport, event)

        entry = {
            "url": stream_url,
            "logo": logo,
            "base": BASE_URL,
            "timestamp": now.timestamp(),
            "id": tvg_id or "MLB.Baseball.Dummy.us",
            "link": ev["link"],
        }

        cached_urls[key] = entry

    CACHE_FILE.write(cached_urls)
    build_playlists(cached_urls)

    log.info(f"Collected {len(cached_urls) - cached_count} new event(s)")

# --------------------------------------------------
def build_playlists(data: dict[str, dict]):

    vlc = ["#EXTM3U"]
    tm = ["#EXTM3U"]

    for name, e in data.items():

        vlc.extend([
            f'#EXTINF:-1 tvg-id="{e["id"]}" tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={REFERER}",
            f"#EXTVLCOPT:http-origin={ORIGIN}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        tm.extend([
            f'#EXTINF:-1 tvg-id="{e["id"]}" tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" group-title="Live Events",{name}',
            f'{e["url"]}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}',
        ])

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tm), encoding="utf-8")

    log.info("Playlists written successfully")

# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
