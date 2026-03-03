import asyncio
import os
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote

from selectolax.parser import HTMLParser
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

TAG = "WEBCAST"

BASE_URL = os.environ.get("WEBTV_MLB_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing WEBTV_MLB_BASE_URL secret")

BASE_URLS = {
    "MLB": BASE_URL
}

REFERER = BASE_URL
ORIGIN = BASE_URL.rstrip("/")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)

UA_ENC = quote(USER_AGENT)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Origin": ORIGIN,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

OUT_VLC = Path("webtv_vlc.m3u8")
OUT_TIVI = Path("webtv_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=19_800)

urls: dict[str, dict[str, str | float]] = {}

# --------------------------------------------------

def fix_event(s: str) -> str:
    return " vs ".join(map(str.strip, s.split("@")))

# --------------------------------------------------

async def safe_request(url: str):
    """
    Adds browser headers and retries once if 403.
    """
    response = await network.request(url, headers=BROWSER_HEADERS, log=log)

    if not response:
        return None

    if response.status_code == 403:
        log.warning("403 detected. Retrying with fresh headers...")
        await asyncio.sleep(1)
        return await network.request(url, headers=BROWSER_HEADERS, log=log)

    return response

# --------------------------------------------------

async def process_event(url: str, url_num: int) -> str | None:

    async with async_playwright() as p:

        browser = await p.firefox.launch(headless=True)

        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={
                "Referer": BASE_URL,
                "Origin": BASE_URL.rstrip("/")
            }
        )

        # Block images / fonts / trackers (faster + less detection)
        await context.route(
            "**/*",
            lambda route, request: (
                route.abort()
                if request.resource_type in ["image", "font"]
                else route.continue_()
            ),
        )

        page = await context.new_page()

        captured = None

        # 🔥 CAPTURE ALL REQUESTS (BETTER THAN requestfinished)
        def handle_request(request):
            nonlocal captured
            if ".m3u8" in request.url and not captured:
                captured = request.url

        context.on("request", handle_request)

        try:

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                pass

            await page.wait_for_timeout(4000)

            # Momentum click (ad bypass)
            for _ in range(2):
                try:
                    await page.mouse.click(500, 350)
                    await asyncio.sleep(1)
                except Exception:
                    pass

            # Click inside iframes
            for frame in page.frames:
                try:
                    await frame.click("body", timeout=2000)
                    await asyncio.sleep(1)
                except Exception:
                    pass

            # Wait for stream
            waited = 0
            while waited < 20 and not captured:
                await asyncio.sleep(1)
                waited += 1

            # Fallback HTML scan
            if not captured:
                html = await page.content()
                m = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
                if m:
                    captured = m.group(0)

            # Base64 fallback
            if not captured:
                html = await page.content()
                blobs = re.findall(r'["\']([A-Za-z0-9+/=]{40,200})["\']', html)
                for b in blobs:
                    try:
                        import base64
                        dec = base64.b64decode(b).decode("utf-8", "ignore")
                        if ".m3u8" in dec:
                            captured = dec.strip()
                            break
                    except Exception:
                        pass

        finally:
            context.remove_listener("request", handle_request)
            await page.close()
            await context.close()
            await browser.close()

        if captured:
            log.info(f"URL {url_num}) Captured M3U8 via browser")
        else:
            log.warning(f"URL {url_num}) Failed to capture stream")

        return captured

# --------------------------------------------------

def build_playlists(data: dict[str, dict]):

    vlc = ["#EXTM3U"]
    tivimate = ["#EXTM3U"]

    channel_number = 200

    for name, e in data.items():

        if not e.get("url"):
            continue

        channel_number += 1

        vlc.extend([
            f'#EXTINF:-1 tvg-chno="{channel_number}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={REFERER}",
            f"#EXTVLCOPT:http-origin={ORIGIN}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        tivimate.extend([
            f'#EXTINF:-1 tvg-chno="{channel_number}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}',
            f'{e["url"]}|referer={REFERER}/|origin={ORIGIN}|user-agent={UA_ENC}',
        ])

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tivimate), encoding="utf-8")

    log.info("Playlists written successfully")

# --------------------------------------------------

async def scrape() -> None:

    cached_urls = CACHE_FILE.load() or {}

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}
    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(list(cached_urls.keys())):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):

            handler = partial(
                process_event,
                url=(link := ev["link"]),
                url_num=i,
            )

            stream_url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.PW_S,
                log=log,
            )

            sport, event = ev["sport"], ev["event"]
            key = f"[{sport}] {event} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            entry = {
                "url": stream_url,
                "logo": logo,
                "base": BASE_URL,
                "timestamp": now.timestamp(),
                "id": tvg_id or "MLB.Baseball.Dummy.us",
                "link": link,
            }

            cached_urls[key] = entry

            if stream_url:
                valid_count += 1
                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
    build_playlists(cached_urls)

# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
