import json
from functools import partial
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import Browser, Page

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "PIXEL"

BASE_URL = "https://pixelsport.tv/backend/livetv/events"

REFERER = "https://pixelsport.tv/"
ORIGIN = "https://pixelsport.tv"

UA_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
UA_ENC = quote(UA_RAW)

CACHE_FILE = Cache("pixel.json", exp=19_800)
OUTPUT_FILE = Path("drw_pxl_tivimate.m3u8")

urls: dict[str, dict[str, str | float]] = {}


# =========================
# TIVIMATE PLAYLIST BUILDER
# =========================
def build_tivimate_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        lines.append(
            f'#EXTINF:-1 '
            f'tvg-chno="{ch}" '
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


# =========================
# API FETCH
# =========================
async def get_api_data(page: Page) -> dict:
    try:
        await page.goto(
            BASE_URL,
            wait_until="domcontentloaded",
            timeout=10_000,
        )

        content = await page.content()

        # PixelSport sometimes returns raw JSON without <pre>
        if content.strip().startswith("{"):
            return json.loads(content)

        pre = page.locator("pre")
        if await pre.count():
            return json.loads(await pre.inner_text())

        raise RuntimeError("Empty or non-JSON response")

    except Exception as e:
        log.error(f'Failed to fetch "{BASE_URL}": {e}')
        return {}


# =========================
# EVENT PARSER
# =========================
async def get_events(page: Page) -> dict:
    now = Time.clean(Time.now())
    api_data = await get_api_data(page)

    events = {}

    for event in api_data.get("events", []):
        event_dt = Time.from_str(event["date"], timezone="UTC")
        if event_dt.date() != now.date():
            continue

        event_name = event["match_name"]
        channel = event["channel"]
        sport = channel["TVCategory"]["name"]

        for i in range(1, 4):
            link = channel.get(f"server{i}URL")
            if not link or link == "null":
                continue

            key = f"[{sport}] {event_name} {i} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info(sport, event_name)

            events[key] = {
                "url": link,
                "logo": logo,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
            }

    return events


# =========================
# SCRAPER ENTRY
# =========================
async def scrape(browser: Browser) -> None:
    cached = CACHE_FILE.load()
    if cached:
        urls.update(cached)
        log.info(f"Loaded {len(urls)} cached event(s)")
        return

    log.info(f'Scraping from "{BASE_URL}"')

    async with network.event_context(browser) as context:
        async with network.event_page(context) as page:
            handler = partial(get_events, page=page)
            events = await network.safe_process(
                handler,
                url_num=1,
                semaphore=network.PW_S,
                log=log,
            )

    if not events:
        log.warning("No events available — playlist not written")
        return

    urls.update(events)
    CACHE_FILE.write(urls)

    OUTPUT_FILE.write_text(
        build_tivimate_playlist(urls),
        encoding="utf-8",
    )

    log.info(f"Wrote {len(events)} event(s) to {OUTPUT_FILE.name}")
