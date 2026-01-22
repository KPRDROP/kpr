import os
import sys
import re
from functools import partial
from urllib.parse import quote

# 🔧 FIX: ensure project root is in PYTHONPATH
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from utils import Cache, Time, get_logger, leagues, network  # ← FIXED IMPORT

from selectolax.parser import HTMLParser

log = get_logger(__name__)

TAG = "SHARK"

BASE_URL = os.getenv("SHARK_BASE_URL", "").rstrip("/")
if not BASE_URL:
    raise RuntimeError("❌ SHARK_BASE_URL secret is missing")

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
    events = {}

    r = await network.request(BASE_URL, log=log)
    if not r:
        return events

    soup = HTMLParser(r.content)
    pattern = re.compile(r"openEmbed\('([^']+)'\)", re.I)

    for row in soup.css(".row"):
        date_node = row.css_first(".ch-date")
        cat_node = row.css_first(".ch-category")
        name_node = row.css_first(".ch-name")

        if not (date_node and cat_node and name_node):
            continue

        event_dt = Time.from_str(date_node.text(strip=True), timezone="EST")
        sport = cat_node.text(strip=True)
        event = name_node.text(strip=True)

        btn = row.css_first("a.hd-link.secondary")
        if not btn:
            continue

        onclick = btn.attributes.get("onclick", "")
        match = pattern.search(onclick)
        if not match:
            continue

        link = match.group(1).replace("player.php", "get-stream.php")

        key = f"[{sport}] {event} ({TAG})"

        events[key] = {
            "sport": sport,
            "event": event,
            "link": link,
            "event_ts": event_dt.timestamp(),
            "timestamp": now_ts,
        }

    return events


async def get_events(cached_keys: list[str]) -> list[dict]:
    now = Time.clean(Time.now())

    events = HTML_CACHE.load()
    if not events:
        events = await refresh_html_cache(now.timestamp())
        HTML_CACHE.write(events)

    live = []
    start_ts = now.delta(hours=-1).timestamp()
    end_ts = now.delta(minutes=10).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue
        if not start_ts <= v["event_ts"] <= end_ts:
            continue
        live.append(v)

    return live


def build_tivimate_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]

    for title, entry in sorted(data.items(), key=lambda x: x[1]["timestamp"]):
        sport = entry["sport"]
        event = entry["event"]
        stream = entry["url"]
        logo = entry["logo"]
        tvg_id = entry["id"]

        name = f"[{sport}] {event} ({TAG})"

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{logo}" '
            f'group-title="Live Events",{name}'
        )

        lines.append(
            f"{stream}"
            f"|referer={BASE_URL}"
            f"|origin={BASE_URL}"
            f"|user-agent={USER_AGENT}"
        )

    return "\n".join(lines) + "\n"


async def scrape():
    cached = CACHE_FILE.load() or {}
    urls.update(cached)

    events = await get_events(cached.keys())

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
