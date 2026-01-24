import json
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright, BrowserContext

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "PIXEL"

FRONTEND_URL = "https://pixelsport.tv/"
API_URL = "https://pixelsport.tv/backend/liveTV/events"

CACHE_FILE = Cache("pixel.json", exp=900)
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


async def bootstrap_session(context: BrowserContext) -> None:
    """Loads frontend to establish cookies + session"""
    page = await context.new_page()
    await page.goto(FRONTEND_URL, wait_until="networkidle", timeout=30_000)
    await page.close()
    log.info("PixelSport session initialized")


async def fetch_api(context: BrowserContext) -> dict:
    try:
        r = await context.request.get(
            f"{API_URL}?ts={int(Time.now().timestamp() * 1000)}",
            headers={
                "User-Agent": UA_RAW,
                "Referer": REFERER,
                "Origin": ORIGIN,
                "Accept": "application/json",
            },
            timeout=20_000,
        )

        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")

        return await r.json()

    except Exception as e:
        log.error(f"PixelSport API blocked: {e}")
        return {}


async def get_events(context: BrowserContext) -> dict:
    now = Time.clean(Time.now())
    events = {}

    await bootstrap_session(context)
    api_data = await fetch_api(context)

    for event in api_data.get("events", []):
        try:
            event_dt = Time.from_str(event["date"], timezone="UTC")
        except Exception:
            continue

        if event_dt.date() != now.date():
            continue

        name = event.get("match_name")
        channel = event.get("channel") or {}
        category = channel.get("TVCategory") or {}

        sport = category.get("name")
        if not name or not sport:
            continue

        for i in (1, 2):
            stream = channel.get(f"server{i}URL")
            if not stream or stream == "null":
                continue

            key = f"[{sport}] {name} {i} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info(sport, name)

            events[key] = {
                "url": stream,
                "logo": logo,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
            }

    return events


async def scrape():
    cached = CACHE_FILE.load()
    urls.update(cached)

    log.info(f"Loaded {len(cached)} cached events")

    async with async_playwright() as p:
        browser, context = await network.browser(p, browser="chromium")

        try:
            fresh = await get_events(context)
        finally:
            await browser.close()

    if fresh:
        urls.update(fresh)
        CACHE_FILE.write(urls)
        log.info(f"Fetched {len(fresh)} live events")
    else:
        log.warning("Using cached events")

    if not urls:
        log.warning("No events available — playlist not written")
        return

    OUTPUT_FILE.write_text(build_playlist(urls), encoding="utf-8")
    log.info(f"Wrote playlist with {len(urls)} entries")


if __name__ == "__main__":
    import asyncio

    log.info("🚀 Starting PixelSport scraper...")
    asyncio.run(scrape())
