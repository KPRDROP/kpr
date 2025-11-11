#!/usr/bin/env python3
"""
update_rox.py (final version with UTF-8 normalization)

Scrapes https://roxiestreams.live/ categories and event pages to extract .m3u8
stream URLs and produces two playlists:
 - Roxiestreams_VLC.m3u8
 - Roxiestreams_TiviMate.m3u8

Features:
✔ Clean event names (removes "Watch Live Sports...", etc.)
✔ Robust .m3u8 extraction
✔ Encoded headers for TiviMate
✔ UTF-8 normalization (fixes AtlÃ©tico → Atlético)
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote, urljoin
import re
import unicodedata

BASE_URL = "https://roxiestreams.live/"
CATEGORIES = ["", "soccer", "nascar", "wwe", "nba", "mlb", "nfl",
              "fighting", "motorsports", "motogp", "f1-streams"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "Roxiestreams_VLC.m3u8"
TIVIMATE_OUTPUT = "Roxiestreams_TiviMate.m3u8"

HEADERS = {"User-Agent": USER_AGENT, "Referer": REFERER}

# Regex for m3u8 links
M3U8_CLEAN_RE = re.compile(r"(https?://[^\s\"\'\)\]]+?\.m3u8(?:\?[^\"\'\)\]\s]*)?)", re.IGNORECASE)

# --- Utility Functions ---

def normalize_text(text: str) -> str:
    """Fix misencoded text and normalize Unicode to NFC."""
    if not text:
        return ""
    try:
        # Try common misencoding fix
        fixed = text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        fixed = text
    # Normalize diacritics
    return unicodedata.normalize("NFC", fixed.strip())

def fetch_page(url, headers=None, timeout=12):
    try:
        resp = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        return soup, html
    except Exception as e:
        print(f"  ❌ fetch_page failed: {url} -> {e}")
        return None, ""

def abs_url(base, href):
    return urljoin(base, href) if href else None

def extract_first_m3u8(text, base=None):
    if not text:
        return None
    m = M3U8_CLEAN_RE.search(text)
    if not m:
        return None
    url = m.group(1)
    if url.startswith("//"):
        url = "https:" + url
    if base and not url.startswith("http"):
        url = abs_url(base, url)
    return url

def clean_event_name(name: str) -> str:
    """Removes site suffixes and cleans up repeated words."""
    if not name:
        return ""
    name = normalize_text(name)
    name = re.sub(r"\s*-\s*Roxiestreams.*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*-\s*Watch Live.*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[\|\[\]\(\)]+", "", name)
    name = re.sub(r"\s{2,}", " ", name).strip()

    # Remove duplicates in title
    words = name.split()
    seen, cleaned = set(), []
    for w in words:
        lw = w.lower()
        if lw not in seen:
            seen.add(lw)
            cleaned.append(w)
    return " ".join(cleaned).strip()

# --- Scraping Functions ---

def get_event_m3u8(event_url):
    event_url = event_url if event_url.startswith("http") else urljoin(BASE_URL, event_url)
    print(f"    ↳ Inspecting event page: {event_url}")
    soup, html = fetch_page(event_url)
    if not soup:
        return []

    results = []

    # <a> tags
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        candidate = extract_first_m3u8(href, base=event_url)
        if candidate:
            results.append((a.get_text(strip=True) or candidate, candidate))

    # <video> or <source>
    for s in soup.find_all(["source", "video"], src=True):
        href = s["src"].strip()
        candidate = extract_first_m3u8(href, base=event_url)
        if candidate:
            results.append((s.get("title") or candidate, candidate))

    # iframes
    for iframe in soup.find_all("iframe", src=True):
        iframe_url = abs_url(event_url, iframe["src"].strip())
        if not iframe_url:
            continue
        soup_if, html_if = fetch_page(iframe_url)
        if not html_if:
            continue
        candidate = extract_first_m3u8(html_if, base=iframe_url)
        if candidate:
            results.append((iframe.get("title") or candidate, candidate))

    # inline script/text
    cand = extract_first_m3u8(html, base=event_url)
    if cand:
        results.append((cand, cand))

    # Deduplicate
    normalized, seen = [], set()
    for name, url in results:
        if not url or url in seen:
            continue
        seen.add(url)
        name = clean_event_name(name)
        if not name:
            title_tag = soup.find("title")
            name = clean_event_name(title_tag.get_text(strip=True) if title_tag else url)
        normalized.append((name, url))
    return normalized

def get_category_links(category_path):
    cat_url = urljoin(BASE_URL, category_path) if category_path else BASE_URL
    print(f"  → Fetching category: {cat_url}")
    soup, _ = fetch_page(cat_url)
    if not soup:
        return []

    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "javascript:")):
            continue
        text = a.get_text(" ", strip=True)
        if ".m3u8" in href:
            candidate = extract_first_m3u8(href, base=cat_url)
            found.append((text, abs_url(cat_url, candidate)))
        elif any(k in href.lower() for k in ("stream", "match", "event", "game")):
            found.append((text, abs_url(cat_url, href)))

    dedup, seen = [], set()
    for name, href in found:
        if href not in seen:
            seen.add(href)
            dedup.append((clean_event_name(name), href))
    print(f"    Found {len(dedup)} candidate links on category")
    return dedup

# --- Playlist Writer ---

def write_playlists(streams):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = (
        '#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n'
        f"# Last Updated: {ts}\n\n"
    )

    ua_enc = quote(USER_AGENT, safe="")

    with open(VLC_OUTPUT, "w", encoding="utf-8") as vlc, open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as tivi:
        vlc.write(header)
        tivi.write(header)

        for cat, name, url in streams:
            clean_name = clean_event_name(name)
            vlc.write(f'#EXTINF:-1 group-title="{cat}",{clean_name}\n{url}\n\n')
            tivi.write(f'#EXTINF:-1 group-title="{cat}",{clean_name}\n{url}|referer={REFERER}|user-agent={ua_enc}\n\n')

# --- Main Execution ---

def main():
    print("▶️ Starting RoxieStreams playlist generation...")
    all_streams, seen_urls = [], set()

    for cat in CATEGORIES:
        cat_display = cat.title() if cat else "Roxiestreams"
        try:
            candidates = get_category_links(cat)
        except Exception as e:
            print(f"  ❌ Category parse failed: {cat} -> {e}")
            continue

        for anchor_text, href in candidates:
            if ".m3u8" in href:
                clean_url = extract_first_m3u8(href, base=href) or href
                if clean_url not in seen_urls:
                    seen_urls.add(clean_url)
                    name = clean_event_name(anchor_text)
                    all_streams.append((cat_display, f"{cat_display} - {name}", clean_url))
                continue

            try:
                found = get_event_m3u8(href)
            except Exception as e:
                print(f"    ❌ Failed inspecting {href}: {e}")
                continue

            for name, url in found:
                clean_name = clean_event_name(name)
                clean_url = extract_first_m3u8(url, base=href) or url
                if clean_url and clean_url not in seen_urls:
                    seen_urls.add(clean_url)
                    all_streams.append((cat_display, f"{cat_display} - {clean_name}", clean_url))

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
