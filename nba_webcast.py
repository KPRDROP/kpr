#!/usr/bin/env python3
"""
castweb_nba.py
Clean NBA-only scraper for https://nbawebcast.top/
Generates BOTH:
 - castweb_nba.m3u8      (VLC format)
 - castweb_nba_tivimate.m3u8  (TiviMate pipe-format)
"""

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

DEFAULT_LOGO = "https://i.postimg.cc/B6WMnCRT/basketball-sport-logo-minimalist-style.jpg"

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
async def scrape_nba_games() -> List[Dict]:
    print(f"\n🏀 Scraping NBAWebcast: {NBA_BASE_URL}")
    results = []

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        try:
            async with session.get(NBA_BASE_URL, timeout=20) as resp:
                html = await resp.text()
        except Exception as e:
            print(f"❌ Failed to load NBA page: {e}")
            return []

        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", class_="NBA_schedule_container")

        if not table:
            print("❌ NBA schedule table not found.")
            return []

        rows = table.find("tbody").find_all("tr")
        print(f"📌 Found {len(rows)} NBA games.")

        for row in rows:
            try:
                team_cells = row.find_all("td", class_="teamvs")
                if len(team_cells) < 2:
                    continue

                away = team_cells[0].text.strip()
                home = team_cells[1].text.strip()
                title = f"{away} vs {home}"

                logos = row.find_all("td", class_="teamlogo")
                logo = DEFAULT_LOGO
                if logos and logos[1].find("img"):
                    logo = logos[1].find("img")["src"]

                watch_btn = row.find("button", class_="watch_btn")
                if not (watch_btn and watch_btn.has_attr("data-team")):
                    continue

                team_key = watch_btn["data-team"]
                stream_url = NBA_STREAM_URL_PATTERN.format(team=team_key)

                print(f"🔎 Testing: {title} -> {stream_url}")

                ok = await verify_stream_url(session, stream_url)
                if ok:
                    print(f"✔️ VALID: {title}")
                    results.append({
                        "name": title,
                        "url": stream_url,
                        "tvg_id": "NBA.Game.us",
                        "tvg_logo": logo,
                        "group": "NBAWebcast - Live Games",
                        "ref": NBA_BASE_URL,
                    })
                else:
                    print(f"❌ Invalid stream: {title}")

            except Exception as e:
                print(f"⚠️ Row parsing error: {e}")

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
    print("🚀 Starting NBA-only Webcast Scraper...\n")
    streams = await scrape_nba_games()

    if not streams:
        print("❌ No working NBA streams found.")
        return

    write_vlc_playlist(streams)
    write_tivimate_playlist(streams)

    print(f"\n🎉 Done! Exported {len(streams)} NBA streams.")


if __name__ == "__main__":
    asyncio.run(main())
