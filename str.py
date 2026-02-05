import asyncio
import re
from pathlib import Path
from urllib.parse import urljoin, quote_plus

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

STREAM_PAGE_RE = re.compile(r'global\d+\.php\?stream=', re.I)
M3U8_RE = re.compile(r'(https?:\/\/[^\s"\']+\.m3u8[^\s"\']*)', re.I)

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
async def extract_m3u8(page_url: str) -> str | None:
    r = await network.request(page_url, log=log)
    if not r:
        return None

    match = M3U8_RE.search(r.text)
    if match:
        log.info(f"🎯 M3U8 found -> {match.group(1)}")
        return match.group(1)

    log.warning("❌ No m3u8 found on page")
    return None


# -------------------------------------------------
async def discover_channels() -> list[str]:
    r = await network.request(BASE_URL, log=log)
    if not r:
        return []

    html = r.text
    found = set()

    for href in re.findall(r'href="([^"]+)"', html):
        if "${" in href:
            continue
        if not STREAM_PAGE_RE.search(href):
            continue

        found.add(urljoin(BASE_URL, href))

    return list(found)


# -------------------------------------------------
async def scrape():
    cached = CACHE_FILE.load() or {}
    log.info(f"Loaded {len(cached)} cached channel(s)")

    channels = await discover_channels()
    log.info(f"Discovered {len(channels)} channel link(s)")

    now = Time.clean(Time.now()).timestamp()

    for i, url in enumerate(channels, start=1):
        name = url.split("stream=")[-1].upper()

        if name in cached:
            continue

        m3u8 = await extract_m3u8(url)
        if not m3u8:
            continue

        cached[name] = {
            "m3u8": m3u8,
            "logo": "https://i.postimg.cc/tgrdPjjC/live-icon-streaming.png",
            "timestamp": now,
        }

        log.info(f"✔ Added channel: {name}")

    CACHE_FILE.write(cached)

    playlist = build_playlist(cached)
    OUTPUT_FILE.write_text(playlist, encoding="utf-8")

    log.info(f"✅ Wrote {len(cached)} entries to str_tivimate.m3u8")


# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
