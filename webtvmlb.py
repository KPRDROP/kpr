#!/usr/bin/env python3
import asyncio
from pathlib import Path
from urllib.parse import quote, urljoin
import os
import re
import random

from playwright.async_api import async_playwright
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
def clean_event_name(text: str) -> str:
    text = text.replace("@", "vs")
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# --------------------------------------------------
async def refresh_html_cache(browser):

    events = {}

    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    try:
        await page.goto(BASE_URL, timeout=30000)
        await page.wait_for_timeout(5000)
        html = await page.content()
    except Exception as e:
        log.error(f"Failed loading page: {e}")
        await context.close()
        return events

    await context.close()

    soup = HTMLParser(html)

    rows = soup.css("tr.singele_match_date")
    log.info(f"Found {len(rows)} raw event row(s)")

    for row in rows:

        link_node = row.css_first("td.teamvs a")
        if not link_node:
            continue

        href = link_node.attributes.get("href")
        if not href:
            continue

        href = urljoin(BASE_URL, href)

        # Get full anchor text INCLUDING date span
        full_text = link_node.text(separator=" ", strip=True)
        full_text = clean_event_name(full_text)

        key = f"[MLB] {full_text} ({TAG})"

        events[key] = {
            "sport": "MLB",
            "event": full_text,
            "link": href,
            "timestamp": Time.now().timestamp(),
        }

    return events

# --------------------------------------------------
async def scrape(browser):

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

    if not new_events:
        log.info("No new events found")
        return

    log.info(f"Processing {len(new_events)} new URL(s)")

    # --------------------------------------------------
    # CRITICAL FIX: NEW CONTEXT PER EVENT
    # --------------------------------------------------

    for i, ev in enumerate(new_events, start=1):

        await asyncio.sleep(random.uniform(1.5, 3.5))  # anti-bot delay

        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={
                "Referer": REFERER,
                "Origin": ORIGIN,
            }
        )

        page = await context.new_page()

        try:
            stream_url = await network.process_event(
                url=ev["link"],
                url_num=i,
                page=page,
                log=log,
            )

        except Exception as e:
            log.warning(f"URL {i}) Failed: {e}")
            await context.close()
            continue

        await context.close()

        if not stream_url:
            continue

        log.info(f"URL {i}) Captured M3U8")

        name = f"[MLB] {ev['event']} ({TAG})"
        tvg_id, logo = leagues.get_tvg_info("MLB", ev["event"])

        cached_urls[name] = {
            "url": stream_url,
            "logo": logo,
            "base": BASE_URL,
            "timestamp": ev["timestamp"],
            "id": tvg_id or "MLB.Baseball.Dummy.us",
            "link": ev["link"],
        }

    CACHE_FILE.write(cached_urls)
    build_playlists(cached_urls)

    log.info(f"Collected {len(cached_urls) - cached_count} new event(s)")

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
