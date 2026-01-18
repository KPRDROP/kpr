#!/usr/bin/env python3
from urllib.parse import urljoin
import urllib.parse

# ✅ ABSOLUTE IMPORTS (GitHub Actions safe)
from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMFREE"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=19_800)

BASE_URL = "https://streamfree.to"
OUTPUT_FILE = "stfree.m3u"

USER_AGENT_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

USER_AGENT_ENCODED = urllib.parse.quote(USER_AGENT_RAW, safe="")


async def get_events() -> dict[str, dict[str, str | float]]:
    events = {}

    r = await network.request(
        urljoin(BASE_URL, "streams"),
        log=log,
    )

    if not r:
        return events

    api_data: dict = r.json()
    now = Time.clean(Time.now())

    for streams in api_data.get("streams", {}).values():
        if not streams:
            continue

        for stream in streams:
            sport = stream.get("league")
            name = stream.get("name")
            stream_key = stream.get("stream_key")

            if not (sport and name and stream_key):
                continue

            key = f"[{sport}] {name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, name)

            events[key] = {
                "url": network.build_proxy_url(
                    tag=TAG,
                    path=f"{stream_key}/index.m3u8",
                    query={"stream_name": name},
                ),
                "logo": logo,
                "base": BASE_URL,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
                "sport": sport,
                "event": name,
            }

    return events


def write_playlist(data: dict[str, dict]):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for title, e in data.items():
            f.write(
                f'#EXTINF:-1 tvg-id="{e["id"]}" '
                f'tvg-name="{title}" '
                f'group-title="{e["sport"]}",{title}\n'
            )
            f.write(
                f'{e["url"]}'
                f'|referer={BASE_URL}'
                f'|origin={BASE_URL}'
                f'|user-agent={USER_AGENT_ENCODED}\n'
            )


async def scrape() -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
        write_playlist(urls)
        return

    log.info(f'Scraping from "{BASE_URL}"')

    events = await network.safe_process(
        get_events,
        url_num=1,
        semaphore=network.HTTP_S,
        log=log,
    )

    if events:
        urls.update(events)
        CACHE_FILE.write(urls)
        write_playlist(urls)

    log.info(f"Collected and cached {len(urls)} event(s)")


if __name__ == "__main__":
    import asyncio
    asyncio.run(scrape())
