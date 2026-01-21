import asyncio
from functools import partial
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "STRMCNTR"

API_URL = "https://backend.streamcenter.live/api/Parties"
BASE_ORIGIN = "https://streams.center"

OUTPUT_FILE = Path("centerstrm.m3u")
CACHE_FILE = Cache("centerstrm.json", exp=10_800)

CATEGORIES = {
    4: "Basketball",
    9: "Football",
    13: "Baseball",
    14: "American Football",
    15: "Motor Sport",
    16: "Hockey",
    17: "MMA",
    18: "Boxing",
    19: "NCAA",
    20: "WWE",
    21: "Tennis",
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)
UA_ENC = quote_plus(UA)

urls: dict[str, dict] = {}


# -------------------------------------------------
# Build playlist
# -------------------------------------------------
def write_playlist(data: dict) -> None:
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
            f'|referer={BASE_ORIGIN}/'
            f'|origin={BASE_ORIGIN}'
            f'|user-agent={UA_ENC}'
        )
        ch += 1

    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Wrote {len(data)} entries to centerstrm.m3u")


# -------------------------------------------------
# Load events from API
# -------------------------------------------------
async def get_events(cached_keys: set[str]) -> list[dict]:
    r = await network.request(API_URL, log=log)
    if not r:
        return []

    api_data = r.json()
    events = []

    now = Time.now()

    for row in api_data:
        category_id = row.get("categoryId")
        sport = CATEGORIES.get(category_id)
        name = row.get("gameName")
        video_url = row.get("videoUrl")
        begin = row.get("beginPartie")
        end = row.get("endPartie")

        if not all([sport, name, video_url, begin, end]):
            continue

        start = Time.from_str(begin, timezone="CET")
        stop = Time.from_str(end, timezone="CET")

        if not (start <= now <= stop):
            continue

        key = f"[{sport}] {name} ({TAG})"
        if key in cached_keys:
            continue

        logo = row.get("logoTeam1") or row.get("logoTeam2")

        events.append(
            {
                "sport": sport,
                "event": name,
                "video": video_url.split("<")[0].strip(),
                "logo": logo,
            }
        )

    return events


# -------------------------------------------------
# Main scraper
# -------------------------------------------------
async def scrape() -> None:
    cached = CACHE_FILE.load()
    urls.update(cached)

    log.info(f"Loaded {len(cached)} cached events")

    events = await get_events(set(cached.keys()))
    log.info(f"Found {len(events)} live API events")

    if not events:
        write_playlist(urls)
        return

    async with async_playwright() as p:
        browser, context = await network.browser(p, browser="external")

        try:
            for i, ev in enumerate(events, 1):
                handler = partial(
                    network.process_event,
                    url=ev["video"],
                    url_num=i,
                    context=context,
                    timeout=20,
                    log=log,
                )

                stream = await network.safe_process(
                    handler,
                    url_num=i,
                    semaphore=network.PW_S,
                    log=log,
                )

                if not stream:
                    continue

                key = f"[{ev['sport']}] {ev['event']} ({TAG})"
                tvg_id, _ = leagues.get_tvg_info(ev["sport"], ev["event"])

                urls[key] = {
                    "url": stream,
                    "logo": ev["logo"],
                    "base": BASE_ORIGIN,
                    "timestamp": Time.now().timestamp(),
                    "id": tvg_id or "Live.Event.us",
                }

        finally:
            await browser.close()

    CACHE_FILE.write(urls)
    write_playlist(urls)


# -------------------------------------------------
# Entrypoint
# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
