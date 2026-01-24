import os
import json
from pathlib import Path
from urllib.parse import quote
from functools import partial

from playwright.async_api import async_playwright, BrowserContext

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "PIXEL"

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


async def get_api_data(context: BrowserContext) -> dict:
    try:
        response = await context.request.get(
            BASE_URL,
            headers={
                "User-Agent": UA_RAW,
                "Referer": REFERER,
                "Origin": ORIGIN,
                "Accept": "application/json",
            },
            timeout=15_000,
        )

        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")

        text = await response.text()

        if not text or not text.lstrip().startswith("{"):
            raise ValueError("Non-JSON API response")

        return json.loads(text)

    except Exception as e:
        log.error(f'Failed to fetch "{BASE_URL}": {e}')
        return {}


async def get_events(context: BrowserContext) -> dict:
    now = Time.clean(Time.now())
    events = {}

    api_data = await get_api_data(context)
    raw_events = api_data.get("events", [])

    for event in raw_events:
        try:
            event_dt = Time.from_str(event["date"], timezone="UTC")
        except Exception:
            continue

        if event_dt.date() != now.date():
            continue

        event_name = event.get("match_name")
        channel = event.get("channel") or {}
        category = channel.get("TVCategory") or {}

        sport = category.get("name")
        if not event_name or not sport:
            continue

        for i in (1, 2):
            stream = channel.get(f"server{i}URL")
            if not stream or stream == "null":
                continue

            key = f"[{sport}] {event_name} {i} ({TAG})"
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

        try:
            handler = partial(get_events, context=context)
            fresh = await network.safe_process(
                handler,
                url_num=1,
                semaphore=network.PW_S,
                log=log,
            )
        finally:
            await browser.close()

    if fresh:
        urls.update(fresh)
        CACHE_FILE.write(urls)
        log.info(f"Fetched {len(fresh)} live events")
    else:
        log.warning("Using cached events (API blocked or empty)")

    if not urls:
        log.warning("No events available — playlist not updated")
        return

    OUTPUT_FILE.write_text(
        build_playlist(urls),
        encoding="utf-8",
    )

    log.info(f"Wrote playlist with {len(urls)} entries")


if __name__ == "__main__":
    import asyncio

    log.info("🚀 Starting PixelSport scraper...")
    asyncio.run(scrape())
