#!/usr/bin/env python3
"""
update_sportsline.py

Scrape https://www.sportsline.com/ categories to extract .m3u8 stream URLs.
Produce two playlists:
 - Sportsline_VLC.m3u8
 - Sportsline_TiviMate.m3u8
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote, urljoin
import re

BASE_URL = "https://www.sportsline.com/"
CATEGORIES = [
    "nfl", "college-football", "nba", "college-basketball",
    "nhl", "ucl", "epl", "laliga", "serie-a", "ligue-1",
    "mlb", "fifa-wc"
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "Sportsline_VLC.m3u8"
TIVIMATE_OUTPUT = "Sportsline_TiviMate.m3u8"

HEADERS = {"User-Agent": USER_AGENT, "Referer": REFERER}
M3U8_CLEAN_RE = re.compile(r"(https?://[^\s\"\'\)\]]+?\.m3u8(?:\?[^\"\'\)\]\s]*)?)", re.IGNORECASE)

def fetch_page(url, headers=None, timeout=12):
    try:
        h = headers or HEADERS
        resp = requests.get(url, headers=h, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser"), resp.text
    except Exception as e:
        print(f"  ❌ fetch_page failed: {url} -> {e}")
        return None, ""

def abs_url(base, href):
    if not href:
        return None
    return urljoin(base, href)

def extract_first_m3u8(text, base=None):
    if not text:
        return None
    m = M3U8_CLEAN_RE.search(text)
    if m:
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        if base and not url.startswith("http"):
            url = abs_url(base, url)
        return url
    return None

def get_event_m3u8(event_url):
    event_url = event_url if event_url.startswith("http") else urljoin(BASE_URL, event_url)
    print(f"    ↳ Inspecting event page: {event_url}")
    soup, html = fetch_page(event_url)
    if not soup:
        return []

    results = []

    # Look for <a href> with .m3u8
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        candidate = extract_first_m3u8(href, base=event_url)
        if candidate:
            results.append((a.get_text(strip=True) or candidate, candidate))

    # <source> or <video src>
    for s in soup.find_all(["source", "video"], src=True):
        href = s["src"].strip()
        candidate = extract_first_m3u8(href, base=event_url)
        if candidate:
            results.append((s.get("title") or s.get("alt") or candidate, candidate))

    # inline JS or data attributes
    cand = extract_first_m3u8(html, base=event_url)
    if cand:
        results.append((cand, cand))

    for tag in soup.find_all(attrs=True):
        for attr, val in tag.attrs.items():
            if isinstance(val, str) and ".m3u8" in val:
                candidate = extract_first_m3u8(val, base=event_url) or val
                results.append((tag.get_text(strip=True) or candidate, candidate))

    # dedupe
    seen = set()
    normalized = []
    for name, url in results:
        if url in seen:
            continue
        seen.add(url)
        normalized.append((name.strip(), url.strip()))

    return normalized

def get_category_links(category_path):
    cat_url = urljoin(BASE_URL, category_path)
    print(f"  → Fetching category: {cat_url}")
    soup, html = fetch_page(cat_url)
    if not soup:
        return []

    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)
        if not href:
            continue
        if href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        if ".m3u8" in href:
            candidate = extract_first_m3u8(href, base=cat_url) or href
            found.append((text or candidate, candidate))
            continue
        if "stream" in href or "match" in href:
            found.append((text or href, href))

    # dedupe
    seen = set()
    dedup = []
    for name, href in found:
        if href in seen:
            continue
        seen.add(href)
        dedup.append((name, abs_url(cat_url, href)))
    print(f"    Found {len(dedup)} candidate links in category")
    return dedup

def write_playlists(streams):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

    # VLC
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            f.write(f'#EXTINF:-1 group-title="{cat_name}",{ev_name}\n{url}\n\n')

    # TiviMate
    ua_enc = quote(USER_AGENT, safe="")
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            f.write(f'#EXTINF:-1 group-title="{cat_name}",{ev_name}\n{url}|referer={REFERER}|user-agent={ua_enc}\n\n')

def main():
    print("▶️ Starting Sportsline playlist generation...")
    all_streams = []
    seen_urls = set()

    for cat in CATEGORIES:
        cat_display = cat.title()
        try:
            candidates = get_category_links(cat)
        except Exception as e:
            print(f"  ❌ Failed parsing category {cat}: {e}")
            continue

        for anchor_text, href in candidates:
            if ".m3u8" in href:
                clean = extract_first_m3u8(href, base=href) or href
                if clean not in seen_urls:
                    seen_urls.add(clean)
                    display_name = f"{cat_display} - {anchor_text}" if anchor_text else cat_display
                    all_streams.append((cat_display, display_name, clean))
                continue

            try:
                found = get_event_m3u8(href)
            except Exception as e:
                print(f"    ❌ Failed inspecting {href}: {e}")
                continue

            for name, url in found:
                clean = extract_first_m3u8(url, base=href) or url
                if clean in seen_urls:
                    continue
                seen_urls.add(clean)
                display_name = f"{cat_display} - {anchor_text}" if anchor_text else f"{cat_display} - {name}"
                all_streams.append((cat_display, display_name, clean))

    if not all_streams:
        print("⚠️ No streams found.")
    else:
        print(f"✅ Found {len(all_streams)} streams.")

    write_playlists(all_streams)
    print(f"VLC: {VLC_OUTPUT}")
    print(f"TiviMate: {TIVIMATE_OUTPUT}")
    print("✅ Finished generating playlists.")

if __name__ == "__main__":
    main()
