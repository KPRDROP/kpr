import asyncio
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, quote
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, async_playwright

# ========== CONFIG ==========
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")
DYNAMIC_WAIT_TIMEOUT = 15000
GAME_TABLE_WAIT_TIMEOUT = 30000
STREAM_PATTERN = re.compile(r"\.m3u8($|\?)", re.IGNORECASE)

OUTPUT_FILE_VLC = "SportsWebcast_VLC.m3u8"
OUTPUT_FILE_TIVIMATE = "SportsWebcast_TiviMate.m3u8"

# ========== BASE URLS ==========
NFL_BASE_URL = "https://nflwebcast.com/"
NHL_BASE_URL = "https://slapstreams.com/"
MLB_BASE_URL = "https://mlbwebcast.com/"
MLS_BASE_URL = "https://mlswebcast.com/"
NBA_BASE_URL = "https://nbawebcast.top/"

# ========== CHANNELS ==========
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


# ===== Normalization and Helpers =====
def normalize_game_name(original_name: str) -> str:
    cleaned_name = " ".join(original_name.splitlines()).strip()
    if "@" in cleaned_name:
        parts = cleaned_name.split("@")
        if len(parts) == 2:
            team1 = parts[0].strip().title()
            team2 = parts[1].strip().title()
            team2 = re.split(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\b', team2, 1)[0].strip()
            return f"{team1} @ {team2}"
    return " ".join(cleaned_name.strip().split()).title()


async def verify_stream_url(session: aiohttp.ClientSession, url: str, headers: Optional[Dict[str, str]] = None) -> bool:
    request_headers = headers or {}
    if "User-Agent" not in request_headers:
        request_headers["User-Agent"] = session.headers.get("User-Agent", USER_AGENT)
    try:
        async with session.get(url, timeout=10, allow_redirects=True, headers=request_headers) as response:
            if response.status == 200:
                print(f" ✔️ URL Verified (200 OK): {url}")
                return True
            else:
                print(f" ❌ URL Failed ({response.status}): {url}")
                return False
    except asyncio.TimeoutError:
        print(f" ❌ URL Timed Out: {url}")
        return False
    except aiohttp.ClientError as e:
        print(f" ❌ URL Client Error ({type(e).__name__}): {url}")
        return False


# (keep the rest of your scraping code unchanged) ...
# ----------------------------------------------------
# To keep this readable, no functional changes below,
# only the playlist writer at the end has been improved.


def write_playlists(streams: List[Dict]):
    """Write dual playlists for VLC and TiviMate."""
    if not streams:
        print("⏹️ No streams found.")
        with open(OUTPUT_FILE_VLC, "w", encoding="utf-8") as v, \
             open(OUTPUT_FILE_TIVIMATE, "w", encoding="utf-8") as t:
            v.write("#EXTM3U\n")
            t.write("#EXTM3U\n")
        return

    with open(OUTPUT_FILE_VLC, "w", encoding="utf-8") as vlc, \
         open(OUTPUT_FILE_TIVIMATE, "w", encoding="utf-8") as tivi:
        vlc.write("#EXTM3U\n")
        tivi.write("#EXTM3U\n")

        for entry in streams:
            name = entry["name"]
            tvg_id = entry["tvg_id"]
            logo = entry["tvg_logo"]
            group = entry["group"]
            ref = entry["ref"]
            url = entry["url"]

            # --- VLC ---
            vlc.write(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}\n')
            vlc.write(f'#EXTVLCOPT:http-referrer={ref}\n')
            vlc.write(f'#EXTVLCOPT:http-origin={ref}\n')
            vlc.write(f'#EXTVLCOPT:http-user-agent={USER_AGENT}\n')
            vlc.write(url + "\n")

            # --- TiviMate ---
            headers = f"referer={ref}|origin={ref}|user-agent={ENCODED_USER_AGENT}|icy-metadata=1"
            tivi.write(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}\n')
            tivi.write(f"{url}|{headers}\n")

    print(f"✅ Playlists saved:\n - {OUTPUT_FILE_VLC}\n - {OUTPUT_FILE_TIVIMATE}")


# Replace the call to write_playlist() in your main() with:
# write_playlists(all_streams)

async def main():
    print("🚀 Starting Sports Webcast Scraper...")
    NBA_DEFAULT_LOGO = "http://drewlive24.duckdns.org:9000/Logos/Basketball.png"
    tasks = [
        scrape_league(NFL_BASE_URL, NFL_CHANNEL_URLS, "NFLWebcast", "NFL.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Maxx.png"),
        scrape_league(NHL_BASE_URL, NHL_CHANNEL_URLS, "NHLWebcast", "NHL.Hockey.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Hockey.png"),
        scrape_league(MLB_BASE_URL, MLB_CHANNEL_URLS, "MLBWebcast", "MLB.Baseball.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/MLB.png"),
        scrape_league(MLS_BASE_URL, MLS_CHANNEL_URLS, "MLSWebcast", "MLS.Soccer.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Football2.png"),
        scrape_nba_league(NBA_DEFAULT_LOGO),
    ]
    results = await asyncio.gather(*tasks)
    all_streams = [s for league in results for s in league]
    write_playlists(all_streams)


if __name__ == "__main__":
    asyncio.run(main())
