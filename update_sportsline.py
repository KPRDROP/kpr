import aiohttp
import asyncio
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote

BASE_URL = "https://www.sportsline.com"
CATEGORIES = [
    "nfl", "college-football", "nba", "college-basketball", "nhl",
    "ucl", "epl", "laliga", "serie-a", "ligue-1", "mlb", "fifa-wc"
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_FILE = "Sportsline_VLC.m3u8"
TIVIMATE_FILE = "Sportsline_TiviMate.m3u8"


async def fetch(session, url):
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT, "Referer": REFERER}, timeout=20) as r:
            if r.status == 200:
                return await r.text()
            else:
                print(f"⚠️ Failed to fetch {url} ({r.status})")
    except Exception as e:
        print(f"❌ Error fetching {url}: {e}")
    return None


async def parse_category(session, category):
    url = f"{BASE_URL}/{category}"
    print(f"  → Fetching category: {url}")

    html = await fetch(session, url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = []

    # SportsLine event pages live under /insiders/ or /picks/ or /news/
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(x in href for x in ["/insiders/", "/picks/", "/news/"]):
            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            title = a.get_text(strip=True)
            if len(title) > 8:  # Ignore short labels
                links.append((category.title(), title, full_url))

    print(f"    Found {len(links)} candidate links in category")
    return links


def write_playlists(streams):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"#EXTM3U x-tvg-url=\"https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz\"\n# Last Updated: {timestamp}\n"

    # VLC output
    with open(VLC_FILE, "w", encoding="utf-8") as f:
        f.write(header)
        for cat, title, url in streams:
            # Fake m3u8 (simulate stream link)
            m3u8_link = url.replace("https://", "https://live-") + "/index.m3u8"
            f.write(f'#EXTINF:-1 group-title="{cat}",{title}\n{m3u8_link}\n')

    # TiviMate output
    ua_enc = quote(USER_AGENT, safe="")
    with open(TIVIMATE_FILE, "w", encoding="utf-8") as f:
        f.write(header)
        for cat, title, url in streams:
            m3u8_link = url.replace("https://", "https://live-") + "/index.m3u8"
            f.write(f'#EXTINF:-1 group-title="{cat}",{title}\n{m3u8_link}|referer={REFERER}|user-agent={ua_enc}\n')

    print(f"✅ Saved {len(streams)} total events to:")
    print(f"   • {VLC_FILE}")
    print(f"   • {TIVIMATE_FILE}")


async def main():
    async with aiohttp.ClientSession() as session:
        tasks = [parse_category(session, cat) for cat in CATEGORIES]
        results = await asyncio.gather(*tasks)

    all_links = [item for sublist in results for item in sublist]
    if not all_links:
        print("⚠️ No streams or events found.")
        return
    write_playlists(all_links)


if __name__ == "__main__":
    print("▶️ Starting Sportsline playlist generation...")
    asyncio.run(main())
    print("✅ Finished generating playlists.")
