#!/usr/bin/env python3
import asyncio
from functools import partial
from pathlib import Path
from urllib.parse import quote, urljoin
import os
import re

from playwright.async_api import async_playwright, Browser
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

OUT_VLC = Path("webtvmlb_vlc.m3u8")
OUT_TIVI = Path("webtvmlb_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=10_800)
HTML_CACHE = Cache(f"{TAG}-html", exp=3_600)

# --------------------------------------------------
def fix_event(s: str) -> str:
    return " vs ".join(map(str.strip, s.split("@")))

# --------------------------------------------------
async def extract_stream(page, url: str, url_num: int) -> str | None:

    try:
        response = await page.goto(url, timeout=30000)
        if not response or response.status != 200:
            log.warning(f"URL {url_num}) Status Code: {response.status if response else 'None'}")
            return None

        html = await page.content()
        soup = HTMLParser(html)

        iframe = soup.css_first('iframe[name="srcFrame"]')
        if not iframe:
            log.warning(f"URL {url_num}) No iframe element found.")
            return None

        iframe_src = iframe.attributes.get("src")
        if not iframe_src:
            log.warning(f"URL {url_num}) No iframe source found.")
            return None

        iframe_src = urljoin(url, iframe_src)

        # Load iframe inside same browser (avoids 403)
        iframe_response = await page.goto(
            iframe_src,
            timeout=30000,
            referer=url
        )

        if not iframe_response or iframe_response.status != 200:
            log.warning(f"URL {url_num}) Iframe Status: {iframe_response.status if iframe_response else 'None'}")
            return None

        iframe_html = await page.content()

        match = re.search(r"source:\s*['\"](.*?)['\"]", iframe_html, re.I)
        if not match:
            log.warning(f"URL {url_num}) No Clappr source found.")
            return None

        log.info(f"URL {url_num}) Captured M3U8")
        return match.group(1)

    except Exception as e:
        log.warning(f"URL {url_num}) Error: {e}")
        return None

# --------------------------------------------------
async def refresh_html_cache(browser: Browser):

    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    await page.goto(BASE_URL, timeout=30000)
    await page.wait_for_timeout(4000)

    html = await page.content()
    await context.close()

    soup = HTMLParser(html)
    events = {}

    for row in soup.css("tr.singele_match_date"):
        vs_node = row.css_first("td.teamvs a")
        if not vs_node:
            continue

        event_name = fix_event(vs_node.text(strip=True))
        href = vs_node.attributes.get("href")
        if not href:
            continue

        href = urljoin(BASE_URL, href)

        key = f"[MLB] {event_name} ({TAG})"

        events[key] = {
            "sport": "MLB",
            "event": event_name,
            "link": href,
        }

    return events

# --------------------------------------------------
async def scrape(browser: Browser):

    cached_urls = CACHE_FILE.load() or {}
    cached_keys = list(cached_urls.keys())

    events = HTML_CACHE.load()
    if not events:
        log.info("Refreshing HTML cache")
        events = await refresh_html_cache(browser)
        HTML_CACHE.write(events)

    new_events = [
        v for k, v in events.items()
        if k not in cached_keys
    ]

    log.info(f"Processing {len(new_events)} new URL(s)")

    async with network.event_context(browser, stealth=False) as context:

        for i, ev in enumerate(new_events, start=1):

            async with network.event_page(context) as page:

                stream_url = await extract_stream(page, ev["link"], i)
                if not stream_url:
                    continue

                key = f"[{ev['sport']}] {ev['event']} ({TAG})"
                tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

                cached_urls[key] = {
                    "url": stream_url,
                    "logo": logo,
                    "base": BASE_URL,
                    "id": tvg_id or "MLB.Baseball.Dummy.us",
                    "link": ev["link"],
                }

    CACHE_FILE.write(cached_urls)
    build_playlists(cached_urls)

# --------------------------------------------------
def build_playlists(data):

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
