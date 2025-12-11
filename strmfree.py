from urllib.parse import urljoin
import httpx
from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

CACHE_FILE = Cache("streamfree.json", exp=19_800)

BASE_URL = "https://streamfree.to"

TAG = "STRMFR"


async def refresh_api_cache(
    client: httpx.AsyncClient,
    url: str,
) -> dict[str, dict[str, list]]:
    try:
        r = await client.get(url)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{url}": {e}')

        return {}

    return r.json()


async def get_events(client: httpx.AsyncClient) -> dict[str, dict[str, str | float]]:
    api_data = await refresh_api_cache(client, urljoin(BASE_URL, "streams"))

    events = {}

    now = Time.now().timestamp()

    for streams in api_data.get("streams", {}).values():
        if not streams:
            continue

        for stream in streams:
            sport, name, stream_key = (
                stream.get("league"),
                stream.get("name"),
                stream.get("stream_key"),
            )

            if not (sport and name and stream_key):
                continue

            key = f"[{sport}] {name} ({TAG})"

            logo = (
                urljoin(BASE_URL, thumbnail)
                if (thumbnail := stream.get("thumbnail_url"))
                else None
            )

            tvg_id, pic = leagues.get_tvg_info(sport, name)

            events[key] = {
                "url": network.build_proxy_url(
                    tag=TAG,
                    path=f"{stream_key}720p/index.m3u8",
                    query={"stream_name": name},
                ),
                "logo": logo or pic,
                "base": BASE_URL,
                "timestamp": now,
                "id": tvg_id or "Live.Event.us",
            }

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
        return

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(client)

    urls.update(events)

    CACHE_FILE.write(urls)

    log.info(f"Collected and cached {len(urls)} new event(s)")
