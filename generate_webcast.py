import asyncio
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, quote
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright

# User agent and settings
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")  # for TiviMate
DYNAMIC_WAIT_TIMEOUT = 15000
GAME_TABLE_WAIT_TIMEOUT = 30000
STREAM_PATTERN = re.compile(r"\.m3u8($|\?)", re.IGNORECASE)

# Output files
OUTPUT_FILE_VLC = "SportsWebcast_VLC.m3u8"
OUTPUT_FILE_TIVIMATE = "SportsWebcast_TiviMate.m3u8"

# Base URLs
NFL_BASE_URL = "https://nflwebcast.com/"
NHL_BASE_URL = "https://slapstreams.com/"
MLB_BASE_URL = "https://mlbwebcast.com/"
MLS_BASE_URL = "https://mlswebcast.com/"
NBA_BASE_URL = "https://nbawebcast.top/"

# Channel URLs
NFL_CHANNEL_URLS = [
    "http://nflwebcast.com/nflnetwork/",
    "https://nflwebcast.com/nflredzone/",
    "https://nflwebcast.com/espnusa/",
]
MLB_CHANNEL_URLS = []
NHL_CHANNEL_URLS = []
MLS_CHANNEL_URLS = []

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

NBA_STREAM_URL_PATTERN = "https://gg.poocloud.in/{stream_key}/tracks-v1a1/mono.ts.m3u8"
NBA_CUSTOM_HEADERS = {
    "origin": "https.embednow.top",
    "referrer": "https.embednow.top/",
    "user_agent": USER_AGENT,
}

def normalize_game_name(original_name: str) -> str:
    cleaned = " ".join(original_name.splitlines()).strip()
    if "@" in cleaned:
        parts = cleaned.split("@")
        if len(parts) == 2:
            team1 = parts[0].strip().title()
            team2 = parts[1].strip().title()
            return f"{team1} @ {team2}"
    return cleaned.title()

async def verify_stream_url(session: aiohttp.ClientSession, url: str, headers: Optional[Dict[str, str]] = None) -> bool:
    request_headers = headers or {"User-Agent": USER_AGENT}
    try:
        async with session.get(url, timeout=10, headers=request_headers) as resp:
            return resp.status == 200
    except Exception:
        return False

async def find_stream_from_servers_on_page(context: BrowserContext, page_url: str, base_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    page = await context.new_page()
    candidate_urls: List[str] = []

    def handle_request(request):
        if STREAM_PATTERN.search(request.url) and request.url not in candidate_urls:
            candidate_urls.append(request.url)

    page.on("request", handle_request)
    try:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=DYNAMIC_WAIT_TIMEOUT)
        for stream_url in reversed(candidate_urls):
            if await verify_stream_url(session, stream_url, {"Referer": base_url, "User-Agent": USER_AGENT}):
                return stream_url
    except Exception:
        pass
    finally:
        page.remove_listener("request", handle_request)
        await page.close()
    return None

async def scrape_league(base_url: str, channel_urls: List[str], group_prefix: str, default_id: str, default_logo: str) -> List[Dict]:
    results = []
    async with async_playwright() as p, aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        try:
            page = await context.new_page()
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
            game_row_selector = "#mtable tr.singele_match_date:not(.mdatetitle), .match-row.clearfix"
            await page.wait_for_selector(game_row_selector, timeout=GAME_TABLE_WAIT_TIMEOUT)
            event_rows = page.locator(game_row_selector)
            count = await event_rows.count()
            print(f"  Found {count} games for {group_prefix}")

            game_links = []
            for i in range(count):
                row = event_rows.nth(i)
                link = row.locator("td.teamvs a")
                href = await link.get_attribute("href")
                if not href:
                    continue
                name = (await link.inner_text()) or "Unknown Game"
                logo_el = row.locator("td.teamlogo img").first
                logo = await logo_el.get_attribute("src") if await logo_el.count() else default_logo
                game_links.append({"name": name.strip(), "url": urljoin(base_url, href), "logo": logo})

            await page.close()
            for game in game_links:
                stream_url = await find_stream_from_servers_on_page(context, game["url"], base_url, session)
                if stream_url:
                    results.append({
                        "name": normalize_game_name(game["name"]),
                        "url": stream_url,
                        "tvg_id": default_id,
                        "tvg_logo": game["logo"],
                        "group": f"{group_prefix} - Live Games",
                        "ref": base_url,
                    })
            for url in channel_urls:
                slug = url.strip("/").split("/")[-1]
                stream_url = await find_stream_from_servers_on_page(context, url, base_url, session)
                if stream_url:
                    meta = CHANNEL_METADATA.get(slug, {})
                    results.append({
                        "name": meta.get("name", slug.title()),
                        "url": stream_url,
                        "tvg_id": meta.get("id", default_id),
                        "tvg_logo": meta.get("logo", default_logo),
                        "group": f"{group_prefix} - 24/7 Channels",
                        "ref": base_url,
                    })
        except Exception as e:
            print(f"❌ Error scraping {group_prefix}: {e}")
        finally:
            await browser.close()
    return results

def write_playlists(streams: List[Dict]):
    if not streams:
        print("⏹️ No streams found.")
        return

    # --- VLC Playlist ---
    with open(OUTPUT_FILE_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            f.write(f'#EXTINF:-1 tvg-id="{s["tvg_id"]}" tvg-name="{s["name"]}" tvg-logo="{s["tvg_logo"]}" group-title="{s["group"]}",{s["name"]}\n')
            f.write(f'#EXTVLCOPT:http-referrer={s["ref"]}\n')
            f.write(f'#EXTVLCOPT:http-origin={s["ref"]}\n')
            f.write(f'#EXTVLCOPT:http-user-agent={USER_AGENT}\n')
            f.write(s["url"] + "\n")

    # --- TiviMate Playlist ---
    with open(OUTPUT_FILE_TIVIMATE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            headers = f"referer={s['ref']}|origin={s['ref']}|user-agent={ENCODED_USER_AGENT}"
            f.write(f'#EXTINF:-1 tvg-id="{s["tvg_id"]}" tvg-name="{s["name"]}" tvg-logo="{s["tvg_logo"]}" group-title="{s["group"]}",{s["name"]}\n')
            f.write(f"{s['url']}|{headers}\n")

    print(f"✅ VLC playlist saved: {OUTPUT_FILE_VLC}")
    print(f"✅ TiviMate playlist saved: {OUTPUT_FILE_TIVIMATE}")

async def main():
    print("🚀 Starting Sports Webcast Scraper...")
    tasks = [
        scrape_league(NFL_BASE_URL, NFL_CHANNEL_URLS, "NFLWebcast", "NFL.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Maxx.png"),
        scrape_league(NHL_BASE_URL, NHL_CHANNEL_URLS, "NHLWebcast", "NHL.Hockey.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Hockey.png"),
        scrape_league(MLB_BASE_URL, MLB_CHANNEL_URLS, "MLBWebcast", "MLB.Baseball.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/MLB.png"),
        scrape_league(MLS_BASE_URL, MLS_CHANNEL_URLS, "MLSWebcast", "MLS.Soccer.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Football2.png"),
    ]
    results = await asyncio.gather(*tasks)
    all_streams = [s for league in results for s in league]
    write_playlists(all_streams)

if __name__ == "__main__":
    asyncio.run(main())
