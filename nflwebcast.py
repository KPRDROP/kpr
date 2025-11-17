import asyncio
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
DYNAMIC_WAIT_TIMEOUT = 15000
GAME_TABLE_WAIT_TIMEOUT = 30000
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
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/nfl-network-hz-us.png?raw=true"
    },
    "nflredzone": {
        "name": "NFL RedZone",
        "id": "NFL.RedZone.HD.us2",
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/nfl-red-zone-hz-us.png?raw=true"
    },
    "espnusa": {
        "name": "ESPN",
        "id": "ESPN.HD.us2",
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/espn-us.png?raw=true"
    },
}

def normalize_game_name(original_name: str) -> str:
    cleaned_name = " ".join(original_name.splitlines()).strip()
    if "@" in cleaned_name:
        parts = cleaned_name.split("@")
        if len(parts) == 2:
            return f"{parts[0].strip().title()} @ {parts[1].strip().title()}"
    return " ".join(cleaned_name.strip().split()).title()

async def verify_stream_url(session: aiohttp.ClientSession, url: str, headers: Optional[Dict[str, str]] = None) -> bool:
    request_headers = headers or {}
    if "User-Agent" not in request_headers:
        request_headers["User-Agent"] = session.headers.get("User-Agent", USER_AGENT)
    try:
        async with session.get(url, timeout=10, allow_redirects=True, headers=request_headers) as response:
            return response.status == 200
    except Exception:
        return False

async def find_stream_from_page(context: BrowserContext, page_url: str, base_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    candidate_urls: List[str] = []
    page = await context.new_page()

    def handle_request(request):
        if STREAM_PATTERN.search(request.url) and request.url not in candidate_urls:
            candidate_urls.append(request.url)

    page.on("request", handle_request)

    try:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=DYNAMIC_WAIT_TIMEOUT)

        # Return first valid m3u8 found
        for url in reversed(candidate_urls):
            if await verify_stream_url(session, url, headers={"Origin": base_url, "Referer": base_url}):
                return url
    except Exception:
        return None
    finally:
        if not page.is_closed():
            page.remove_listener("request", handle_request)
        await page.close()
    return None

async def scrape_nfl() -> List[Dict]:
    results: List[Dict] = []
    async with async_playwright() as p, aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        try:
            # Scrape main games from NFL base URL
            page = await context.new_page()
            await page.goto(NFL_BASE_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector("tr.singele_match_date, .match-row.clearfix", timeout=GAME_TABLE_WAIT_TIMEOUT)
            event_rows = page.locator("tr.singele_match_date, .match-row.clearfix")
            count = await event_rows.count()

            for i in range(count):
                row = event_rows.nth(i)
                link = row.locator("td.teamvs a")
                if await link.count() == 0:
                    continue
                name = await link.inner_text()
                href = await link.get_attribute("href")
                if not href:
                    continue
                full_url = urljoin(NFL_BASE_URL, href)
                logo = None
                logo_loc = row.locator("td.teamlogo img")
                if await logo_loc.count() > 0:
                    logo = await logo_loc.nth(0).get_attribute("src")
                stream_url = await find_stream_from_page(context, full_url, NFL_BASE_URL, session)
                if stream_url:
                    results.append({
                        "name": normalize_game_name(name),
                        "url": stream_url,
                        "tvg_id": "NFL.Dummy.us",
                        "tvg_logo": logo or "",
                        "group": "NFLWebcast - Live Games",
                        "ref": NFL_BASE_URL
                    })

            # Scrape static 24/7 channels
            for url in NFL_CHANNEL_URLS:
                slug = url.strip("/").split("/")[-1]
                stream_url = await find_stream_from_page(context, url, NFL_BASE_URL, session)
                if stream_url:
                    meta = CHANNEL_METADATA.get(slug, {})
                    results.append({
                        "name": meta.get("name", slug.title()),
                        "url": stream_url,
                        "tvg_id": meta.get("id", "NFL.Dummy.us"),
                        "tvg_logo": meta.get("logo", ""),
                        "group": "NFLWebcast - 24/7 Channels",
                        "ref": NFL_BASE_URL
                    })

            await page.close()
        finally:
            await browser.close()
    return results

def write_playlist(streams: List[Dict], filename: str):
    if not streams:
        print("⏹️ No streams found.")
        return
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            f.write(f'#EXTINF:-1 tvg-id="{s["tvg_id"]}" tvg-name="{s["name"]}" tvg-logo="{s["tvg_logo"]}" group-title="{s["group"]}",{s["name"]}\n')
            f.write(f"#EXTVLCOPT:http-origin={s['ref']}\n")
            f.write(f"#EXTVLCOPT:http-referrer={s['ref']}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(s["url"] + "\n")
    print(f"✅ Playlist saved to {filename} ({len(streams)} streams).")

async def main():
    print("🚀 Starting NFL Webcast Scraper...")
    streams = await scrape_nfl()
    write_playlist(streams, OUTPUT_FILE)

if __name__ == "__main__":
    asyncio.run(main())
