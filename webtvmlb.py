#!/usr/bin/env python3
import asyncio
from pathlib import Path
from urllib.parse import quote, urljoin
import os
import re

from playwright.async_api import async_playwright, Browser
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues

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

OUT_VLC = Path("webtvmlb_vlc.m3u8")
OUT_TIVI = Path("webtvmlb_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=10_800)
HTML_CACHE = Cache(f"{TAG}-html", exp=3_600)

# --------------------------------------------------
def fix_event(s: str) -> str:
    return " vs ".join(map(str.strip, s.split("@")))

# --------------------------------------------------
def parse_event_time(date_text: str, time_text: str) -> float:
    clean = re.sub(r"(ET|CT|PT|LIVE)", "", time_text, flags=re.I).strip()
    try:
        return Time.from_str(
            f"{date_text} {clean}",
            timezone="EST"
        ).timestamp()
    except Exception:
        return Time.now().timestamp()

# --------------------------------------------------
async def refresh_html_cache(browser: Browser) -> dict[str, dict]:

    events = {}

    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    try:
        await page.goto(BASE_URL, timeout=30000)
        await page.wait_for_timeout(4000)
        html = await page.content()
    except Exception as e:
        log.error(f"Failed loading page: {e}")
        await context.close()
        return events

    await context.close()

    now = Time.clean(Time.now())
    soup = HTMLParser(html)

    sport = "MLB"
    date_text = now.strftime("%B %d, %Y")

    rows = soup.css("tr.singele_match_date")
    log.info(f"Found {len(rows)} raw event row(s)")

    for row in rows:
        time_node = row.css_first("td.matchtime")
        vs_node = row.css_first("td.teamvs a")

        if not time_node or not vs_node:
            continue

        time_text = time_node.text(strip=True)
        raw_event = vs_node.text(strip=True)
        href = vs_node.attributes.get("href")

        if not href:
            continue

        href = urljoin(BASE_URL, href)

        event = fix_event(raw_event)
        event_ts = parse_event_time(date_text, time_text)

        key = f"[{sport}] {event} ({TAG})"

        events[key] = {
            "sport": sport,
            "event": event,
            "link": href,
            "event_ts": event_ts,
        }

    return events

# --------------------------------------------------
async def capture_m3u8(browser: Browser, url: str, url_num: int):

    context = await browser.new_context(
        user_agent=USER_AGENT,
        extra_http_headers={
            "Referer": REFERER,
            "Origin": ORIGIN,
        },
    )

    page = await context.new_page()

    m3u8_url = None
    done = asyncio.Event()

    async def handle_response(response):
        nonlocal m3u8_url
        if ".m3u8" in response.url and response.status == 200:
            if not m3u8_url:
                m3u8_url = response.url
                log.info(f"URL {url_num}) Captured M3U8")
                done.set()

    page.on("response", handle_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.wait_for(done.wait(), timeout=30)
    except asyncio.TimeoutError:
        log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
    except Exception as e:
        log.warning(f"URL {url_num}) Error: {e}")

    await context.close()
    return m3u8_url

# --------------------------------------------------
async def scrape(browser: Browser) -> None:

    cached_urls = CACHE_FILE.load() or {}
    cached_count = len(cached_urls)

    log.info(f"Loaded {cached_count} cached event(s)")
    log.info(f'Scraping from "{BASE_URL}"')

    events = HTML_CACHE.load()

    if not events:
        log.info("Refreshing HTML cache")
        events = await refresh_html_cache(browser)
        HTML_CACHE.write(events)

    new_events = [
        v for k, v in events.items()
        if k not in cached_urls
    ]

    log.info(f"Processing {len(new_events)} new URL(s)")

    for i, ev in enumerate(new_events, start=1):

        stream_url = await capture_m3u8(browser, ev["link"], i)

        if not stream_url:
            continue

        key = f"[{ev['sport']}] {ev['event']} ({TAG})"
        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        cached_urls[key] = {
            "url": stream_url,
            "logo": logo,
            "timestamp": ev["event_ts"],
            "id": tvg_id or "MLB.Baseball.Dummy.us",
        }

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
async def main():

    log.info("Starting WEBTV MLB updater")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        await scrape(browser)
        await browser.close()

# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
