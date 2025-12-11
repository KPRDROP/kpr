#!/usr/bin/env python3
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import quote
from typing import List, Dict

# ----------------------------------------------------------
#  CONFIG
# ----------------------------------------------------------
NBA_BASE_URL = "https://nbawebcast.top/"
NBA_STREAM_URL_PATTERN = "https://gg.poocloud.in/{team}/index.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

NBA_HEADERS = {
    "Origin": "https://playembed.top",
    "Referer": "https://playembed.top/",
    "User-Agent": USER_AGENT,
}

OUT_VLC = "nba_webcast.m3u8"
OUT_TIVIMATE = "nba_webcast_tivimate.m3u8"

NBA_DEFAULT_LOGO = "https://i.postimg.cc/B6WMnCRT/basketball-sport-logo-minimalist-style.jpg"

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)


# ----------------------------------------------------------
#  VERIFY STREAM
# ----------------------------------------------------------
async def verify_stream_url(session: aiohttp.ClientSession, url: str) -> bool:
    """Check if URL is a valid playable m3u8"""
    try:
        async with session.get(url, headers=NBA_HEADERS, timeout=HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return False

            chunk = await resp.content.read(500)
            text = chunk.decode(errors="ignore")

            return "#EXTM3U" in text or "EXT-X" in text

    except Exception:
        return False


# ----------------------------------------------------------
#  SCRAPE NBA GAMES
# ----------------------------------------------------------
async def scrape_nba_games(default_logo: str) -> List[Dict]:
    print(f"\n🏀 Scraping NBAWebcast: {NBA_BASE_URL}")
    results: List[Dict] = []

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        try:
            async with session.get(NBA_BASE_URL, timeout=20) as response:
                response.raise_for_status()
                html = await response.text()
        except Exception as e:
            print(f"❌ Could not load NBA site: {e}")
            return []

        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", class_="NBA_schedule_container")

        if not table:
            print("❌ NBA games table missing.")
            return []

        rows = table.find("tbody").find_all("tr")
        print(f"📌 Found {len(rows)} NBA games.")

        for row in rows:
            try:
                teams = [t.text.strip() for t in row.find_all("td", class_="teamvs")]
                away, home = teams[0], teams[1]

                logos = row.find_all("td", class_="teamlogo")
                logo = logos[1].find("img")["src"] if len(logos) > 1 else default_logo

                watch_btn = row.find("button", class_="watch_btn")
                if not watch_btn:
                    continue

                team_key = watch_btn["data-team"]
                m3u8_url = NBA_STREAM_URL_PATTERN.format(team_name=team_key)
                title = f"{away} vs {home}"

                print(f"🔎 Testing: {title} -> {m3u8_url}")

                # Verification (just logs, does NOT stop playlist)
                _ = await verify_stream_url(session, m3u8_url, headers=NBA_CUSTOM_HEADERS)

                # Always write stream even if invalid
                results.append({
                    "name": title,
                    "url": m3u8_url,
                    "tvg_id": "NBA.Basketball.Dummy.us",
                    "tvg_logo": NBA_DEFAULT_LOGO,
                    "group": "NBA Games - Live Games",
                    "ref": NBA_BASE_URL,
                    "custom_headers": NBA_CUSTOM_HEADERS,
                })

            except Exception as e:
                print(f"⚠️ Parsing error: {e}")

    return results

# ----------------------------------------------------------
#  WRITE NORMAL M3U (VLC)
# ----------------------------------------------------------
def write_vlc_playlist(streams: List[Dict]):
    if not streams:
        print("⛔ No streams to save.")
        return

    with open(OUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            f.write(
                f'#EXTINF:-1 tvg-id="{s["tvg_id"]}" '
                f'tvg-logo="{s["tvg_logo"]}" group-title="{s["group"]}",{s["name"]}\n'
            )
            f.write(f'#EXTVLCOPT:http-origin={NBA_HEADERS["Origin"]}\n')
            f.write(f'#EXTVLCOPT:http-referrer={NBA_HEADERS["Referer"]}\n')
            f.write(f'#EXTVLCOPT:http-user-agent={USER_AGENT}\n')
            f.write(s["url"] + "\n")

    print(f"✅ Saved: {OUT_VLC}")


# ----------------------------------------------------------
#  WRITE TIVIMATE PLAYLIST
# ----------------------------------------------------------
def write_tivimate_playlist(streams: List[Dict]):
    if not streams:
        print("⛔ No streams to save.")
        return

    encoded_ua = quote(USER_AGENT, safe="")

    with open(OUT_TIVIMATE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            f.write(
                f'#EXTINF:-1 tvg-id="{s["tvg_id"]}" '
                f'tvg-logo="{s["tvg_logo"]}" group-title="{s["group"]}",{s["name"]}\n'
            )
            f.write(
                f'{s["url"]}'
                f'|referer={NBA_HEADERS["Referer"]}'
                f'|origin={NBA_HEADERS["Origin"]}'
                f'|user-agent={encoded_ua}\n'
            )

    print(f"✅ Saved: {OUT_TIVIMATE}")


# ----------------------------------------------------------
#  MAIN
# ----------------------------------------------------------
async def main():
    print("🚀 Starting NBA-only Webcast Scraper...")

    NBA_DEFAULT_LOGO = "https://i.postimg.cc/B6WMnCRT/basketball-sport-logo-minimalist-style-600nw-2484656797.jpg"

    # Scrape NBA games
    streams = await scrape_nba_games(NBA_DEFAULT_LOGO)

    if not streams:
        print("❌ No NBA streams were extracted.")
    else:
        # Save main playlist
        write_playlist(streams, "nba_webcast.m3u8")

        # Save TiviMate playlist
        write_playlist_tivimate(streams, "nba_webcast_tivimate.m3u8")

    print(f"\n🎉 Done! Exported {len(streams)} NBA streams.")


if __name__ == "__main__":
    asyncio.run(main())
