import asyncio
import re
import random
from typing import Dict, List, Optional, Callable, Awaitable
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
)

MAX_RETRIES = 5
BASE_DELAY = 1
DYNAMIC_WAIT_TIMEOUT = 15000
GAME_TABLE_WAIT_TIMEOUT = 15000

STREAM_PATTERN = re.compile(r"\.m3u8($|\?)", re.IGNORECASE)

OUTPUT_FILE = "NFLWebcast.m3u8"

NFL_BASE_URL = "https://nflwebcast.com/"

NFL_CHANNEL_URLS = [
    "http://nflwebcast.com/nflnetwork/",
    "https://nflwebcast.com/nflredzone/",
    "https://nflwebcast.com/espnusa/",
]

CHANNEL_METADATA = {
    "nflnetwork": {
        "name": "NFL Network",
        "id": "NFL.Network.HD.us2",
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/nfl-network-hz-us.png?raw=true",
    },
    "nflredzone": {
        "name": "NFL RedZone",
        "id": "NFL.RedZone.HD.us2",
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/nfl-red-zone-hz-us.png?raw=true",
    },
    "espnusa": {
        "name": "ESPN USA",
        "id": "ESPN.USA.HD.us2",
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/espn-us.png?raw=true",
    },
}


# -----------------------------------------------------------
#  RETRY WRAPPER
# -----------------------------------------------------------
async def retry_async(label: str, func: Callable[[], Awaitable], retries=MAX_RETRIES):
    for i in range(retries):
        try:
            return await func()
        except Exception as e:
            if i == retries - 1:
                print(f"❌ {label} FAILED after {retries} attempts: {e}")
                return None

            delay = BASE_DELAY * (2 ** i) + random.uniform(0, 0.5)
            print(f"⚠️ {label} failed (attempt {i+1}/{retries}): {e}, retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)


# -----------------------------------------------------------
#  UTILITY
# -----------------------------------------------------------
def normalize_game_name(original_name: str) -> str:
    return " ".join(original_name.strip().split()).title()


# -----------------------------------------------------------
#  STREAM VERIFICATION
# -----------------------------------------------------------
async def verify_stream_url(
    session: aiohttp.ClientSession, url: str, headers: Optional[Dict[str, str]] = None
) -> bool:
    request_headers = headers or {"User-Agent": USER_AGENT}

    async def _req():
        async with session.get(
            url, timeout=10, allow_redirects=True, headers=request_headers
        ) as response:
            return response.status == 200

    result = await retry_async(f"Verify stream URL {url}", _req, retries=4)

    if result:
        print(f" ✔️ Verified stream: {url}")
    else:
        print(f" ❌ Verification failed: {url}")
    return bool(result)


# -----------------------------------------------------------
#  STREAM EXTRACTION FROM PAGE
# -----------------------------------------------------------
async def find_stream_from_page(
    context: BrowserContext, page_url: str, session: aiohttp.ClientSession
) -> Optional[str]:

    page: Page = await context.new_page()

    candidate_streams = []

    def on_request(req):
        if STREAM_PATTERN.search(req.url):
            candidate_streams.append(req.url)
            print(f"🌐 Detected stream: {req.url}")

    page.on("request", on_request)

    async def do_goto():
        await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)

    # Retry navigation
    await retry_async(f"Navigate to {page_url}", do_goto)

    # Wait for JS to load streams
    await asyncio.sleep(3)

    # Test captured streams
    for url in reversed(candidate_streams):
        if await verify_stream_url(session, url):
            await page.close()
            return url

    # Try iframe
    iframes = page.frames
    for frame in iframes:
        try:
            for req in frame._requests.values():
                if STREAM_PATTERN.search(req.url):
                    if await verify_stream_url(session, req.url):
                        await page.close()
                        return req.url
        except:
            pass

    await page.close()
    return None


# -----------------------------------------------------------
#  MAIN SCRAPER
# -----------------------------------------------------------
async def scrape_nfl():
    all_streams = []

    async with async_playwright() as p, aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT}
    ) as session:

        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)

        page = await context.new_page()

        # Retry homepage
        await retry_async(
            "Open NFLWebcast homepage",
            lambda: page.goto(NFL_BASE_URL, wait_until="domcontentloaded", timeout=60000),
        )

        selectors = [
            "tr.single_match_date, .match-row.clearfix",
            "div.match-row",
            ".game-row",
            "table tbody tr",
        ]

        event_rows = None
        for sel in selectors:
            print(f"Trying selector: {sel}")
            ok = await retry_async(
                f"Wait for selector: {sel}",
                lambda s=sel: page.wait_for_selector(s, timeout=GAME_TABLE_WAIT_TIMEOUT),
            )
            if ok:
                event_rows = page.locator(sel)
                break

        if not event_rows:
            print("⚠️ No game rows found")
        else:
            count = await event_rows.count()
            print(f"📌 Found {count} rows")

            for i in range(count):
                try:
                    row = event_rows.nth(i)
                    link = row.locator("a").first
                    href = await link.get_attribute("href")
                    name = normalize_game_name(await link.inner_text())

                    full_url = urljoin(NFL_BASE_URL, href)

                    stream_url = await find_stream_from_page(context, full_url, session)
                    if stream_url:
                        all_streams.append(
                            {
                                "name": name,
                                "url": stream_url,
                                "tvg_id": "NFL.Webcast.Game",
                                "tvg_logo": "https://i.imgur.com/NFLlogo.png",
                                "group": "NFLWebcast - Live Games",
                                "ref": NFL_BASE_URL,
                            }
                        )
                except Exception as e:
                    print(f"⚠️ Error parsing row {i}: {e}")

        # STATIC CHANNELS
        for url in NFL_CHANNEL_URLS:
            slug = url.strip("/").split("/")[-1]
            meta = CHANNEL_METADATA.get(slug, {})

            print(f"📺 Scraping channel: {meta.get('name', slug)}")

            stream_url = await find_stream_from_page(context, url, session)
            if stream_url:
                all_streams.append(
                    {
                        "name": meta.get("name", slug),
                        "url": stream_url,
                        "tvg_id": meta.get("id", slug),
                        "tvg_logo": meta.get("logo", ""),
                        "group": "NFLWebcast - 24/7 Channels",
                        "ref": NFL_BASE_URL,
                    }
                )

        await browser.close()
        return all_streams


# -----------------------------------------------------------
#  WRITE M3U
# -----------------------------------------------------------
def write_playlist(streams: List[Dict], filename: str):
    if not streams:
        print("⚠️ No streams found, skipping file write.")
        return

    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            f.write(
                f'#EXTINF:-1 tvg-id="{s["tvg_id"]}" tvg-logo="{s["tvg_logo"]}" '
                f'group-title="{s["group"]}",{s["name"]}\n'
            )
            f.write(f'#EXTVLCOPT:http-origin={s["ref"]}\n')
            f.write(f'#EXTVLCOPT:http-referrer={s["ref"]}\n')
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(s["url"] + "\n")

    print(f"✅ Saved {filename} ({len(streams)} streams)")


# -----------------------------------------------------------
#  ENTRY POINT
# -----------------------------------------------------------
async def main():
    streams = await scrape_nfl()
    write_playlist(streams, OUTPUT_FILE)


if __name__ == "__main__":
    asyncio.run(main())
