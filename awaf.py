import asyncio
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote, quote_plus, urljoin

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "FAWA"
BASE_URL = "http://www.fawanews.sc/"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)
OUTPUT_FILE = Path("awaf.m3u")

# Encoded User-Agent for TiViMate pipe
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)
UA_ENC = quote_plus(UA)


# -------------------------------------------------
# Build the final playlist text
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
            f'|referer={BASE_URL}'
            f'|origin={BASE_URL}'
            f'|user-agent={UA_ENC}'
        )
        chno += 1

    return "\n".join(lines) + "\n"


# -------------------------------------------------
# Extract .m3u8 from an event page response
# -------------------------------------------------
async def process_event(url: str, url_num: int) -> str | None:
    r = await network.request(url, log=log)
    if not r:
        log.warning(f"URL {url_num}) failed to load event page")
        return None

    # Try to find .m3u8 in the HTML or JS
    match = re.search(r'(https?:\/\/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', r.text, re.IGNORECASE)
    if match:
        stream = match.group(1)
        log.info(f"URL {url_num}) captured M3U8 -> {stream}")
        return stream

    log.warning(f"URL {url_num}) no m3u8 found on page")
    return None


# -------------------------------------------------
# Parse events from main homepage HTML
# -------------------------------------------------
async def get_events(cached_hrefs: set[str]) -> list[dict[str, str]]:
    events = []

    r = await network.request(BASE_URL, log=log)
    if not r:
        return events

    homepage = r.text

    # Each item: <a href="..."><div class="user-item__avatar"><img src="..."></div><div class="user-item__name">...</div><div class="user-item__playing">Sport Time</div></a>
    card_pattern = re.compile(
        r'<a\s+href="([^"]+)"[^>]*>.*?'
        r'<div class="user-item__avatar">\s*<img\s+src="([^"]+)"[^>]*>.*?'
        r'<div class="user-item__name">\s*([^<]+)\s*</div>',
        re.S | re.IGNORECASE,
    )

    for href, img_url, raw_name in card_pattern.findall(homepage):
        href_enc = quote(href)

        if href_enc in cached_hrefs:
            continue

        event_url = urljoin(BASE_URL, href_enc)

        # Clean event name
        event_name = raw_name.strip()

        # sport is text before first space or dash
        sport_guess = event_name.split()[0].strip()

        events.append(
            {
                "sport": sport_guess,
                "event": event_name,
                "link": event_url,
                "href": href_enc,
                "logo": img_url,
            }
        )

    return events


# -------------------------------------------------
# Main scrape function
# -------------------------------------------------
async def scrape() -> None:
    cached = CACHE_FILE.load() or {}
    urls: dict[str, dict] = dict(cached)
    cached_hrefs = {v["href"] for v in urls.values()}

    log.info(f"Loaded {len(urls)} cached events")

    events = await get_events(cached_hrefs)
    log.info(f"Found {len(events)} new event(s)")

    if not events:
        log.info("No new events to process")
        return

    now_ts = Time.clean(Time.now()).timestamp()

    for i, ev in enumerate(events, start=1):
        handler = partial(process_event, ev["link"], i)

        stream = await network.safe_process(
            handler,
            url_num=i,
            semaphore=network.HTTP_S,
            log=log,
        )

        if not stream:
            continue

        title = f"[{ev['sport']}] {ev['event']} ({TAG})"
        tvg_id, _logo_lookup = leagues.get_tvg_info(ev["sport"], ev["event"])

        urls[title] = {
            "url": stream,
            "logo": ev["logo"] or _logo_lookup,
            "base": BASE_URL,
            "timestamp": now_ts,
            "id": tvg_id or "Live.Event.us",
            "href": ev["href"],
        }

    CACHE_FILE.write(urls)

    # Write playlist
    out = build_playlist(urls)
    OUTPUT_FILE.write_text(out, encoding="utf-8")
    log.info(f"Successfully wrote {len(urls)} entries to awaf.m3u")


# -------------------------------------------------
# Run scraper
# -------------------------------------------------
if __name__ == "__main__":
    import asyncio
    asyncio.run(scrape())
