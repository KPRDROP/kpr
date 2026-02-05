import asyncio
import re
from pathlib import Path
from urllib.parse import quote_plus, urljoin

from utils import Cache, Time, get_logger, network

log = get_logger(__name__)

# -------------------------------------------------
# CONFIG
# -------------------------------------------------

BASE_URL = "https://streamtp10.com/"
TAG = "STR"

OUTPUT_FILE = Path("str_tivimate.m3u8")
CACHE_FILE = Cache("str_channels", exp=6 * 60 * 60)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)
UA_ENC = quote_plus(USER_AGENT)

# -------------------------------------------------
# Build TiViMate playlist
# -------------------------------------------------
def build_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]
    chno = 1

    for name, info in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="Live.Event.us" '
            f'tvg-name="{name}" '
            f'tvg-logo="{info["logo"]}" '
            f'group-title="Live Events",{name} --- (ACTIVO)'
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
# Extract m3u8 from PHP stream page
# -------------------------------------------------
async def extract_m3u8(url: str) -> str | None:
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
# Parse channels from homepage
# -------------------------------------------------
async def get_channels() -> list[dict]:
    r = await network.request(BASE_URL, log=log)
    if not r:
        return []

    html = r.text
    channels = []

    pattern = re.compile(
        r'<div class="channel-info">\s*<h2>([^<]+)</h2>.*?'
        r'<div class="channel-buttons">\s*<a href="([^"]+)"',
        re.S | re.I,
    )

    for name, href in pattern.findall(html):
        channels.append(
            {
                "name": name.strip(),
                "url": urljoin(BASE_URL, href),
                "logo": "https://i.postimg.cc/tgrdPjjC/live-icon-streaming.png",
            }
        )

    return channels


# -------------------------------------------------
# Main scraper
# -------------------------------------------------
async def scrape():
    cached = CACHE_FILE.load() or {}
    log.info(f"Loaded {len(cached)} cached channel(s)")

    channels = await get_channels()
    log.info(f"Discovered {len(channels)} channel link(s)")

    now = Time.clean(Time.now()).timestamp()

    for ch in channels:
        if ch["name"] in cached:
            continue

        m3u8 = await extract_m3u8(ch["url"])
        if not m3u8:
            continue  # NOT ACTIVE / NOT STREAMING

        cached[ch["name"]] = {
            "m3u8": m3u8,
            "logo": ch["logo"],
            "timestamp": now,
        }

        log.info(f"✔ Active: {ch['name']}")

    CACHE_FILE.write(cached)

    playlist = build_playlist(cached)
    OUTPUT_FILE.write_text(playlist, encoding="utf-8")

    log.info(f"✅ Wrote {len(cached)} entries to str_tivimate.m3u8")


# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
