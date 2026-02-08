#!/usr/bin/env python3
import asyncio
import base64
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.async_api import async_playwright
from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
TAG = "STRMBTW"

BASE_URLS = [
    "https://hiteasport.info",
    "https://streambtw.com",
]

REFERER = "https://hiteasport.info/"
ORIGIN = "https://hiteasport.info"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
UA_ENC = quote(USER_AGENT)

OUT_VLC = Path("Streambtw_VLC.m3u8")
OUT_TIVI = Path("Streambtw_TiviMate.m3u8")

CACHE_FILE = Cache(TAG, exp=3600)
urls: dict[str, dict] = {}

M3U8_RE = re.compile(r'var\s+\w+\s*=\s*"([^"]+)"', re.I)

# --------------------------------------------------
async def collect_event_links() -> list[dict[str, str]]:
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        for base in BASE_URLS:
            log.info(f'Playwright scraping "{base}"')
            await page.goto(base, timeout=60000)
            await page.wait_for_timeout(4000)

            links = await page.eval_on_selector_all(
                "a",
                """els => els
                    .map(e => e.href)
                    .filter(h => h && h.includes("watch"))"""
            )

            for link in set(links):
                events.append({
                    "sport": "Live",
                    "event": link.split("/")[-1][:60],
                    "link": link,
                    "base": base,
                })

            if events:
                break

        await browser.close()

    log.info(f"Collected {len(events)} event link(s)")
    return events

# --------------------------------------------------
async def process_event(url: str, url_num: int) -> str | None:
    html = await network.request(url, log=log)
    if not html:
        return None

    m = M3U8_RE.search(html.text)
    if not m:
        return None

    stream = m.group(1)
    if not stream.startswith("http"):
        stream = base64.b64decode(stream).decode("utf-8")

    log.info(f"URL {url_num}) Captured M3U8")
    return stream

# --------------------------------------------------
def build_playlists(data: dict[str, dict]):
    vlc = ["#EXTM3U"]
    tiv = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        vlc.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}",{name}'
        )
        vlc.append(f"#EXTVLCOPT:http-referrer={REFERER}")
        vlc.append(f"#EXTVLCOPT:http-origin={ORIGIN}")
        vlc.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc.append(e["url"])

        tiv.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}",{name}'
        )
        tiv.append(
            f'{e["url"]}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}'
        )

        ch += 1

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tiv), encoding="utf-8")
    log.info("Playlists written")

# --------------------------------------------------
async def scrape():
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} cached event(s)")
    else:
        events = await collect_event_links()
        now = Time.clean(Time.now())

        for i, ev in enumerate(events, 1):
            handler = partial(process_event, ev["link"], i)
            url = await network.safe_process(
                handler, i, network.HTTP_S, log
            )
            if not url:
                continue

            key = f"{ev['event']} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info("Live", ev["event"])

            urls[key] = {
                "url": url,
                "logo": logo,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
            }

        CACHE_FILE.write(urls)

    build_playlists(urls)

# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
