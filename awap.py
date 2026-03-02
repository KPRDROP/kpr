#!/usr/bin/env python3
import asyncio
import os
from urllib.parse import urljoin, quote

from playwright.async_api import async_playwright
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# ---------------- CONFIG ----------------
TAG = "PAWA"

BASE_URL = os.environ.get("PAWA_FEED_URL")
if not BASE_URL:
    raise RuntimeError("Missing PAWA_FEED_URL secret")

OUTPUT_VLC = "awap_vlc.m3u8"
OUTPUT_TIVI = "awap_tivimate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)

ENCODED_UA = quote(USER_AGENT, safe="")

CACHE_FILE = Cache(TAG, exp=10_800)

urls: dict[str, dict] = {}

# --------------------------------------------------
# PLAYWRIGHT CAPTURE
# --------------------------------------------------
async def capture_stream(event_url: str, url_num: int):

    if not event_url.startswith("http"):
        event_url = urljoin(BASE_URL, event_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        stream_url = None

        def handle_response(response):
            nonlocal stream_url
            if ".m3u8" in response.url and response.url.startswith("http"):
                if not stream_url:
                    stream_url = response.url

        page.on("response", handle_response)

        try:
            await page.goto(event_url, timeout=45000)
            await page.wait_for_timeout(5000)

            # some feeds need interaction
            await page.mouse.click(600, 400)
            await page.wait_for_timeout(3000)

            await page.wait_for_timeout(8000)

        except Exception as e:
            log.warning(f"URL {url_num}) Interaction failed: {e}")

        await browser.close()

    if stream_url:
        log.info(f"URL {url_num}) Captured M3U8")
        return stream_url, event_url

    log.warning(f"URL {url_num}) No M3U8 captured")
    return None, None


# --------------------------------------------------
# EVENTS (feed parsing)
# --------------------------------------------------
async def get_events(cached_keys):

    if not (page := await network.request(BASE_URL, log=log)):
        return []

    soup = HTMLParser(page.content)
    events = []

    for a in soup.css("a"):
        href = a.attributes.get("href")
        if not href:
            continue

        if "stream" not in href.lower():
            continue

        full_url = urljoin(BASE_URL, href)
        name = a.text(strip=True) or "Live Event"

        key = f"{name} ({TAG})"
        if key in cached_keys:
            continue

        events.append({
            "event": name,
            "sport": "Live",
            "link": full_url,
        })

    return events


# --------------------------------------------------
# MAIN
# --------------------------------------------------
async def scrape():

    cached = CACHE_FILE.load() or {}

    valid = {k: v for k, v in cached.items() if v.get("url")}
    urls.update(valid)

    cached_count = len(valid)

    log.info(f"Loaded {cached_count} cached event(s)")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached.keys())

    if not events:
        CACHE_FILE.write(cached)
        write_playlists()
        return

    log.info(f"Processing {len(events)} new stream URL(s)")

    now = Time.clean(Time.now()).timestamp()

    for i, ev in enumerate(events, 1):

        stream, referer = await capture_stream(ev["link"], i)

        if not stream:
            continue

        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        key = f"{ev['event']} ({TAG})"

        entry = {
            "url": stream,
            "base": referer,
            "logo": logo,
            "id": tvg_id or "Live.Event.us",
            "sport": ev["sport"],
            "event": ev["event"],
            "timestamp": now,
        }

        cached[key] = entry
        urls[key] = entry   # ✅ FIXED indentation

    CACHE_FILE.write(cached)
    write_playlists()

    log.info(f"Collected and cached {len(cached) - cached_count} new event(s)")


# --------------------------------------------------
# PLAYLISTS
# --------------------------------------------------
def write_playlists():

    vlc = ["#EXTM3U"]
    tivi = ["#EXTM3U"]

    for key, e in urls.items():

        title = f"{e['event']} ({TAG})"
        referer = e["base"]

        extinf = (
            f'#EXTINF:-1 tvg-id="{e["id"]}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{title}'
        )

        vlc.extend([
            extinf,
            f"#EXTVLCOPT:http-referrer={referer}",
            f"#EXTVLCOPT:http-origin={referer}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        tivi.extend([
            extinf,
            f'{e["url"]}|referer={referer}|origin={referer}|user-agent={ENCODED_UA}',
        ])

    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("\n".join(vlc) + "\n")

    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("\n".join(tivi) + "\n")

    log.info("Playlists written successfully")


# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
