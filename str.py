import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote_plus, urljoin

from utils import Cache, Time, get_logger, network

log = get_logger(__name__)

TAG = "STR"
BASE_URL = "https://streamtp10.com/"
STATUS_URL = f"{BASE_URL}status.json"

OUTPUT_FILE = Path("str_tivimate.m3u8")
CACHE_FILE = Cache("str_cache", exp=6 * 60 * 60)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)
UA_ENC = quote_plus(UA)


# -------------------------------------------------
# Build TiViMate playlist
# -------------------------------------------------
def build_playlist(channels: dict) -> str:
    lines = ["#EXTM3U"]
    chno = 1

    for name, info in channels.items():
        status = info["status"].upper()
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="Live.Event.us" '
            f'tvg-name="{name}" '
            f'tvg-logo="{info["logo"]}" '
            f'group-title="Live Events",{name} --- ({status})'
        )
        lines.append(
            f'{info["m3u8"]}'
            f'|referer={BASE_URL}'
            f'|origin={BASE_URL}'
            f'|user-agent={UA_ENC}'
        )
        chno += 1

    return "\n".join(lines) + "\n"


# -------------------------------------------------
# Extract m3u8 from channel page
# -------------------------------------------------
async def process_event(url: str) -> str | None:
    r = await network.request(url, log=log)
    if not r:
        return None

    match = re.search(
        r'(https?:\/\/[^\s"\']+\.m3u8[^\s"\']*)',
        r.text,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


# -------------------------------------------------
# Load & normalize status.json
# -------------------------------------------------
async def get_status_map() -> dict[str, str]:
    r = await network.request(STATUS_URL, log=log)
    if not r:
        return {}

    try:
        raw = json.loads(r.text)
    except Exception:
        return {}

    status_map = {}

    if isinstance(raw, list):
        for item in raw:
            name = item.get("channel") or item.get("name")
            state = item.get("Estado") or item.get("status")
            if name and state:
                status_map[name.strip()] = state.strip().lower()

    return status_map


# -------------------------------------------------
# Parse homepage channels
# -------------------------------------------------
async def get_events(status_map: dict) -> list[dict]:
    r = await network.request(BASE_URL, log=log)
    if not r:
        return []

    html = r.text
    events = []

    pattern = re.compile(
        r'<div class="channel-info">\s*<h2>([^<]+)</h2>.*?'
        r'<div class="channel-status">.*?(Activo|Inactivo).*?</div>.*?'
        r'<div class="channel-buttons">\s*<a href="([^"]+)"',
        re.S | re.I,
    )

    for name, status_html, link in pattern.findall(html):
        name = name.strip()

        # Status from status.json has priority
        status_api = status_map.get(name, "").lower()
        if status_api != "activo":
            continue

        events.append(
            {
                "name": name,
                "status": "Activo",
                "url": urljoin(BASE_URL, link),
            }
        )

    return events


# -------------------------------------------------
# Main scraper
# -------------------------------------------------
async def scrape():
    cached = CACHE_FILE.load() or {}
    log.info(f"Loaded {len(cached)} cached channel(s)")

    status_map = await get_status_map()
    events = await get_events(status_map)

    log.info(f"Processing {len(events)} active channel(s)")
    now = Time.clean(Time.now()).timestamp()

    for ev in events:
        if ev["name"] in cached:
            continue

        m3u8 = await process_event(ev["url"])
        if not m3u8:
            continue

        cached[ev["name"]] = {
            "m3u8": m3u8,
            "status": "Activo",
            "logo": "https://i.postimg.cc/tgrdPjjC/live-icon-streaming.png",
            "timestamp": now,
        }

    CACHE_FILE.write(cached)

    playlist = build_playlist(cached)
    OUTPUT_FILE.write_text(playlist, encoding="utf-8")

    log.info(f"✅ Wrote {len(cached)} entries to str_tivimate.m3u8")


# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
