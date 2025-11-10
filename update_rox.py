import requests
from bs4 import BeautifulSoup
from urllib.parse import quote
from datetime import datetime
import os

BASE_URL = "https://roxiestreams.live/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
}
REFERER = BASE_URL


def get_category_paths():
    """Auto-discover category paths from the RoxieStreams homepage."""
    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        categories = set([""])  # include main page
        for a in soup.select("a[href]"):
            href = a["href"]
            # Match simple category links like /soccer, /nba, etc.
            if href.startswith("/") and len(href.split("/")) == 2:
                categories.add(href.strip("/"))
            elif href.startswith(BASE_URL):
                subpath = href.replace(BASE_URL, "").strip("/")
                if subpath and "/" not in subpath:
                    categories.add(subpath)

        print(f"✅ Found categories: {categories}")
        return list(categories)
    except Exception as e:
        print(f"⚠️ Failed to auto-fetch categories: {e}")
        return ["", "soccer", "nba", "mlb", "nfl", "fighting", "motorsports"]


def extract_streams_from_page(url):
    """Scrape all .m3u8 links from a single page."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        m3u8_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if ".m3u8" in href:
                title = a.get_text(strip=True) or "Untitled"
                full_url = href if href.startswith("http") else BASE_URL + href.lstrip("/")
                m3u8_links.append((title, full_url))

        print(f"🎯 Found {len(m3u8_links)} links on {url}")
        return m3u8_links
    except Exception as e:
        print(f"❌ Error scraping {url}: {e}")
        return []


def build_playlists():
    """Scrape site and create two playlist formats."""
    categories = get_category_paths()
    all_streams = []

    for category in categories:
        page_url = f"{BASE_URL.rstrip('/')}/{category}" if category else BASE_URL
        all_streams.extend(extract_streams_from_page(page_url))

    if not all_streams:
        print("⚠️ No streams found.")
        return False

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # === VLC Playlist ===
    vlc_lines = [
        '#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"',
        f"# Last Updated: {timestamp}",
    ]

    # === TiviMate Playlist ===
    tivimate_lines = list(vlc_lines)

    for title, url in all_streams:
        vlc_lines.append(f"#EXTINF:-1,{title}")
        vlc_lines.append(f"{url}")

        encoded_ua = quote(HEADERS["User-Agent"])
        tivimate_url = f"{url}|referer={REFERER}|user-agent={encoded_ua}"
        tivimate_lines.append(f"#EXTINF:-1,{title}")
        tivimate_lines.append(tivimate_url)

    # Write both playlists
    vlc_filename = "Roxiestreams_VLC.m3u8"
    tivimate_filename = "Roxiestreams_TiviMate.m3u8"

    for filename, lines in [
        (vlc_filename, vlc_lines),
        (tivimate_filename, tivimate_lines),
    ]:
        with open(filename, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"✅ Created {filename} ({len(lines)//2} channels)")

    return True


if __name__ == "__main__":
    build_playlists()
