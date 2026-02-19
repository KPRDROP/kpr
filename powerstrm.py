import asyncio
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote_plus, urljoin

from utils import Cache, Time, get_logger, network

log = get_logger(__name__)

TAG = "POWERSTRM"
BASE_URL = "https://powerstreams.online/"
REFERER = "https://streams.center/"
ORIGIN = "https://streams.center"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)
OUTPUT_FILE = Path("powerstrm.m3u8")

# -------------------------------------------------
# Encoded TiViMate User-Agent
# -------------------------------------------------

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)
UA_ENC = quote_plus(UA)

# -------------------------------------------------
# Category → tvg-id mapping
# -------------------------------------------------

TVG_MAP = {
    "Football": "Soccer.Dummy.us",
    "Basketball": "NBA.Basketball.Dummy.us",
    "Hockey": "NHL.Hockey.Dummy.us",
    "Other Sports": "Sports.Dummy.us",
}

# -------------------------------------------------
# Build TiViMate playlist
# -------------------------------------------------

def build_playlist(data: dict[str, dict]) -> str:
    lines = ["#EXTM3U"]
    chno = 1

    for title, info in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{info["id"]}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{info["logo"]}" '
            f'group-title="Live Events",{title}'
        )
        lines.append(
            f'{info["url"]}'
            f'|Referer={REFERER}'
            f'|Origin={ORIGIN}'
            f'|User-Agent={UA_ENC}'
        )
        chno += 1

    return "\n".join(lines) + "\n"


# -------------------------------------------------
# Extract .m3u8 from event page
# -------------------------------------------------

async def process_event(url: str, url_num: int) -> str | None:
    r = await network.request(url, log=log)
    if not r:
        log.warning(f"URL {url_num}) failed loading event page")
        return None

    match = re.search(
        r'(https?:\/\/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r.text,
        re.IGNORECASE,
    )

    if match:
        stream = match.group(1)
        log.info(f"URL {url_num}) captured M3U8")
        return stream

    log.warning(f"URL {url_num}) no m3u8 found")
    return None


# -------------------------------------------------
# Parse homepage for categories + matches
# -------------------------------------------------

async def get_events() -> list[dict]:
    events = []

    r = await network.request(BASE_URL, log=log)
    if not r:
        return events

    html = r.text

    # Split by category blocks
    category_blocks = re.split(
        r'<div class="category-title">(.*?)</div>',
        html,
        flags=re.IGNORECASE,
    )

    # Structure:
    # [before, cat1, content1, cat2, content2, ...]
    for i in range(1, len(category_blocks), 2):
        category = category_blocks[i].strip()
        block_html = category_blocks[i + 1]

        if category not in TVG_MAP:
            continue

        # Find match cards inside this category block
        for card in re.finditer(
            r'<div class="match-card">.*?<a href="([^"]+)".*?>.*?'
            r'<div class="team-name">\s*([^<]+)\s*</div>.*?'
            r'<div class="vs">VS</div>.*?'
            r'<div class="team-name">\s*([^<]+)\s*</div>.*?'
            r'<div class="match-date">\s*([^<]+)\s*</div>',
            block_html,
            re.S | re.IGNORECASE,
        ):
            href, team1, team2, date_raw = card.groups()

            event_url = urljoin(BASE_URL, href)

            # Clean date: remove comma
            date_clean = date_raw.replace(",", "").strip()

            title = (
                f"[{category}] {team1.strip()} at "
                f"{team2.strip()} ({TAG})"
            )

            events.append(
                {
                    "title": title,
                    "url": event_url,
                    "category": category,
                    "date": date_clean,
                }
            )

    return events


# -------------------------------------------------
# Main scrape
# -------------------------------------------------

async def scrape() -> None:
    cached = CACHE_FILE.load() or {}
    urls: dict[str, dict] = dict(cached)

    log.info(f"Loaded {len(urls)} cached events")

    events = await get_events()
    log.info(f"Found {len(events)} event(s)")

    if not events:
        log.info("No events found")
        return

    now_ts = Time.clean(Time.now()).timestamp()

    for i, ev in enumerate(events, start=1):
        handler = partial(process_event, ev["url"], i)

        stream = await network.safe_process(
            handler,
            url_num=i,
            semaphore=network.HTTP_S,
            log=log,
        )

        if not stream:
            continue

        urls[ev["title"]] = {
            "url": stream,
            "logo": "",
            "timestamp": now_ts,
            "id": TVG_MAP.get(ev["category"], "Live.Event.us"),
        }

    CACHE_FILE.write(urls)

    playlist = build_playlist(urls)
    OUTPUT_FILE.write_text(playlist, encoding="utf-8")

    log.info(f"Successfully wrote {len(urls)} entries to powerstrm.m3u8")


# -------------------------------------------------
# Run
# -------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
