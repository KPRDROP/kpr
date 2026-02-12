import json
import os
from functools import partial
from pathlib import Path
from urllib.parse import urljoin, quote_plus

from playwright.async_api import Browser, Page

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "PIXEL"

CACHE_FILE = Cache(TAG, exp=19_800)

# ✅ BASE URL FROM SECRET
BASE_URL = os.getenv("PIXNINE_BASE_URL")

if not BASE_URL:
    raise ValueError("PIXNINE_BASE_URL secret is not set")

# Output files
VLC_FILE = Path("pixnine_vlc.m3u8")
TIVIMATE_FILE = Path("pixnine_tivimate.m3u8")

# User agents
VLC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
)

TIVIMATE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)

TIVIMATE_UA_ENC = quote_plus(TIVIMATE_USER_AGENT)


# -------------------------------------------------
# Generate VLC playlist
# -------------------------------------------------
def build_vlc_playlist(data: dict) -> None:
    lines = ["#EXTM3U"]
    chno = 1

    for name, info in data.items():
        clean_name = name.replace("@", "vs")

        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{info["id"]}" '
            f'tvg-name="{clean_name}" '
            f'tvg-logo="{info["logo"]}" '
            f'group-title="Live Events",{clean_name}'
        )

        lines.append(f"#EXTVLCOPT:http-referrer={BASE_URL}")
        lines.append(f"#EXTVLCOPT:http-origin={BASE_URL}")
        lines.append(f"#EXTVLCOPT:http-user-agent={VLC_USER_AGENT}")
        lines.append(info["url"])

        chno += 1

    VLC_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"✅ Wrote {len(data)} entries to pixnine_vlc.m3u8")


# -------------------------------------------------
# Generate TiViMate playlist
# -------------------------------------------------
def build_tivimate_playlist(data: dict) -> None:
    lines = ["#EXTM3U"]
    chno = 1

    for name, info in data.items():
        clean_name = name.replace("@", "vs")

        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{info["id"]}" '
            f'tvg-name="{clean_name}" '
            f'tvg-logo="{info["logo"]}" '
            f'group-title="Live Events",{clean_name}'
        )

        lines.append(
            f'{info["url"]}'
            f'|referer={BASE_URL}/'
            f'|origin={BASE_URL}'
            f'|user-agent={TIVIMATE_UA_ENC}'
        )

        chno += 1

    TIVIMATE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"✅ Wrote {len(data)} entries to pixnine_tivimate.m3u8")


# -------------------------------------------------
async def get_api_data(page: Page) -> dict:
    try:
        await page.goto(
            url := urljoin(BASE_URL, "backend/livetv/events"),
            wait_until="domcontentloaded",
            timeout=10_000,
        )

        raw_json = await page.locator("pre").inner_text(timeout=5_000)
    except Exception as e:
        log.error(f'Failed to fetch "{url}": {e}')
        return {}

    return json.loads(raw_json)


# -------------------------------------------------
async def get_events(page: Page) -> dict:
    now = Time.clean(Time.now())
    api_data = await get_api_data(page)

    events = {}

    for event in api_data.get("events", []):
        event_dt = Time.from_str(event["date"], timezone="UTC")

        if event_dt.date() != now.date():
            continue

        event_name = event["match_name"]

        channel_info: dict[str, str] = event["channel"]
        category: dict[str, str] = channel_info["TVCategory"]
        sport = category["name"]

        stream_urls = [(i, f"server{i}URL") for i in range(1, 4)]

        for z, stream_url in stream_urls:
            if (stream_link := channel_info.get(stream_url)) and stream_link != "null":
                key = f"[{sport}] {event_name} {z} ({TAG})"

                tvg_id, logo = leagues.get_tvg_info(sport, event_name)

                events[key] = {
                    "url": stream_link,
                    "logo": logo,
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                }

    return events


# -------------------------------------------------
async def scrape(browser: Browser) -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
    else:
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

        urls.update(events or {})
        CACHE_FILE.write(urls)
        log.info(f"Collected and cached {len(urls)} new event(s)")

    # ✅ Always build playlists
    build_vlc_playlist(urls)
    build_tivimate_playlist(urls)
