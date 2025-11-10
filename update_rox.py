import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote

BASE_URL = "https://roxiestreams.live/"
CATEGORIES = ["", "soccer", "nba", "mlb", "nfl", "fighting", "motorsports", "motogp"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "Roxiestreams_VLC.m3u8"
TIVIMATE_OUTPUT = "Roxiestreams_TiviMate.m3u8"

def get_category_links(category_path):
    url = f"{BASE_URL}{category_path}" if category_path else BASE_URL
    headers = {"User-Agent": USER_AGENT, "Referer": REFERER}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # RoxieStreams usually has events in <a> tags inside divs
        links = []
        for a in soup.find_all("a", href=True):
            href = a['href']
            text = a.get_text(strip=True)
            # Filter only links ending with .m3u8 or pointing to event pages
            if href.endswith(".m3u8"):
                links.append((text, href))
            elif "streams" in href or "stream" in href:  # likely event page
                # follow event page to find .m3u8
                event_links = get_event_m3u8(href)
                links.extend(event_links)
        return links
    except Exception as e:
        print(f"❌ Failed to fetch category {category_path}: {e}")
        return []

def get_event_m3u8(event_path):
    url = event_path if event_path.startswith("http") else f"{BASE_URL}{event_path}"
    headers = {"User-Agent": USER_AGENT, "Referer": REFERER}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if href.endswith(".m3u8"):
                links.append((text, href))
        return links
    except Exception as e:
        print(f"❌ Failed to fetch event page {event_path}: {e}")
        return []

def write_playlists(streams):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    m3u_header = f"#EXTM3U x-tvg-url=\"https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz\"\n# Last Updated: {timestamp}\n"

    # VLC Playlist
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u_header)
        for cat, name, url in streams:
            f.write(f'#EXTINF:-1 group-title="RoxieStreams",{cat} - {name}\n{url}\n')

    # TiviMate Playlist
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u_header)
        for cat, name, url in streams:
            ua_encoded = quote(USER_AGENT, safe="")
            f.write(f'#EXTINF:-1 group-title="RoxieStreams",{cat} - {name}\n{url}|referer={REFERER}|user-agent={ua_encoded}\n')

def main():
    all_streams = []
    for cat_path in CATEGORIES:
        cat_name = cat_path if cat_path else "RoxieStreams"
        print(f"Processing category: {cat_name}")
        links = get_category_links(cat_path)
        for text, url in links:
            all_streams.append((cat_name, text, url))
    if all_streams:
        print(f"✅ Found {len(all_streams)} streams.")
    else:
        print("⚠️ No streams found.")
    write_playlists(all_streams)
    print(f"VLC: {VLC_OUTPUT}")
    print(f"TiviMate: {TIVIMATE_OUTPUT}")
    print("✅ Script finished.")

if __name__ == "__main__":
    main()
