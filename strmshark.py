import os
import sys
import re
from functools import partial
from urllib.parse import quote

from selectolax.parser import HTMLParser

# 🔧 Ensure local imports work
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "SHARK"

BASE_URL = os.getenv("SHARK_BASE_URL", "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("❌ SHARK_BASE_URL secret not set")

OUTPUT_FILE = "strmshark_tivimate.m3u8"

USER_AGENT_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
USER_AGENT = quote(USER_AGENT_RAW, safe="")

CACHE_FILE = Cache("shark.json", exp=10_800)
HTML_CACHE = Cache("shark-html.json", exp=19_800)

urls: dict[str, dict] = {}


async def process_event(url: str, url_num: int) -> str | None:
    r = await network.request(url, log=log)
    if not r:
        return None

    data = r.json()
    streams = data.get("urls")
    if not streams:
        return None

    return streams[0]


async def refresh_html_cache(now_ts: float) -> dict:
    log.info("🔄 Refreshing HTML cache")

    events = {}
    r = await network.request(BASE_URL, log=log)
    if not r:
        return events

    soup = HTMLParser(r.content)
    pattern = re.compile(r"openEmbed\('([^']+)'\)", re.I)

    # ✅ FIX: correct container
    for box in soup.css(".channel"):
        date_node = box.css_first(".ch-date")
        cat_node = box.css_first(".ch-category")
        name_node = box.css_first(".ch-name")
        link_node = box.css_first("a.hd-link.secondary")

        if not (date_node and cat_node and name_node and link_node):
            continue

        onclick = link_node.attributes.get("onclick", "")
        match = pattern.search(onclick)
        if not match:
            continue

        event_dt = Time.from_str(date_node.text(strip=True), timezone="EST")
        sport = cat_node.text(strip=True)
        event = name_node.text(strip=True)

        stream_api = match.group(1).replace("player.php", "get-stream.php")

        key = f"[{sport}] {event} ({TAG})"

        events[key] = {
            "sport": sport,
            "event": event,
            "link": stream_api,
            "event_ts": event_dt.timestamp(),
            "timestamp": now_ts,
        }

    log.info(f"📺 Parsed {len(events)} events from HTML")
    return events


async def get_events(cached_keys: list[str]) -> list[dict]:
    now = Time.clean(Time.now())

    events = HTML_CACHE.load()
    if not events:
        events = await refresh_html_cache(now.timestamp())
        HTML_CACHE.write(events)

    live = []

    # ✅ FIX: allow future events
    for k, v in events.items():
        if k in cached_keys:
            continue
        live.append(v)

    return live


def build_tivimate_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]

    for title, entry in sorted(data.items(), key=lambda x: x[1]["timestamp"]):
        name = f"[{entry['sport']}] {entry['event']} ({TAG})"

        lines.append(
            f'#EXTINF:-1 '
            f'tvg-id="{entry["id"]}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{entry["logo"]}" '
            f'group-title="Live Events",{name}'
        )

        lines.append(
            f'{entry["url"]}'
            f'|referer={BASE_URL}'
            f'|origin={BASE_URL}'
            f'|user-agent={USER_AGENT}'
        )

    return "\n".join(lines) + "\n"


async def scrape():
    cached = CACHE_FILE.load() or {}
    urls.update(cached)

    log.info(f"Loaded {len(cached)} cached events")

    events = await get_events(cached.keys())
    log.info(f"Processing {len(events)} events")

    for i, ev in enumerate(events, 1):
        handler = partial(process_event, ev["link"], i)

        stream = await network.safe_process(
            handler,
            url_num=i,
            semaphore=network.HTTP_S,
            log=log,
        )

        if not stream:
            continue

        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        key = f"[{ev['sport']}] {ev['event']} ({TAG})"

        urls[key] = cached[key] = {
            "url": stream,
            "logo": logo,
            "timestamp": ev["event_ts"],
            "id": tvg_id or "Live.Event.us",
            "sport": ev["sport"],
            "event": ev["event"],
        }

    CACHE_FILE.write(cached)

    playlist = build_tivimate_playlist(urls)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(playlist)

    log.info(f"✅ Saved {OUTPUT_FILE} ({len(urls)} entries)")


if __name__ == "__main__":
    import asyncio
    asyncio.run(scrape())
