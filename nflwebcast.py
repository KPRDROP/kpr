import asyncio
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
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
    return " ".join(original_name.strip().split()).title()

async def verify_stream_url(session: aiohttp.ClientSession, url: str, headers: Optional[Dict[str, str]] = None) -> bool:
    request_headers = headers or {"User-Agent": USER_AGENT}
    try:
        async with session.get(url, timeout=10, allow_redirects=True, headers=request_headers) as response:
            if response.status == 200:
                print(f" ✔️ URL Verified: {url}")
                return True
            else:
                print(f" ❌ URL Failed ({response.status}): {url}")
                return False
    except Exception as e:
        print(f" ❌ URL Error ({type(e).__name__}): {url}")
        return False

async def find_stream_from_servers_on_page(context: BrowserContext, page_url: str, base_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    verification_headers = {"Origin": base_url.rstrip('/'), "Referer": base_url}
    page = await context.new_page()
    candidate_urls: List[str] = []

    def handle_request(request):
        if STREAM_PATTERN.search(request.url) and request.url not in candidate_urls:
            candidate_urls.append(request.url)
            print(f" ✅ Captured potential stream: {request.url}")

    page.on("request", handle_request)

    try:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state('networkidle', timeout=DYNAMIC_WAIT_TIMEOUT)

        for stream_url in reversed(candidate_urls):
            if await verify_stream_url(session, stream_url, headers=verification_headers):
                return stream_url

        # Optional: handle iframes or server links
        iframe_locator = page.locator("iframe").first
        if await iframe_locator.count():
            frame_content = await iframe_locator.content_frame()
            if frame_content:
                candidate_iframe_urls: List[str] = []
                def handle_iframe_request(request):
                    if STREAM_PATTERN.search(request.url) and request.url not in candidate_iframe_urls:
                        candidate_iframe_urls.append(request.url)
                        print(f" ✅ Captured iframe stream: {request.url}")
                frame_content.on("request", handle_iframe_request)
                await asyncio.sleep(3)  # allow iframe to load streams
                for url in reversed(candidate_iframe_urls):
                    if await verify_stream_url(session, url, headers=verification_headers):
                        return url

    except Exception as e:
        print(f" ❌ Error processing page {page_url}: {e}")
    finally:
        if not page.is_closed():
            page.remove_listener("request", handle_request)
        await page.close()

    return None

async def scrape_nfl() -> List[Dict]:
    streams = []
    async with async_playwright() as p, aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        try:
            await page.goto(NFL_BASE_URL, wait_until="domcontentloaded", timeout=60000)

            selectors = [
                "tr.single_match_date, .match-row.clearfix",
                "div.game-row, div.match-row",
                "h1.gametitle a"
            ]
            event_rows = None
            for sel in selectors:
                try:
                    await page.wait_for_selector(sel, timeout=GAME_TABLE_WAIT_TIMEOUT)
                    event_rows = page.locator(sel)
                    print(f"✅ Using selector: {sel}")
                    break
                except Exception:
                    continue

            if not event_rows:
                print("⚠️ Could not find any event rows.")
                return []

            count = await event_rows.count()
            print(f"Found {count} event rows.")

            for i in range(count):
                row = event_rows.nth(i)
                try:
                    link = row.locator("td.teamvs a, span.tm").first
                    href = await link.get_attribute("href")
                    name = (await link.inner_text()).strip() if link else "Unknown Game"
                    full_url = urljoin(NFL_BASE_URL, href) if href else None
                    if full_url:
                        stream_url = await find_stream_from_servers_on_page(context, full_url, NFL_BASE_URL, session)
                        if stream_url:
                            streams.append({
                                "name": normalize_game_name(name),
                                "url": stream_url,
                                "tvg_id": "NFL.Dummy.us",
                                "tvg_logo": "http://drewlive24.duckdns.org:9000/Logos/Maxx.png",
                                "group": "NFLWebcast - Live Games",
                                "ref": NFL_BASE_URL
                            })
                except Exception as e:
                    print(f" ⚠️ Error processing row {i}: {e}")
                    continue

            for url in NFL_CHANNEL_URLS:
                slug = url.strip("/").split("/")[-1]
                stream_url = await find_stream_from_servers_on_page(context, url, NFL_BASE_URL, session)
                if stream_url:
                    streams.append({
                        "name": CHANNEL_METADATA.get(slug, {}).get("name", normalize_game_name(slug)),
                        "url": stream_url,
                        "tvg_id": CHANNEL_METADATA.get(slug, {}).get("id", "NFL.Dummy.us"),
                        "tvg_logo": CHANNEL_METADATA.get(slug, {}).get("logo", "http://drewlive24.duckdns.org:9000/Logos/Maxx.png"),
                        "group": "NFLWebcast - 24/7 Channels",
                        "ref": NFL_BASE_URL
                    })

        except Exception as e:
            print(f"❌ Error scraping NFL page: {e}")
        finally:
            await browser.close()
    return streams

def write_playlist(streams: List[Dict], filename: str):
    if not streams:
        print("⏹️ No streams found.")
        return
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for entry in streams:
            f.write(f'#EXTINF:-1 tvg-id="{entry["tvg_id"]}" tvg-name="{entry["name"]}" tvg-logo="{entry["tvg_logo"]}" group-title="{entry["group"]}",{entry["name"]}\n')
            f.write(f'#EXTVLCOPT:http-origin={entry["ref"]}\n')
            f.write(f'#EXTVLCOPT:http-referrer={entry["ref"]}\n')
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(entry["url"] + "\n")
    print(f"✅ Playlist saved to {filename} ({len(streams)} streams).")

async def main():
    streams = await scrape_nfl()
    write_playlist(streams, OUTPUT_FILE)

if __name__ == "__main__":
    asyncio.run(main())
