#!/usr/bin/env python3
import asyncio
import os
from functools import partial
from urllib.parse import urljoin, quote

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues

log = get_logger(__name__)

# ---------------- CONFIG ----------------
TAG = "XEAST"

BASE_URL = os.environ.get("XEAST_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing XEAST_BASE_URL secret")

OUTPUT_VLC = "east_vlc.m3u8"
OUTPUT_TIVI = "east_tivimate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
ENCODED_UA = quote(USER_AGENT, safe="")

CACHE_FILE = Cache(TAG, exp=10_800)

SPORT_ENDPOINTS = ["mma", "nba", "nhl", "soccer", "wwe"]

urls: dict[str, dict] = {}


# ---------------- PLAYWRIGHT STREAM CAPTURE ----------------
async def capture_stream(event_url: str, url_num: int):

    if not event_url.startswith("http"):
        event_url = urljoin(BASE_URL, event_url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        stream_url = None

        def handle_response(response):
            nonlocal stream_url
            if ".m3u8" in response.url and not stream_url:
                stream_url = response.url

        page.on("response", handle_response)

        try:
            await page.goto(event_url, timeout=30000)
            await page.wait_for_timeout(8000)
        except Exception as e:
            log.warning(f"URL {url_num}) Page load failed: {e}")

        await browser.close()

    if stream_url:
        log.info(f"URL {url_num}) Captured M3U8 via network")
        return stream_url, event_url

    log.warning(f"URL {url_num}) No M3U8 captured from network")
    return None, None


# ---------------- EVENTS ----------------
async def get_events():
    from selectolax.parser import HTMLParser
    from utils import network

    tasks = [
        network.request(urljoin(BASE_URL, f"categories/{sport}/"), log=log)
        for sport in SPORT_ENDPOINTS
    ]

    pages = await asyncio.gather(*tasks)
    events = []

    for page in pages:
        if not page:
            continue

        soup = HTMLParser(page.content)
        sport = "Live Event"

        if h := soup.css_first("h1.text-3xl"):
            sport = h.text(strip=True).split("Streams")[0].strip()

        for card in soup.css("article.game-card"):
            team = card.css_first("h2.text-xl.font-semibold")
            link = card.css_first("a.stream-button")
            live = card.css_first("span.bg-green-600")

            if not (team and link and live and live.text(strip=True) == "LIVE"):
                continue

            name = team.text(strip=True)
            href = link.attributes.get("href")
            if not href:
                continue

            key = f"[{sport}] {name} ({TAG})"

            events.append({
                "sport": sport,
                "event": name,
                "link": href,
                "key": key,
            })

    return events


# ---------------- MAIN ----------------
async def scrape():
    cached = CACHE_FILE.load()
    urls.update({k: v for k, v in cached.items() if v.get("url")})

    log.info(f"Loaded {len(urls)} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events()
    log.info(f"Processing {len(events)} new URL(s)")

    now = Time.clean(Time.now()).timestamp()

    for i, ev in enumerate(events, 1):

        if ev["key"] in cached:
            continue

        stream, referer = await capture_stream(ev["link"], i)

        if not stream:
            continue

        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        urls[ev["key"]] = cached[ev["key"]] = {
            "url": stream,
            "base": referer,
            "logo": logo,
            "id": tvg_id or "Live.Event.us",
            "sport": ev["sport"],
            "event": ev["event"],
            "timestamp": now,
        }

    CACHE_FILE.write(cached)
    write_playlists()


# ---------------- PLAYLISTS ----------------
def write_playlists():
    vlc, tivi = ["#EXTM3U"], ["#EXTM3U"]

    for key, e in urls.items():
        title = f"[{e['sport']}] {e['event']} ({TAG})"
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

    log.info(f"Generated {OUTPUT_VLC} and {OUTPUT_TIVI}")


if __name__ == "__main__":
    asyncio.run(scrape())
