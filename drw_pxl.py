import os
import json
from pathlib import Path
from urllib.parse import quote

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "PIXEL"

# 🔐 API URL FROM SECRETS
BASE_URL = os.getenv("PIXEL_API_URL")
if not BASE_URL:
    raise RuntimeError("PIXEL_API_URL secret is not set")

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=900)
OUTPUT_FILE = Path("drw_pxl_tivimate.m3u8")

UA_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
)
UA_ENC = quote(UA_RAW)

REFERER = "https://pixelsport.tv/"
ORIGIN = "https://pixelsport.tv"

urls: dict[str, dict] = {}


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
            f'|referer={REFERER}'
            f'|origin={ORIGIN}'
            f'|user-agent={UA_ENC}'
        )
        ch += 1

    return "\n".join(lines) + "\n"


async def scrape() -> None:
    now = Time.clean(Time.now())
    cached = CACHE_FILE.load()
    urls.update(cached)

    log.info(f"Loaded {len(cached)} cached events")

    r = await network.request(BASE_URL, log=log)
    if not r:
        log.error("Failed to fetch PixelSport API")
        return

    api_data = r.json()
    events = api_data.get("events", [])

    added = 0

    for ev in events:
        try:
            event_dt = Time.from_str(ev["date"], timezone="UTC")
        except Exception:
            continue

        # 🎯 Today + upcoming window
        if abs((event_dt - now).total_seconds()) > 6 * 3600:
            continue

        event_name = ev.get("match_name")
        channel = ev.get("channel") or {}
        category = channel.get("TVCategory") or {}

        sport = category.get("name")
        if not all([event_name, sport]):
            continue

        for idx in (1, 2):
            stream = channel.get(f"server{idx}URL")
            if not stream or stream == "null":
                continue

            key = f"[{sport}] {event_name} {idx} ({TAG})"
            if key in urls:
                continue

            tvg_id, logo = leagues.get_tvg_info(sport, event_name)

            urls[key] = {
                "url": stream,
                "logo": logo,
                "base": ORIGIN,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
            }

            added += 1

    CACHE_FILE.write(urls)

    playlist = build_playlist(urls)
    OUTPUT_FILE.write_text(playlist, encoding="utf-8")

    log.info(f"Wrote {added} new streams")
    log.info(f"Total entries: {len(urls)}")


if __name__ == "__main__":
    import asyncio

    log.info("🚀 Starting PixelSport scraper...")
    asyncio.run(scrape())
