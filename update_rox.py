import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote
from datetime import datetime
import os

BASE_URL = "https://roxiestreams.live/"
CATEGORY_PATHS = ["", "soccer", "nba", "mlb", "nfl", "fighting", "motorsports", "f1", "motogp"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
HEADERS = {"User-Agent": USER_AGENT, "Referer": BASE_URL}

OUTPUT_VLC = "Roxiestreams_VLC.m3u8"
OUTPUT_TIVIMATE = "Roxiestreams_TiviMate.m3u8"

def fetch_category_links(category_path):
    url = urljoin(BASE_URL, category_path)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    # Find all links to events
    for a in soup.select("a[href]"):
        href = a.get("href")
        text = a.get_text(strip=True)
        if href and text and ".m3u8" not in text.lower():  # Skip direct .m3u8 links
            full_url = urljoin(BASE_URL, href)
            links.append((text, full_url))
    return links

def fetch_m3u8_link(event_url):
    resp = requests.get(event_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    # Grab first m3u8 link
    m3u8_link = None
    for a in soup.select("a[href]"):
        href = a.get("href")
        if href and href.endswith(".m3u8"):
            m3u8_link = href
            break
    return m3u8_link

def main():
    playlist_entries = []

    for category in CATEGORY_PATHS:
        category_name = category.capitalize() if category else "RoxieStreams"
        print(f"Processing category: {category_name}")
        try:
            events = fetch_category_links(category)
            for event_name, event_url in events:
                m3u8 = fetch_m3u8_link(event_url)
                if not m3u8:
                    continue
                # Combine category + event name
                full_name = f"{category_name} - {event_name}"
                # VLC entry
                playlist_entries.append(f'#EXTINF:-1 group-title="RoxieStreams",{full_name}\n{m3u8}')
                # TiviMate entry with headers
                ua_encoded = quote(USER_AGENT, safe="")
                tm_url = f"{m3u8}|referer={BASE_URL}|user-agent={ua_encoded}"
                playlist_entries.append(f'#EXTINF:-1 group-title="RoxieStreams",{full_name}\n{tm_url}')
        except Exception as e:
            print(f"❌ Error processing category {category_name}: {e}")

    # Write VLC playlist
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {timestamp}\n'
    vlc_content = header + "\n".join([e for i, e in enumerate(playlist_entries) if i % 2 == 0])
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write(vlc_content)
    # Write TiviMate playlist
    tm_content = header + "\n".join([e for i, e in enumerate(playlist_entries) if i % 2 == 1])
    with open(OUTPUT_TIVIMATE, "w", encoding="utf-8") as f:
        f.write(tm_content)

    print(f"✅ Finished. {len(playlist_entries)//2} streams found.")
    print(f"VLC: {OUTPUT_VLC}")
    print(f"TiviMate: {OUTPUT_TIVIMATE}")

if __name__ == "__main__":
    main()
