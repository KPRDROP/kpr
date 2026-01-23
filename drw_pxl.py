import os
import json
from pathlib import Path
from urllib.parse import quote
from functools import partial

from playwright.async_api import async_playwright, BrowserContext

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "PIXEL"

# 🔐 API URL FROM SECRET
BASE_URL = os.getenv("PIXEL_API_URL")
if not BASE_URL:
    raise RuntimeError("PIXEL_API_URL secret is not set")

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=900)
OUTPUT_FILE = Path("drw_pxl_tivimate.m3u8")

REFERER = "https://pixelsport.tv/"
ORIGIN = "https://pixelsport.tv"

UA_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
)
UA_ENC = quote(UA_RAW)

urls: dict[str, dict] = {}


def build_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{ch}" '
            f'tvg-id="{e["id"]}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )
        lines.append(
            f'{e["url"]}'
            f'|referer={REFERER}'
            f'|origin={ORIGIN}'
            f'|user-agent={UA_ENC}'
        )
        ch += 1

    return "\n".join(lines) + "\n"


async def fetch_api(context: BrowserContext) -> dict:
    page = await context.new_page()

    await page.goto(
        "https://pixelsport.tv",
        wait_until="domcontentloaded",
        timeout=15_000,
    )

    try:
        data = await page.evaluate(
            """
            async (url) => {
                const r = await fetch(url, {
                    credentials: "include",
                    headers: { "accept": "application/json" }
                });
                if (!r.ok) throw new Error(r.status);
                return await r.json();
            }
            """,
            BASE_URL,
        )
        return data
    finally:
        await page.close()


async def get_events(context: BrowserContext) -> dict:
    now = Time.clean(Time.now())
    events = {}

    api_data = await fetch_api(context)
    api_events = api_data.get("events", [])

    for ev in api_events:
        try:
            event_dt = Time.from_str(ev["date"], timezone="UTC")
        except Exception:
            continue

        # Live + upcoming (±6h window)
        if abs((event_dt - now).total_seconds()) > 6 * 3600:
            continue

        event_name = ev.get("match_name")
        channel = ev.get("channel") or {}
        category = channel.get("TVCategory") or {}

        sport = category.get("name")
        if not event_name or not sport:
            continue

        for idx in (1, 2):
            stream = channel.get(f"server{idx}URL")
            if not stream or stream == "null":
                continue

            key = f"[{sport}] {event_name} {idx} ({TAG})"
            if key in events:
                continue

            tvg_id, logo = leagues.get_tvg_info(sport, event_name)

            events[key] = {
                "url": stream,
                "logo": logo,
                "base": ORIGIN,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
            }

    return events


async def scrape() -> None:
    cached = CACHE_FILE.load()
    urls.update(cached)

    log.info(f"Loaded {len(cached)} cached events")
    log.info(f'Scraping from "{BASE_URL}"')

    async with async_playwright() as p:
        browser, context = await network.browser(p, browser="chromium")

        # ✅ APPLY UA + HEADERS HERE (FIX)
        await context.set_extra_http_headers({
            "User-Agent": UA_RAW,
            "Referer": REFERER,
            "Origin": ORIGIN,
        })

        try:
            handler = partial(get_events, context=context)
            events = await network.safe_process(
                handler,
                url_num=1,
                semaphore=network.PW_S,
                log=log,
            )
        finally:
            await browser.close()

    added = 0
    for k, v in (events or {}).items():
        if k not in urls:
            urls[k] = v
            added += 1

    CACHE_FILE.write(urls)

    OUTPUT_FILE.write_text(
        build_playlist(urls),
        encoding="utf-8",
    )

    log.info(f"Wrote {added} new streams")
    log.info(f"Total entries: {len(urls)}")


if __name__ == "__main__":
    import asyncio

    log.info("🚀 Starting PixelSport scraper...")
    asyncio.run(scrape())
