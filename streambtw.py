#!/usr/bin/env python3
import base64
import re
from functools import partial
from urllib.parse import urljoin, quote
from pathlib import Path

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
TAG = "STRMBTW"

BASE_URL = "https://hiteasport.info"

REFERER = "https://hiteasport.info/"
ORIGIN = "https://hiteasport.info"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
UA_ENC = quote(USER_AGENT)

OUT_VLC = Path("Streambtw_VLC.m3u8")
OUT_TIVI = Path("Streambtw_TiviMate.m3u8")

CACHE_FILE = Cache(TAG, exp=3600)
API_FILE = Cache(f"{TAG}-api", exp=28800)

urls: dict[str, dict] = {}

M3U8_RE = re.compile(r'var\s+\w+\s*=\s*"([^"]+)"', re.I)

# --------------------------------------------------
def fix_league(s: str) -> str:
    return " ".join(s.split("-"))

# --------------------------------------------------
async def process_event(url: str, url_num: int) -> str | None:
    html = await network.request(url, log=log)
    if not html:
        return None

    m = M3U8_RE.search(html.text)
    if not m:
        log.info(f"URL {url_num}) No M3U8 found")
        return None

    stream = m.group(1)
    if not stream.startswith("http"):
        stream = base64.b64decode(stream).decode("utf-8")

    log.info(f"URL {url_num}) Captured M3U8")
    return stream

# --------------------------------------------------
async def get_events() -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api := API_FILE.load(per_entry=False)):
        log.info("Fetching API data")

        r = await network.request(
            urljoin(BASE_URL, "public/api.php"),
            log=log,
            params={"action": "get"},
        )
        if not r:
            return []

        api = r.json()
        api["timestamp"] = now.timestamp()
        API_FILE.write(api)

    events: list[dict[str, str]] = []

    for group in api.get("groups", []):
        sport = fix_league(group.get("title") or "Live")

        for item in group.get("items", []):
            if not item.get("url"):
                continue

            events.append({
                "sport": sport,
                "event": item.get("title", "Live Event"),
                "link": item["url"],
            })

    return events

# --------------------------------------------------
def build_playlists(data: dict[str, dict]):
    vlc = ["#EXTM3U"]
    tiv = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        vlc.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}",{name}'
        )
        vlc.append(f"#EXTVLCOPT:http-referrer={REFERER}")
        vlc.append(f"#EXTVLCOPT:http-origin={ORIGIN}")
        vlc.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc.append(e["url"])

        tiv.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}",{name}'
        )
        tiv.append(
            f'{e["url"]}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}'
        )

        ch += 1

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tiv), encoding="utf-8")

    log.info("Playlists written")

# --------------------------------------------------
async def scrape():
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} cached event(s)")
        build_playlists(urls)
        return

    events = await get_events()
    log.info(f"Processing {len(events)} new URL(s)")

    now = Time.clean(Time.now())

    for i, ev in enumerate(events, 1):
        handler = partial(process_event, ev["link"], i)
        url = await network.safe_process(
            handler, i, network.HTTP_S, log
        )
        if not url:
            continue

        key = f"[{ev['sport']}] {ev['event']} ({TAG})"
        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        urls[key] = {
            "url": url,
            "logo": logo,
            "timestamp": now.timestamp(),
            "id": tvg_id or "Live.Event.us",
        }

    CACHE_FILE.write(urls)
    build_playlists(urls)

# --------------------------------------------------
if __name__ == "__main__":
    import asyncio
    asyncio.run(scrape())
