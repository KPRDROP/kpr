from functools import partial
from pathlib import Path

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)import asyncio
import os
from functools import partial
from urllib.parse import quote, urlparse

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMCNTR"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)
API_FILE = Cache(f"{TAG.lower()}-api.json", exp=28_800)

# BASE_URL FROM SECRET
BASE_URL = os.getenv("CENTERSTRM_API")
if not BASE_URL:
    raise RuntimeError("CENTERSTRM_API secret is missing")

OUTPUT_FILE = "centerstrm.m3u"

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

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)
UA_ENC = quote(UA)


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        if r := await network.request(
            BASE_URL,
            log=log,
            params={"pageNumber": 1, "pageSize": 500},
        ):
            api_data = r.json()
            api_data.append({"timestamp": now.timestamp()})
            API_FILE.write(api_data)
        else:
            return []

    events = []

    start_dt = now.delta(hours=-1)
    end_dt = now.delta(minutes=5)

    for item in api_data:
        sport = CATEGORIES.get(item.get("categoryId"))
        name = item.get("gameName")
        iframe = item.get("videoUrl")
        event_time = item.get("beginPartie")

        if not (sport and name and iframe and event_time):
            continue

        key = f"[{sport}] {name} ({TAG})"
        if key in cached_keys:
            continue

        event_dt = Time.from_str(event_time, timezone="CET")
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


def write_m3u(data: dict[str, dict]) -> None:
    lines = ["#EXTM3U"]

    for chno, (title, ev) in enumerate(data.items(), start=1):
        referer = ev.get("link")
        origin = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"

        stream = (
            f'{ev["url"]}'
            f"|referer={referer}"
            f"|origin={origin}"
            f"|user-agent={UA_ENC}"
        )

        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{ev["id"]}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{ev["logo"]}" '
            f'group-title="Live Events",{title}'
        )
        lines.append(stream)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info(f"Playlist written: {OUTPUT_FILE}")


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()
    urls.update(cached_urls)

    log.info(f"Loaded {len(cached_urls)} cached event(s)")
    log.info('Scraping from "https://streams.center"')

    events = await get_events(list(cached_urls.keys()))
    log.info(f"Processing {len(events)} new event(s)")

    if events:
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

                    stream = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    if not stream:
                        continue

                    sport, name = ev["sport"], ev["event"]
                    key = f"[{sport}] {name} ({TAG})"
                    tvg_id, logo = leagues.get_tvg_info(sport, name)

                    cached_urls[key] = {
                        "url": stream,
                        "logo": logo,
                        "timestamp": ev["timestamp"],
                        "id": tvg_id or "Live.Event.us",
                        "link": ev["link"],
                    }

            finally:
                await browser.close()

    CACHE_FILE.write(cached_urls)
    write_m3u(cached_urls)


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
