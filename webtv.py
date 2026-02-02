import asyncio
from functools import partial
from pathlib import Path
from urllib.parse import quote
import os
import re

from playwright.async_api import async_playwright, Browser
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

TAG = "WEBCAST"

BASE_URLS = {
    "NFL": os.environ.get("WEBTV_NFL_BASE_URL"),
}

if not BASE_URLS["NFL"]:
    raise RuntimeError("Missing WEBTV_NFL_BASE_URL secret")

REFERER = "https://nflwebcast.io/"
ORIGIN = "https://nflwebcast.io"

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

OUT_VLC = Path("webtv_vlc.m3u8")
OUT_TIVI = Path("webtv_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=10_800)
HTML_CACHE = Cache(f"{TAG}-html", exp=3_600)

urls: dict[str, dict] = {}

# --------------------------------------------------
def fix_event(s: str) -> str:
    return " vs ".join(map(str.strip, s.split("@")))

# --------------------------------------------------
def parse_event_time(date_text: str, time_text: str) -> float:
    """
    Safely parse event time.
    Falls back to 'now' if parsing fails.
    """
    clean = re.sub(r"(ET|CT|PT|LIVE)", "", time_text, flags=re.I).strip()
    try:
        return Time.from_str(
            f"{date_text} {clean}",
            timezone="EST"
        ).timestamp()
    except Exception:
        log.warning(f"Time parse failed: {date_text} {time_text}")
        return Time.now().timestamp()

# --------------------------------------------------
async def refresh_html_cache(url: str) -> dict[str, dict]:
    events = {}

    html = await network.request(
        url,
        headers=HEADERS,
        log=log,
    )
    if not html:
        return events

    now = Time.clean(Time.now())
    soup = HTMLParser(html.content)

    title = soup.css_first("title").text(strip=True)
    sport = "NFL" if "NFL" in title else "NHL"

    date_text = now.strftime("%B %d, %Y")
    if row := soup.css_first("tr.mdatetitle span.mtdate"):
        date_text = row.text(strip=True)

    rows = soup.css("tr.singele_match_date")
    log.info(f"Found {len(rows)} raw event row(s)")

    for row in rows:
        time_node = row.css_first("td.matchtime")
        vs_node = row.css_first("td.teamvs a")

        if not time_node or not vs_node:
            continue

        time_text = time_node.text(strip=True)
        raw_event = vs_node.text(strip=True)

        for span in vs_node.css("span"):
            raw_event = raw_event.replace(span.text(strip=True), "").strip()

        href = vs_node.attributes.get("href")
        if not href:
            continue

        event = fix_event(raw_event)
        event_ts = parse_event_time(date_text, time_text)

        key = f"[{sport}] {event} ({TAG})"

        events[key] = {
            "sport": sport,
            "event": event,
            "link": href,
            "event_ts": event_ts,
            "timestamp": now.timestamp(),
        }

    return events

# --------------------------------------------------
async def get_events(cached_keys: list[str]) -> list[dict]:
    events = HTML_CACHE.load()
    if not events:
        log.info("Refreshing HTML cache")
        results = await asyncio.gather(
            *(refresh_html_cache(url) for url in BASE_URLS.values())
        )
        events = {k: v for r in results for k, v in r.items()}
        HTML_CACHE.write(events)

    live = []
    for k, v in events.items():
        if k in cached_keys:
            continue
        live.append(v)

    return live

# --------------------------------------------------
async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load() or {}
    cached_count = len(cached_urls)

    log.info(f"Loaded {cached_count} cached event(s)")
    log.info(f'Scraping from "{", ".join(BASE_URLS.values())}"')

    events = await get_events(list(cached_urls.keys()))
    log.info(f"Processing {len(events)} new URL(s)")

    if not events:
        CACHE_FILE.write(cached_urls)
        return

    async with network.event_context(browser) as context:
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

                key = f"[{ev['sport']}] {ev['event']} ({TAG})"
                tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

                cached_urls[key] = {
                    "url": stream_url,
                    "logo": logo,
                    "base": BASE_URLS[ev["sport"]],
                    "timestamp": ev["event_ts"],
                    "id": tvg_id or "NFL.Dummy.us",
                    "link": ev["link"],
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
    log.info("🚀 Starting WEBTV scraper")
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
