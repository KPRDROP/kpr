#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
StreamFree scraper
- Outputs stfree.m3u (TiviMate format)
- Fully fixes pytz compatibility (tzinfo-safe)
"""

# -------------------------------------------------
# PYTZ COMPATIBILITY SHIM (100% SAFE)
# -------------------------------------------------
import sys
import types
from datetime import tzinfo, timedelta
from zoneinfo import ZoneInfo

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
            # Correct pytz behavior:
            # dt.tzinfo MUST be self
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
BASE_URL = "https://streamfree.to/"
OUTPUT_FILE = "stfree.m3u"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

ENCODED_UA = quote_plus(USER_AGENT)

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=19_800)

urls: dict[str, dict[str, str | float]] = {}

# -------------------------------------------------
async def get_events() -> dict[str, dict[str, str | float]]:
    events = {}

    r = await network.request(urljoin(BASE_URL, "streams"), log=log)
    if not r:
        return events

    data = r.json()
    now = Time.clean(Time.now()).timestamp()

    for streams in data.get("streams", {}).values():
        for s in streams or []:
            sport = s.get("league")
            name = s.get("name")
            key = s.get("stream_key")

            if not (sport and name and key):
                continue

            tvg_id, logo = leagues.get_tvg_info(sport, name)

            event_key = f"[{sport}] {name} ({TAG})"

            events[event_key] = {
                "url": network.build_proxy_url(
                    tag=TAG,
                    path=f"{key}/index.m3u8",
                    query={"stream_name": name},
                ),
                "logo": logo,
                "id": tvg_id or "Live.Event.us",
                "group": sport,
                "name": name,
                "timestamp": now,
            }

    return events

# -------------------------------------------------
def write_playlist(entries: dict[str, dict]) -> None:
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for e in entries.values():
            f.write(
                f'#EXTINF:-1 tvg-id="{e["id"]}" '
                f'tvg-name="{e["name"]}" '
                f'tvg-logo="{e["logo"]}" '
                f'group-title="{e["group"]}",{e["name"]}\n'
            )
            f.write(
                f'{e["url"]}'
                f'|referer={BASE_URL}'
                f'|origin={BASE_URL}'
                f'|user-agent={ENCODED_UA}\n'
            )

    log.info(f"✅ Playlist written: {OUTPUT_FILE}")

# -------------------------------------------------
async def scrape() -> None:
    cached = CACHE_FILE.load() or {}
    urls.update(cached)

    log.info(f"Loaded {len(cached)} cached events")

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

    log.info(f"🎉 Done — total events: {len(urls)}")

# -------------------------------------------------
if __name__ == "__main__":
    import asyncio

    log.info("🚀 Starting StreamFree scraper...")
    asyncio.run(scrape())
