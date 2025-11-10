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

# List of category paths
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

MAX_RECURSION = 2  # Avoid endless recursion

def get_page_html(session, url):
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"⚠️ Failed to load {url}: {e}")
        return ""

def extract_m3u8_from_html(session, html, base_url, category_name, visited_urls=None, depth=0):
    """
    Recursively extract .m3u8 links with their event names and categories
    Returns a list of tuples: (category_name, event_name, stream_url)
    """
    if visited_urls is None:
        visited_urls = set()
    if depth > MAX_RECURSION:
        return []

    results = []
    soup = BeautifulSoup(html, "html.parser")

    # Links to streams or subpages
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = urljoin(base_url, href)
        if full_url in visited_urls:
            continue
        visited_urls.add(full_url)

        # Event name from link text
        event_name = a.get_text(strip=True)
        if not event_name:
            event_name = None

        # Direct .m3u8 link
        if ".m3u8" in href:
            if not event_name:
                event_name = "Roxie Event"
            results.append((category_name, event_name, full_url))
        # Internal page, recurse
        elif href.startswith("/") or href.startswith(base_url):
            sub_html = get_page_html(session, full_url)
            sub_links = extract_m3u8_from_html(session, sub_html, full_url, category_name, visited_urls, depth + 1)
            # Propagate parent link text if subpage does not have a proper event name
            for sub_category, sub_event_name, sub_url in sub_links:
                name_to_use = event_name or sub_event_name
                results.append((category_name, name_to_use, sub_url))

    # Iframe src links
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        iframe_url = urljoin(base_url, src)
        if iframe_url in visited_urls:
            continue
        visited_urls.add(iframe_url)
        iframe_html = get_page_html(session, iframe_url)
        for match in re.findall(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', iframe_html):
            iframe_title = iframe.get("title") or iframe.get("alt") or "Roxie Event"
            results.append((category_name, iframe_title.strip(), match))

    # Raw .m3u8 in JS or HTML
    for match in re.findall(r'(https?://[^\s"\']+\.m3u8[^\s"\']*)', html):
        results.append((category_name, "Roxie Event", match))

    return results

def build_m3u_files(all_links):
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

    for category, event_name, link in all_links:
        # Combine category and event name
        display_name = f"{category} - {event_name}" if category else event_name

        # VLC format
        vlc_lines.append(f'#EXTINF:-1 group-title="RoxieStreams",{display_name}')
        vlc_lines.append(link)

        # TiviMate format
        encoded_ua = quote(HEADERS["User-Agent"])
        tivi_lines.append(f'#EXTINF:-1 group-title="RoxieStreams",{display_name}')
        tivi_lines.append(f'{link}|referer={BASE_URL}|user-agent={encoded_ua}')

    with open("Roxiestreams_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(vlc_lines))
    with open("Roxiestreams_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(tivi_lines))

    print(f"✅ Generated {len(all_links)} streams.")
    print("✅ Created Roxiestreams_VLC.m3u8 and Roxiestreams_TiviMate.m3u8")

def main():
    all_links = []
    print("✅ Starting RoxieStreams scraping...")

    with requests.Session() as session:
        for path in CATEGORY_PATHS:
            url = urljoin(BASE_URL, path)
            category_name = path.replace("-", " ").title() if path else "General"
            html = get_page_html(session, url)
            links = extract_m3u8_from_html(session, html, url, category_name)
            print(f"🎯 Found {len(links)} links on {url}")
            all_links.extend(links)

    # Remove duplicates (same URL)
    seen_urls = set()
    unique_links = []
    for cat, name, url in all_links:
        if url not in seen_urls:
            seen_urls.add(url)
            unique_links.append((cat, name, url))

    build_m3u_files(unique_links)

if __name__ == "__main__":
    main()
