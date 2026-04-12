#!/usr/bin/env python3

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import quote, urljoin
from functools import partial

from playwright.async_api import async_playwright, Browser
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "MLBCAST"
CACHE_FILE = Cache(TAG, exp=19_800)

# -------------------------------------------------
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

# -------------------------------------------------
def clean_event_name(text: str) -> str:
    text = text.replace("Live Stream", "")
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# -------------------------------------------------
async def get_events(browser: Browser, cached_keys: list[str]) -> list[dict]:
    events = []

    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    try:
        log.info(f"Loading page with Playwright: {BASE_URL}")
        await page.goto(BASE_URL, timeout=30000)

        await page.wait_for_timeout(4000)

        html = await page.content()
        soup = HTMLParser(html)

        anchors = soup.css("a[href*='-live']")

        seen = set()

        for a in anchors:
            href = a.attributes.get("href")
            if not href:
                continue

            url = urljoin(BASE_URL, href)

            if url in seen:
                continue
            seen.add(url)

            title = a.attributes.get("title") or a.text(strip=True)

            if not title:
                continue

            event = clean_event_name(title)

            key = f"[MLB] {event} ({TAG})"
            if key in cached_keys:
                continue

            events.append({
                "sport": "MLB",
                "event": event,
                "link": url,
            })

        log.info(f"Detected {len(events)} events")

    except Exception as e:
        log.error(f"Failed to load page: {e}")

    finally:
        await page.close()
        await context.close()

    return events

# -------------------------------------------------
async def scrape(browser: Browser):
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}
    urls.update(valid_urls)

    log.info(f"Loaded {len(valid_urls)} event(s) from cache")

    events = await get_events(browser, cached_urls.keys())

    if not events:
        log.info("No new events found")
        write_outputs()
        return

    log.info(f"Processing {len(events)} events")

    now = Time.clean(Time.now())

    async with network.event_context(browser) as context:
        for i, ev in enumerate(events, start=1):
            log.info(f"[{i}/{len(events)}] {ev['event']}")

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
                    log.info("No stream found")
                    continue

                log.info(f"STREAM FOUND: {stream_url}")

                tvg_id, logo = leagues.get_tvg_info("MLB", ev["event"])

                key = f"[MLB] {ev['event']} ({TAG})"

                entry = {
                    "url": stream_url,
                    "logo": logo,
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "MLB.Baseball.Dummy.us",
                }

                urls[key] = entry
                cached_urls[key] = entry

    CACHE_FILE.write(cached_urls)
    write_outputs()

# -------------------------------------------------
def write_outputs():
    if not urls:
        log.warning("No URLs to write")
        return

    log.info("Writing playlists...")

    with OUT_VLC.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for i, (name, data) in enumerate(urls.items(), 1):
            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{data["id"]}" '
                f'tvg-name="{name}" tvg-logo="{data["logo"]}" '
                f'group-title="Live Events",{name}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={REFERER}\n")
            f.write(f"#EXTVLCOPT:http-origin={ORIGIN}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{data['url']}\n\n")

    with OUT_TIVI.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for i, (name, data) in enumerate(urls.items(), 1):
            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{data["id"]}" '
                f'tvg-name="{name}" tvg-logo="{data["logo"]}" '
                f'group-title="Live Events",{name}\n'
            )
            f.write(
                f"{data['url']}|referer={REFERER}/|origin={ORIGIN}|user-agent={UA_ENC}\n"
            )

    log.info("Playlists generated")

# -------------------------------------------------
async def main():
    log.info("Starting MLB WebTV updater...")

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)

        try:
            await scrape(browser)
        finally:
            await browser.close()

# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
