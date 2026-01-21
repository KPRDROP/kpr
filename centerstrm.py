import asyncio
import re
from functools import partial
from pathlib import Path
from urllib.parse import urljoin, quote_plus

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "STRMCNTR"
BASE_SITE = "https://streamcenter.live"
BASE_ORIGIN = "https://streamcenter.xyz"

OUT_FILE = Path("centerstrm.m3u")

CACHE_FILE = Cache("centerstrm.json", exp=10_800)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)
UA_ENC = quote_plus(UA)

urls: dict[str, dict] = {}


# -------------------------------------------------
# Extract frontend events
# -------------------------------------------------
async def get_events(cached_keys: set[str]) -> list[dict]:
    r = await network.request(BASE_SITE, log=log)
    if not r:
        return []

    html = r.text
    events = []

    card_re = re.compile(
        r'<a[^>]+href="(/stream/\d+)"[^>]*>.*?'
        r'<h3[^>]*>([^<]+)</h3>.*?'
        r'<p[^>]*>([^|]+)\|([^<]+)</p>.*?'
        r'<img[^>]+src="([^"]+)"',
        re.S | re.I,
    )

    for href, name, sport, _, logo in card_re.findall(html):
        key = f"[{sport.strip()}] {name.strip()} ({TAG})"
        if key in cached_keys:
            continue

        events.append(
            {
                "sport": sport.strip(),
                "event": name.strip(),
                "url": urljoin(BASE_SITE, href),
                "logo": logo,
            }
        )

    return events


# -------------------------------------------------
# Write playlist
# -------------------------------------------------
def write_playlist(data: dict) -> None:
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
            f'|referer={BASE_ORIGIN}/'
            f'|origin={BASE_ORIGIN}'
            f'|user-agent={UA_ENC}'
        )
        ch += 1

    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Wrote {len(data)} entries to centerstrm.m3u")


# -------------------------------------------------
# Main scraper
# -------------------------------------------------
async def scrape() -> None:
    cached = CACHE_FILE.load()
    urls.update(cached)

    log.info(f"Loaded {len(cached)} cached events")

    events = await get_events(set(cached.keys()))
    log.info(f"Found {len(events)} frontend events")

    if not events:
        write_playlist(urls)
        return

    async with async_playwright() as p:
        browser, context = await network.browser(p, browser="external")

        try:
            for i, ev in enumerate(events, 1):
                handler = partial(
                    network.process_event,
                    url=ev["url"],
                    url_num=i,
                    context=context,
                    timeout=20,
                    log=log,
                )

                stream = await network.safe_process(
                    handler,
                    url_num=i,
                    semaphore=network.PW_S,
                    log=log,
                )

                if not stream:
                    continue

                key = f"[{ev['sport']}] {ev['event']} ({TAG})"
                tvg_id, _ = leagues.get_tvg_info(ev["sport"], ev["event"])

                urls[key] = {
                    "url": stream,
                    "logo": ev["logo"],
                    "base": BASE_ORIGIN,
                    "timestamp": Time.now().timestamp(),
                    "id": tvg_id or "Live.Event.us",
                }

        finally:
            await browser.close()

    CACHE_FILE.write(urls)
    write_playlist(urls)


# -------------------------------------------------
# Entrypoint
# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
