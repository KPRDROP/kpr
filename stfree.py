import asyncio
import re
import sys
import types
from datetime import tzinfo, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus, urljoin

if "pytz" not in sys.modules:
    pytz = types.ModuleType("pytz")

    class PytzZone(tzinfo):
        def __init__(self, name: str):
            self._zone = ZoneInfo(name)
            self.zone = name

        def utcoffset(self, dt):
            return self._zone.utcoffset(dt)

        def dst(self, dt):
            return self._zone.dst(dt)

        def tzname(self, dt):
            return self._zone.tzname(dt)

        def fromutc(self, dt):
            
            
            if dt.tzinfo is not self:
                raise ValueError("fromutc: dt.tzinfo is not self")

            # Convert via UTC, then reattach self
            utc_dt = dt.replace(tzinfo=None)
            converted = utc_dt.replace(tzinfo=self._zone).astimezone(self._zone)
            return converted.replace(tzinfo=self)

        def localize(self, dt, is_dst=False):
            if dt.tzinfo is not None:
                raise ValueError("Not naive datetime (tzinfo already set)")
            return dt.replace(tzinfo=self)

    def timezone(name: str):
        return PytzZone(name)

    pytz.timezone = timezone
    pytz.UTC = timezone("UTC")

    sys.modules["pytz"] = pytz

# -------------------------------------------------
# SAFE IMPORTS (utils works unchanged)
# -------------------------------------------------
from urllib.parse import urljoin, quote_plus

from utils import Cache, Time, get_logger, leagues, network

# -------------------------------------------------

log = get_logger(__name__)

TAG = "STRMFREE"
BASE_URL = "https://streamfree.to"

CACHE_FILE = Cache("stfree.json", exp=19_800)

urls: dict[str, dict[str, str | float]] = {}

SPORT_MAP = {
    "NHL": "hockey",
    "Hockey": "hockey",
    "NBA": "basketball",
    "Basketball": "basketball",
    #"NFL": "football",
    #"American Football": "football",
    "MLB": "baseball",
    "Baseball": "baseball",
    "Soccer": "soccer",
}

UA_ENCODED = (
    "Mozilla%2F5.0%20%28Windows%20NT%2010.0%3B%20Win64%3B%20x64%29%20"
    "AppleWebKit%2F537.36%20%28KHTML%2C%20like%20Gecko%29%20"
    "Chrome%2F142.0.0.0%20Safari%2F537.36"
)


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text.strip("-")


def build_embed_referer(sport: str, name: str) -> str:
    category = SPORT_MAP.get(sport, "sports")
    slug = slugify(name)

    return (
        f"{BASE_URL}/embed/{category}/{slug}"
        f"?server=origin&quality=720p&category={category}"
    )


async def get_events() -> dict[str, dict[str, str | float]]:
    events = {}

    r = await network.request(urljoin(BASE_URL, "streams"), log=log)
    if not r:
        return events

    data = r.json()
    now = Time.now().clean()

    for streams in data.get("streams", {}).values():
        if not streams:
            continue

        for stream in streams:
            sport = stream.get("league")
            name = stream.get("name")
            stream_key = stream.get("stream_key")

            if not (sport and name and stream_key):
                continue

            title = f"[{sport}] {name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, name)

            referer = build_embed_referer(sport, name)

            m3u8 = network.build_proxy_url(
                tag=TAG,
                path=f"{stream_key}/index.m3u8",
                query={"stream_name": name},
            )

            # Tivimate pipe headers
            m3u8 += (
                f"|referer={referer}"
                f"|origin={BASE_URL}"
                f"|user-agent={UA_ENCODED}"
            )

            events[title] = {
                "url": m3u8,
                "logo": logo,
                "id": tvg_id or "Live.Event.us",
                "group": sport,
                "timestamp": now.timestamp(),
            }

    return events


async def scrape() -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} events from cache")
        return

    log.info("Scraping StreamFree events")

    events = await network.safe_process(
        get_events,
        url_num=1,
        semaphore=network.HTTP_S,
        log=log,
    )

    if events:
        urls.update(events)
        CACHE_FILE.write(urls)

    log.info(f"Collected {len(urls)} events")


def write_m3u(path="stfree.m3u") -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for title, e in urls.items():
            f.write(
                f'#EXTINF:-1 tvg-id="{e["id"]}" '
                f'tvg-name="{title}" '
                f'tvg-logo="{e["logo"]}" '
                f'group-title="{e["group"]}",'
                f'{title}\n'
            )
            f.write(f'{e["url"]}\n')

    log.info(f"Wrote playlist: {path}")


if __name__ == "__main__":
    asyncio.run(scrape())
    write_m3u()
