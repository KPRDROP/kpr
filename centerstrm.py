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
            f'|referer=https://streamcenter.xyz/'
            f'|origin=https://streamcenter.xyz'
            f'|user-agent={UA_ENC}'
        )
        ch += 1

    return "\n".join(lines) + "\n"


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    api_data = API_FILE.load(per_entry=False, index=-1)
    if not api_data:
        if r := await network.request(
            BASE_URL,
            log=log,
            params={"pageNumber": 1, "pageSize": 500},
        ):
            api_data = r.json()
            API_FILE.write(api_data)
        else:
            return []

    events = []

    # ✅ FIX: wide live/upcoming window
    start_dt = now.delta(hours=-6)
    end_dt = now.delta(hours=6)

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
                "link": iframe.strip(),
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
    log.info(f"Found {len(events)} candidate events")

    if not events:
        log.info("No events passed schedule filter")
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
                    timeout=20,
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
                    "base": "https://streamcenter.xyz",
                    "timestamp": ev["timestamp"],
                    "id": tvg_id or "NFL.Dummy.us",
                }

        finally:
            await browser.close()

    if len(cached_urls) == cached_count:
        log.info("No new streams captured")
        return

    CACHE_FILE.write(cached_urls)

    playlist = build_playlist(cached_urls)
    OUTPUT_FILE.write_text(playlist, encoding="utf-8")

    log.info(f"Updated playlist with {len(cached_urls) - cached_count} new event(s)")
