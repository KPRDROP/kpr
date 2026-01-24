import json
from functools import partial
from typing import Dict

from playwright.async_api import Browser, BrowserContext, Page

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "PIXEL"
CACHE_FILE = Cache(TAG, exp=19_800)

FRONT_URL = "https://pixelsport.tv"
API_URL = "https://pixelsport.tv/backend/liveTV/events"

urls: Dict[str, dict] = {}


# -------------------------
# TOKEN HANDLING
# -------------------------
async def get_token(page: Page) -> str | None:
    try:
        token = await page.evaluate("() => localStorage.getItem('token')")
        if not token:
            log.error("PixelSport token not found")
            return None
        return token
    except Exception as e:
        log.error(f"Token extraction failed: {e}")
        return None


# -------------------------
# API FETCH
# -------------------------
async def get_api_data(page: Page) -> dict:
    token = await get_token(page)
    if not token:
        return {}

    try:
        response = await page.request.get(
            API_URL,
            headers={"token": token},
            timeout=10_000,
        )

        if response.status != 200:
            log.error(f"Backend returned HTTP {response.status}")
            return {}

        return await response.json()

    except Exception as e:
        log.error(f'Failed to fetch "{API_URL}": {e}')
        return {}


# -------------------------
# EVENT PARSER
# -------------------------
async def get_events(page: Page) -> Dict[str, dict]:
    now = Time.clean(Time.now())
    api_data = await get_api_data(page)

    events = {}

    for event in api_data.get("events", []):
        event_dt = Time.from_str(event["date"], timezone="UTC")

        if event_dt.date() != now.date():
            continue

        event_name = event["match_name"]
        channel = event["channel"]
        category = channel.get("TVCategory", {})
        sport = category.get("name", "Live")

        for i in range(1, 4):
            key_name = f"server{i}URL"
            stream = channel.get(key_name)

            if not stream or stream == "null":
                continue

            tvg_id, logo = leagues.get_tvg_info(sport, event_name)

            name = f"[{sport}] {event_name} {i} ({TAG})"

            events[name] = {
                "url": stream,
                "logo": logo,
                "base": FRONT_URL,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
            }

    return events


# -------------------------
# MAIN SCRAPER
# -------------------------
async def scrape(browser: Browser) -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} cached events")
        return

    log.info("Launching PixelSport session")

    async with browser.new_context() as context:  # BrowserContext
        page = await context.new_page()

        # Load frontend FIRST
        await page.goto(FRONT_URL, wait_until="networkidle", timeout=20_000)

        handler = partial(get_events, page=page)

        events = await network.safe_process(
            handler,
            url_num=1,
            semaphore=network.PW_S,
            log=log,
        )

    if not events:
        log.warning("Using cached events (API blocked or empty)")
        return

    urls.update(events)
    CACHE_FILE.write(urls)

    log.info(f"Collected and cached {len(urls)} event(s)")
