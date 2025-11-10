import requests
import re
import os
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin, quote

BASE_URL = "https://roxiestreams.live/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0",
    "Referer": BASE_URL,
}

CATEGORY_PATHS = [
    "",  # main page
    "soccer-streams-1",
    "fighting",
    "f1-streams",
    "motogp",
    "mlb",
    "nfl",
    "motorsports",
    "soccer-streams-14",
    "nba",
    "soccer"
]

def get_page_html(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"⚠️ Failed to load {url}: {e}")
        return ""

def extract_m3u8_from_html(html, base_url):
    """Extract .m3u8 URLs from <a>, <iframe>, and embedded JS"""
    links = set()
    soup = BeautifulSoup(html, "html.parser")

    # <a href> direct links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".m3u8" in href:
            links.add(urljoin(base_url, href))
        # follow links to subpages
        elif href.startswith("/") or href.startswith(base_url):
            full_url = urljoin(base_url, href)
            sub_html = get_page_html(full_url)
            links.update(extract_m3u8_from_html(sub_html, full_url))

    # <iframe src> links
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        iframe_url = urljoin(base_url, src)
        iframe_html = get_page_html(iframe_url)
        for match in re.findall(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', iframe_html):
            links.add(match)

    # raw .m3u8 inside JS
    for match in re.findall(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', html):
        links.add(match)

    return links

def build_m3u_files(all_links):
    """Build VLC and TiviMate playlists"""
    if not all_links:
        print("⚠️ No streams found, skipping file creation.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    header = (
        '#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n'
        f"# Last Updated: {timestamp}\n"
    )

    vlc_lines = [header]
    tivi_lines = [header]

    for i, link in enumerate(all_links, 1):
        title = f"Roxie Channel {i}"
        vlc_lines.append(f'#EXTINF:-1 group-title="RoxieStreams",{title}')
        vlc_lines.append(link)

        encoded_ua = quote(HEADERS["User-Agent"])
        tivi_lines.append(f'#EXTINF:-1 group-title="RoxieStreams",{title}')
        tivi_lines.append(f'{link}|referer={BASE_URL}|user-agent={encoded_ua}')

    with open("Roxiestreams_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(vlc_lines))
    with open("Roxiestreams_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(tivi_lines))

    print(f"✅ Generated {len(all_links)} streams.")
    print("✅ Created Roxiestreams_VLC.m3u8 and Roxiestreams_TiviMate.m3u8")

def main():
    all_links = set()
    print("✅ Starting RoxieStreams scraping...")

    found_categories = set(CATEGORY_PATHS)
    print(f"✅ Found categories: {found_categories}")

    for path in CATEGORY_PATHS:
        url = urljoin(BASE_URL, path)
        html = get_page_html(url)
        links = extract_m3u8_from_html(html, url)
        print(f"🎯 Found {len(links)} links on {url}")
        all_links.update(links)

    build_m3u_files(list(all_links))

if __name__ == "__main__":
    main()
