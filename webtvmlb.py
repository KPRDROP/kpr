#!/usr/bin/env python3

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import quote
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

BASE_URLS = {"MLB": BASE_URL}

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
def fix_event(s: str) -> str:
    return " vs ".join(s.split("@"))

def clean_event_name(text: str) -> str:
    text = text.replace("@", "vs")
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# -------------------------------------------------
# ✅ FIXED: use Playwright instead of network.request
async def get_events(browser: Browser, cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    try:
        log.info(f"Loading page with Playwright: {BASE_URL}")
        await page.goto(BASE_URL, timeout=30000, wait_until="domcontentloaded")

        await page.wait_for_timeout(3000)

        html = await page.content()
        soup = HTMLParser(html)

        for row in soup.css("tr.singele_match_date"):
            if not (vs_node := row.css_first("td.teamvs a")):
                continue

            event_name = vs_node.text(strip=True)

            for span in vs_node.css("span.mtdate"):
                date = span.text(strip=True)
                event_name = event_name.replace(date, "").strip()

            href = vs_node.attributes.get("href")
            if not href:
                continue

            event = clean_event_name(fix_event(event_name))

            key = f"[MLB] {event} ({TAG})"
            if key in cached_keys:
                continue

            events.append(
                {
                    "sport": "MLB",
                    "event": event,
                    "link": href,
                }
            )

    except Exception as e:
        log.error(f"Failed to load page via Playwright: {e}")

    finally:
        await page.close()
        await context.close()

    return events

# -------------------------------------------------
async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}
    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f"Scraping from '{BASE_URL}'")

    # ✅ FIXED CALL
    events = await get_events(browser, cached_urls.keys())

    if events:
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        log=log,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    sport, event = ev["sport"], ev["event"]
                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": BASE_URL,
                        "timestamp": now.timestamp(),
                        "id": tvg_id or "MLB.Baseball.Dummy.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1
                        urls[key] = entry

        log.info(f"Collected {valid_count - cached_count} new events")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)

    write_outputs()

# -------------------------------------------------
def write_outputs():
    if not urls:
        log.warning("No URLs to write")
        return

    log.info("Writing M3U outputs...")

    with OUT_VLC.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for i, (name, data) in enumerate(urls.items(), start=1):
            if not data.get("url"):
                continue

            title = clean_event_name(name)

            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{data["id"]}" '
                f'tvg-name="{title}" tvg-logo="{data["logo"]}" '
                f'group-title="Live Events",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={REFERER}\n")
            f.write(f"#EXTVLCOPT:http-origin={ORIGIN}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{data['url']}\n\n")

    with OUT_TIVI.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for i, (name, data) in enumerate(urls.items(), start=1):
            if not data.get("url"):
                continue

            title = clean_event_name(name)

            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{data["id"]}" '
                f'tvg-name="{title}" tvg-logo="{data["logo"]}" '
                f'group-title="Live Events",{title}\n'
            )
            f.write(
                f"{data['url']}|referer={REFERER}/|origin={ORIGIN}|user-agent={UA_ENC}\n"
            )

    log.info("M3U files generated successfully")

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
