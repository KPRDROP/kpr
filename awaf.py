import asyncio
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote, urljoin, quote_plus

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "FAWA"
OUT_FILE = Path("awaf.m3u")

BASE_URL = "http://www.fawanews.sc/"

CACHE_FILE = Cache("awaf.json", exp=10_800)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)
UA_ENC = quote_plus(UA)

urls: dict[str, dict] = {}


# -------------------------------------------------
# Extract m3u8 from FAWA event page (JS-based)
# -------------------------------------------------
async def process_event(url: str, url_num: int) -> str | None:
    r = await network.request(url, log=log)
    if not r:
        log.warning(f"URL {url_num}) failed to load")
        return None

    patterns = [
        r'https?:\/\/[^"\']+\.m3u8[^"\']*',
        r'var\s+\w+\s*=\s*\[\s*"([^"]+\.m3u8[^"]*)"',
    ]

    for p in patterns:
        m = re.search(p, r.text, re.IGNORECASE)
        if m:
            stream = m.group(1) if m.groups() else m.group(0)
            log.info(f"URL {url_num}) captured M3U8")
            return stream

    log.warning(f"URL {url_num}) no M3U8 found")
    return None


# -------------------------------------------------
# Get events from FAWA homepage (regex based)
# -------------------------------------------------
async def get_events(cached_hrefs: set[str]) -> list[dict]:
    r = await network.request(BASE_URL, log=log)
    if not r:
        return []

    html = r.text

    events = []

    card_re = re.compile(
        r'<a[^>]+href="([^"]+)"[^>]*>.*?'
        r'<div class="user-item__name">([^<]+)</div>.*?'
        r'<div class="user-item__playing">([^<]+)</div>',
        re.S | re.I,
    )

    time_re = re.compile(r"\d{1,2}:\d{2}")

    for href, name, meta in card_re.findall(html):
        href = quote(href)

        if href in cached_hrefs:
            continue

        if not time_re.search(meta):
            continue

        sport = meta.split(time_re.search(meta).group())[0].strip()

        events.append(
            {
                "sport": sport,
                "event": name.strip(),
                "link": urljoin(BASE_URL, href),
                "href": href,
            }
        )

    return events


# -------------------------------------------------
# Write TiViMate playlist
# -------------------------------------------------
def write_playlist(data: dict[str, dict]) -> None:
    lines = ["#EXTM3U"]

    for name, e in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-id="{e["id"]}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )

        lines.append(
            f'{e["url"]}'
            f'|referer={BASE_URL}'
            f'|origin={BASE_URL}'
            f'|user-agent={UA_ENC}'
        )

    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Wrote {len(data)} entries to awaf.m3u")


# -------------------------------------------------
# Main scrape
# -------------------------------------------------
async def scrape() -> None:
    cached = CACHE_FILE.load()
    urls.update(cached)

    cached_hrefs = {v["href"] for v in cached.values()}

    log.info(f"Loaded {len(cached)} cached events")
    log.info(f'Scraping "{BASE_URL}"')

    events = await get_events(cached_hrefs)
    log.info(f"Processing {len(events)} new events")

    now = Time.clean(Time.now()).timestamp()

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

        key = f'[{ev["sport"]}] {ev["event"]} ({TAG})'

        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        urls[key] = {
            "url": stream,
            "logo": logo,
            "base": BASE_URL,
            "timestamp": now,
            "id": tvg_id or "Live.Event.us",
            "href": ev["href"],
        }

    CACHE_FILE.write(urls)
    write_playlist(urls)


# -------------------------------------------------
# Entrypoint
# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
