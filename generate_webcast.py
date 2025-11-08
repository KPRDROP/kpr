import asyncio
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, quote
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

OUTPUT_FILE_VLC = "SportsWebcast_VLC.m3u8"
OUTPUT_FILE_TIVIMATE = "SportsWebcast_TiviMate.m3u8"

NFL_BASE_URL = "https://nflwebcast.com/"
NHL_BASE_URL = "https://slapstreams.com/"
MLB_BASE_URL = "https://mlbwebcast.com/"
MLS_BASE_URL = "https://mlswebcast.com/"
NBA_BASE_URL = "https://nbawebcast.top/"

NFL_CHANNEL_URLS = [
    "https://nflwebcast.com/nflnetwork/",
    "https://nflwebcast.com/nflredzone/",
    "https://nflwebcast.com/espnusa/",
]
NBA_CHANNEL_URLS = [
    "https://nbawebcast.top/espn/",
    "https://nbawebcast.top/tnt/",
    "https://nbawebcast.top/nba-tv/"
]

CHANNEL_METADATA = {
    "nflnetwork": {"name": "NFL Network", "id": "NFL.Network.HD.us", "logo": "https://i.imgur.com/Lwtw1Hc.png"},
    "nflredzone": {"name": "NFL RedZone", "id": "NFL.RedZone.HD.us", "logo": "https://i.imgur.com/4M3tUyE.png"},
    "espnusa": {"name": "ESPN USA", "id": "ESPN.HD.us", "logo": "https://i.imgur.com/yzQZLhW.png"},
    "espn": {"name": "ESPN", "id": "ESPN.HD.us", "logo": "https://i.imgur.com/yzQZLhW.png"},
    "tnt": {"name": "TNT", "id": "TNT.HD.us", "logo": "https://i.imgur.com/2ZQFIBL.png"},
    "nba-tv": {"name": "NBA TV", "id": "NBATV.HD.us", "logo": "https://i.imgur.com/xu9U1rS.png"},
}


def normalize_game_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name.strip())
    if "@" in name:
        parts = name.split("@")
        if len(parts) == 2:
            return f"{parts[0].strip().title()} @ {parts[1].strip().title()}"
    return name.title()


async def verify_stream_url(session: aiohttp.ClientSession, url: str, headers: Dict[str, str]) -> bool:
    """Checks if stream is alive."""
    try:
        async with session.get(url, timeout=10, headers=headers) as r:
            return r.status == 200
    except Exception:
        return False


async def find_stream_from_servers_on_page(context: BrowserContext, page_url: str, base_url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Opens match page and extracts .m3u8 links."""
    page = await context.new_page()
    candidate_urls: List[str] = []

    def handle_request(request):
        if ".m3u8" in request.url:
            candidate_urls.append(request.url)

    page.on("request", handle_request)
    try:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_load_state("networkidle", timeout=20000)

        # Some pages lazy-load streams
        await asyncio.sleep(5)

        for stream_url in reversed(candidate_urls):
            if await verify_stream_url(session, stream_url, {"Referer": base_url, "User-Agent": USER_AGENT}):
                return stream_url
    except Exception as e:
        print(f"⚠️ Could not extract from {page_url}: {e}")
    finally:
        page.remove_listener("request", handle_request)
        await page.close()
    return None


async def scrape_league(base_url: str, channel_urls: List[str], group_prefix: str, default_logo: str) -> List[Dict]:
    """Main scraper per league."""
    results = []
    async with async_playwright() as p, aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(user_agent=USER_AGENT)

        try:
            page = await context.new_page()
            print(f"🌐 Visiting {base_url}")
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=25000)
            await asyncio.sleep(5)

            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            match_links = soup.select("a[href*='match'], a[href*='game']")
            if not match_links:
                print(f"⚠️ No match links found on {base_url}")

            for a_tag in match_links[:10]:
                href = a_tag.get("href")
                if not href:
                    continue
                name = normalize_game_name(a_tag.get_text())
                stream_url = await find_stream_from_servers_on_page(context, urljoin(base_url, href), base_url, session)
                if stream_url:
                    results.append({
                        "name": name,
                        "url": stream_url,
                        "tvg_id": "sports.game",
                        "tvg_logo": default_logo,
                        "group": f"{group_prefix} - Live Games",
                        "ref": base_url,
                    })

            # Add 24/7 channels
            for url in channel_urls:
                slug = url.strip("/").split("/")[-1]
                stream_url = await find_stream_from_servers_on_page(context, url, base_url, session)
                if stream_url:
                    meta = CHANNEL_METADATA.get(slug, {"name": slug, "id": slug, "logo": default_logo})
                    results.append({
                        "name": meta["name"],
                        "url": stream_url,
                        "tvg_id": meta["id"],
                        "tvg_logo": meta["logo"],
                        "group": f"{group_prefix} - 24/7 Channels",
                        "ref": base_url,
                    })

        except Exception as e:
            print(f"❌ Error scraping {group_prefix}: {e}")
        finally:
            await browser.close()
    return results


def write_playlists(streams: List[Dict]):
    """Writes M3U playlists."""
    if not streams:
        print("⚠️ No streams found, skipping write.")
        return

    # VLC
    with open(OUTPUT_FILE_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            f.write(f'#EXTINF:-1 tvg-id="{s["tvg_id"]}" tvg-logo="{s["tvg_logo"]}" group-title="{s["group"]}",{s["name"]}\n')
            f.write(f'#EXTVLCOPT:http-referrer={s["ref"]}\n')
            f.write(f'#EXTVLCOPT:http-origin={s["ref"]}\n')
            f.write(f'#EXTVLCOPT:http-user-agent={USER_AGENT}\n')
            f.write(s["url"] + "\n")

    # TiviMate
    with open(OUTPUT_FILE_TIVIMATE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            headers = f"referer={s['ref']}|origin={s['ref']}|user-agent={ENCODED_USER_AGENT}|icy-metadata=1"
            f.write(f'#EXTINF:-1 tvg-id="{s["tvg_id"]}" tvg-logo="{s["tvg_logo"]}" group-title="{s["group"]}",{s["name"]}\n')
            f.write(f"{s['url']}|{headers}\n")

    print(f"✅ Wrote {len(streams)} streams to {OUTPUT_FILE_VLC} and {OUTPUT_FILE_TIVIMATE}")


async def main():
    print("🚀 Starting Sports Webcast Scraper...")
    leagues = [
        scrape_league(NFL_BASE_URL, NFL_CHANNEL_URLS, "NFLWebcast", "https://i.imgur.com/Lwtw1Hc.png"),
        scrape_league(NHL_BASE_URL, [], "NHLWebcast", "https://i.imgur.com/ZxRZpcP.png"),
        scrape_league(MLB_BASE_URL, [], "MLBWebcast", "https://i.imgur.com/ENqOehA.png"),
        scrape_league(MLS_BASE_URL, [], "MLSWebcast", "https://i.imgur.com/4Wb9P1O.png"),
        scrape_league(NBA_BASE_URL, NBA_CHANNEL_URLS, "NBAWebcast", "https://i.imgur.com/xu9U1rS.png"),
    ]
    results = await asyncio.gather(*leagues)
    all_streams = [s for group in results for s in group]
    write_playlists(all_streams)


if __name__ == "__main__":
    asyncio.run(main())
