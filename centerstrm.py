from functools import partial
from pathlib import Path

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMCNTR"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)

API_FILE = Cache(f"{TAG.lower()}-api.json", exp=28_800)

BASE_URL = "https://backend.streamcenter.live/api/Parties"

OUTPUT_FILE = Path("centerstrm.m3u")

CATEGORIES = {
    4: "Basketball",
    9: "Football",
    13: "Baseball",
    14: "American Football",
    15: "Motor Sport",
    16: "Hockey",
    17: "Fight MMA",
    18: "Boxing",
    19: "NCAA Sports",
    20: "WWE",
    21: "Tennis",
}

UA_ENC = (
    "Mozilla%2F5.0%20(Windows%20NT%2010.0%3B%20Win64%3B%20x64)"
    "%20AppleWebKit%2F537.36%20(KHTML%2C%20like%20Gecko)"
    "%20Chrome%2F144.0.0.0%20Safari%2F537.36"
)


def build_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]

    for name, e in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-id="{e["id"]}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )

        lines.append(
            f'{e["url"]}'
            f'|referer=https://streams.center/'
            f'|origin=https://streams.center'
            f'|user-agent={UA_ENC}'
        )

    return "\n".join(lines) + "\n"


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(
            BASE_URL,
            log=log,
            params={"pageNumber": 1, "pageSize": 500},
        ):
            api_data = r.json()
            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(hours=-1)
    end_dt = now.delta(minutes=5)

    for row in api_data:
        sport = CATEGORIES.get(row.get("categoryId"))
        name = row.get("gameName")
        iframe = row.get("videoUrl")
        begin = row.get("beginPartie")

        if not all([sport, name, iframe, begin]):
            continue

        key = f"[{sport}] {name} ({TAG})"
        if key in cached_keys:
            continue

        event_dt = Time.from_str(begin, timezone="CET")
        if not start_dt <= event_dt <= end_dt:
            continue

        events.append(
            {
                "sport": sport,
                "event": name,
                "link": iframe.replace("<", "?", 1),
                "timestamp": event_dt.timestamp(),
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()
    cached_count = len(cached_urls)

    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} cached events")

    events = await get_events(cached_urls.keys())

    if not events:
        log.info("No new events found")
        return

    async with async_playwright() as p:
        browser, context = await network.browser(p, browser="external")

        try:
            for i, ev in enumerate(events, 1):
                handler = partial(
                    network.process_event,
                    url=ev["link"],
                    url_num=i,
                    context=context,
                    log=log,
                )

                url = await network.safe_process(
                    handler,
                    url_num=i,
                    semaphore=network.PW_S,
                    log=log,
                )

                if not url:
                    continue

                key = f"[{ev['sport']}] {ev['event']} ({TAG})"
                tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

                urls[key] = cached_urls[key] = {
                    "url": url,
                    "logo": logo,
                    "base": "https://streams.center",
                    "timestamp": ev["timestamp"],
                    "id": tvg_id or "Live.Event.us",
                }

        finally:
            await browser.close()

    new_count = len(cached_urls) - cached_count
    if not new_count:
        log.info("No new streams captured")
        return

    CACHE_FILE.write(cached_urls)

    playlist = build_playlist(cached_urls)

    if OUTPUT_FILE.exists() and OUTPUT_FILE.read_text() == playlist:
        log.info("Playlist unchanged — not rewriting")
        return

    OUTPUT_FILE.write_text(playlist, encoding="utf-8")
    log.info(f"Added {new_count} new events and updated playlist")
