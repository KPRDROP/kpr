import os
import asyncio
from functools import partial
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import Browser, async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "EMBEDHD"

# 🔐 API URL FROM SECRET
BASE_URL = os.getenv("EMBEDHD_API_URL")
if not BASE_URL:
    raise RuntimeError("EMBEDHD_API_URL secret is not set")

CACHE_FILE = Cache(TAG, exp=5_400)
API_CACHE = Cache(f"{TAG}-api", exp=28_800)

# OUTPUT FILES
OUT_VLC = Path("embedhd_vlc.m3u8")
OUT_TIVI = Path("embedhd_tivimate.m3u8")

REFERER = "https://vividmosaica.com/"
ORIGIN = "https://vividmosaica.com/"

UA_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
)
UA_ENC = quote(UA_RAW)


def fix_league(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split()) if len(s) > 5 else s.upper()


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_CACHE.load(per_entry=False)):
        log.info("Refreshing API cache")

        api_data = {"timestamp": now.timestamp()}

        if r := await network.request(BASE_URL, log=log):
            api_data = r.json()
            api_data["timestamp"] = now.timestamp()

        API_CACHE.write(api_data)

    events = []

    start_dt = now.delta(hours=-3)
    end_dt = now.delta(minutes=30)

    for info in api_data.get("days", []):
        for event in info["items"]:
            if event["league"] == "channel tv":
                continue

            event_dt = Time.from_str(event["when_et"], timezone="ET")
            if not start_dt <= event_dt <= end_dt:
                continue

            sport = fix_league(event["league"])
            event_name = event["title"]

            key = f"[{sport}] {event_name} ({TAG})"
            if key in cached_keys:
                continue

            streams = event["streams"]
            if not streams or not streams[0].get("link"):
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event_name,
                    "link": streams[0]["link"],
                    "timestamp": now.timestamp(),
                }
            )

    return events


def build_vlc_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )
        lines.append(f"#EXTVLCOPT:http-referrer={REFERER}")
        lines.append(f"#EXTVLCOPT:http-origin={ORIGIN}")
        lines.append(f"#EXTVLCOPT:http-user-agent={UA_RAW}")
        lines.append(e["url"])
        ch += 1

    return "\n".join(lines) + "\n"


def build_tivimate_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
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


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()
    urls.update(cached_urls)

    log.info(f"Loaded {len(cached_urls)} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    if events:
        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):

                # ✅ DIRECT STREAM — NO PLAYWRIGHT
                if ".m3u8" in ev["link"]:
                    stream = ev["link"]
                else:
                    async with network.event_page(context) as page:
                        handler = partial(
                            network.process_event,
                            url=ev["link"],
                            url_num=i,
                            page=page,
                            log=log,
                        )

                        stream = await network.safe_process(
                            handler,
                            url_num=i,
                            semaphore=network.PW_S,
                            log=log,
                        )

                if stream:
                    tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])
                    key = f"[{ev['sport']}] {ev['event']} ({TAG})"

                    urls[key] = {
                        "url": stream,
                        "logo": logo,
                        "id": tvg_id or "Live.Event.us",
                        "timestamp": ev["timestamp"],
                    }

    CACHE_FILE.write(urls)

    OUT_VLC.write_text(build_vlc_playlist(urls), encoding="utf-8")
    OUT_TIVI.write_text(build_tivimate_playlist(urls), encoding="utf-8")

    log.info(f"Wrote {len(urls)} total events")


async def main() -> None:
    log.info("🚀 Starting EmbedHD scraper...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            await scrape(browser)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
