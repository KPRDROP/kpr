#!/usr/bin/env python3
import os
import asyncio
from urllib.parse import quote
from functools import partial

import feedparser
from playwright.async_api import async_playwright, Page, Browser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "LIVETVSX"

BASE_URL = os.getenv("SXLIVE_BASE_URL")
BASE_REF = os.getenv("SXLIVE_BASE_REF")

if not BASE_URL or not BASE_REF:
    raise RuntimeError("Missing SXLIVE_BASE_URL or SXLIVE_BASE_REF")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)
ENCODED_UA = quote(USER_AGENT)

CACHE_FILE = Cache(TAG, exp=10_800)
XML_CACHE = Cache(f"{TAG}-xml", exp=28_000)

OUTPUT_VLC = "sxlive_vlc.m3u8"
OUTPUT_TIVI = "sxlive_tivimate.m3u8"

VALID_SPORTS = {
    "Football", "Basketball", "Ice Hockey", "Volleyball",
    "Table Tennis", "Handball", "Water Polo",
    "Tennis", "Futsal", "Floorball"
}


# --------------------------------------------------
async def refresh_xml_cache(now_ts: float) -> dict:
    log.info("Refreshing XML cache")

    events = {}
    xml = await network.request(BASE_URL, log=log)
    if not xml:
        return events

    feed = feedparser.parse(xml.content)

    for e in feed.entries:
        title = e.get("title")
        link = e.get("link")
        summary = e.get("summary")
        date = e.get("published")

        if not all([title, link, summary, date]):
            continue

        sport, *league = summary.split(".", 1)
        if sport not in VALID_SPORTS:
            continue

        league = league[0].strip() if league else ""
        event_dt = Time.from_str(date)

        key = f"[{sport} - {league}] {title} ({TAG})"

        events[key] = {
            "sport": sport,
            "league": league,
            "event": title,
            "link": link,
            "event_ts": event_dt.timestamp(),
            "timestamp": now_ts,
        }

    return events


# --------------------------------------------------
async def get_events(cached_keys):
    now = Time.clean(Time.now())

    events = XML_CACHE.load()
    if not events:
        events = await refresh_xml_cache(now.timestamp())
        XML_CACHE.write(events)

    start = now.delta(hours=-1).timestamp()
    end = now.delta(minutes=10).timestamp()

    return [
        v for k, v in events.items()
        if k not in cached_keys and start <= v["event_ts"] <= end
    ]


# --------------------------------------------------
async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> str | None:

    captured: list[str] = []
    got_one = asyncio.Event()

    handler = partial(
        network.capture_req,
        captured=captured,
        got_one=got_one,
    )

    page.on("request", handler)

    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=20_000,
        )

        await page.wait_for_timeout(2_000)

        # 🔧 FIX 1: scan ALL links (livetv DOM changed)
        links = await page.query_selector_all("a[href]")

        player_url = None

        for a in links:
            href = await a.get_attribute("href")
            if not href:
                continue

            href_l = href.lower()
            if any(k in href_l for k in ("player", "embed", "stream", "iframe")):
                player_url = href
                break

        # 🔧 FIX 2: iframe fallback
        if not player_url:
            iframe = await page.query_selector("iframe[src]")
            if iframe:
                player_url = await iframe.get_attribute("src")

        if not player_url:
            log.warning(f"URL {url_num}) No valid source")
            return None

        if not player_url.startswith("http"):
            player_url = f"https:{player_url}"

        await page.goto(
            player_url,
            wait_until="domcontentloaded",
            timeout=15_000,
        )

        # 🔧 FIX 3: trigger playback (required now)
        try:
            await page.mouse.click(300, 300)
            await asyncio.sleep(1)
            await page.mouse.click(300, 300)
        except Exception:
            pass

        # 🔧 FIX 4: wait longer for HLS
        try:
            await asyncio.wait_for(got_one.wait(), timeout=12)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return None

        if captured:
            log.info(f"URL {url_num}) Captured M3U8")
            return captured[0]

        log.warning(f"URL {url_num}) No M3U8 captured after waiting.")
        return None

    except Exception as e:
        log.warning(f"URL {url_num}) Exception while processing: {e}")
        return None

    finally:
        page.remove_listener("request", handler)


# --------------------------------------------------
async def scrape(browser):
    cached = CACHE_FILE.load() or {}
    urls = {k: v for k, v in cached.items() if v.get("url")}

    events = await get_events(cached.keys())
    log.info(f"Processing {len(events)} event(s)")

    if events:
        async with network.event_context(browser, ignore_https=True) as ctx:
            for i, ev in enumerate(events, 1):
                async with network.event_page(ctx) as page:
                    url = await process_event(ev["link"], i, page)

                    key = f"[{ev['sport']} - {ev['league']}] {ev['event']} ({TAG})"
                    tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

                    cached[key] = {
                        "url": url,
                        "logo": logo,
                        "base": BASE_REF,
                        "timestamp": ev["event_ts"],
                        "id": tvg_id or "Live.Event.us",
                        "link": ev["link"],
                    }

                    if url:
                        urls[key] = cached[key]

    CACHE_FILE.write(cached)
    write_playlists(urls)


# --------------------------------------------------
def write_playlists(entries: dict):
    vlc = ["#EXTM3U"]
    tivi = ["#EXTM3U"]

    ch = 1
    for name, e in entries.items():
        if not e["url"]:
            continue

        info = (
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )

        vlc.extend([
            info,
            f"#EXTVLCOPT:http-referrer={BASE_REF}",
            f"#EXTVLCOPT:http-origin={BASE_REF}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        tivi.extend([
            info,
            f'{e["url"]}|referer={BASE_REF}|origin={BASE_REF}|user-agent={ENCODED_UA}',
        ])

        ch += 1

    open(OUTPUT_VLC, "w", encoding="utf-8").write("\n".join(vlc))
    open(OUTPUT_TIVI, "w", encoding="utf-8").write("\n".join(tivi))

    log.info(f"Wrote {ch - 1} entries")


# --------------------------------------------------
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        await scrape(browser)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
