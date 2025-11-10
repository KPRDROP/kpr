#!/usr/bin/env python3
"""
update_rox.py

Scrape https://roxiestreams.live/ categories and event pages to extract .m3u8
stream URLs. Produce two playlists:
 - Roxiestreams_VLC.m3u8
 - Roxiestreams_TiviMate.m3u8

This version includes robust cleaning of discovered m3u8 candidates so the
playlist won't contain JS wrapper calls like showPlayer(...) — only clean URLs.
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote, urljoin
import re
import sys

BASE_URL = "https://roxiestreams.live/"
# categories to scan (empty string = root)
CATEGORIES = ["", "soccer", "nascar", "wwe", "nba", "mlb", "nfl",
              "fighting", "motorsports", "motogp", "f1-streams", "f1", "misc", "ppv"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "Roxiestreams_VLC.m3u8"
TIVIMATE_OUTPUT = "Roxiestreams_TiviMate.m3u8"

HEADERS = {"User-Agent": USER_AGENT, "Referer": REFERER}

# Regex to find clean m3u8 URL substrings (avoid trailing quotes/paren)
M3U8_CLEAN_RE = re.compile(r"(https?://[^\s\"\'\)\]]+?\.m3u8(?:\?[^\"\'\)\]\s]*)?)", re.IGNORECASE)

# helper to fetch a URL and return BeautifulSoup + text
def fetch_page(url, headers=None, timeout=12):
    try:
        h = headers or HEADERS
        resp = requests.get(url, headers=h, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser"), resp.text
    except Exception as e:
        print(f"  ❌ fetch_page failed: {url} -> {e}")
        return None, ""

# Normalize a possibly-relative url to absolute
def abs_url(base, href):
    if not href:
        return None
    return urljoin(base, href)

# Extract first valid m3u8 URL substring from arbitrary text
def extract_first_m3u8(text, base=None):
    if not text:
        return None
    # Try to find a clean m3u8 URL inside the text
    m = M3U8_CLEAN_RE.search(text)
    if m:
        url = m.group(1)
        # Fix protocol-relative
        if url.startswith("//"):
            url = "https:" + url
        # If still relative (unlikely), join with base
        if base and not url.startswith("http"):
            url = abs_url(base, url)
        return url
    return None

# Inspect an event page to find .m3u8 links
def get_event_m3u8(event_url):
    event_url = event_url if event_url.startswith("http") else urljoin(BASE_URL, event_url)
    print(f"    ↳ Inspecting event page: {event_url}")
    soup, html = fetch_page(event_url)
    if not soup:
        return []

    results = []

    # 1) <a href="...m3u8"> direct links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        candidate = extract_first_m3u8(href, base=event_url) or (href if ".m3u8" in href else None)
        if candidate:
            full = abs_url(event_url, candidate)
            results.append((a.get_text(strip=True) or full, full))

    # 2) <source src="..."> or <video src="...">
    for s in soup.find_all(["source", "video"], src=True):
        href = s["src"].strip()
        candidate = extract_first_m3u8(href, base=event_url)
        if candidate:
            results.append((s.get("title") or s.get("alt") or candidate, abs_url(event_url, candidate)))

    # 3) <iframe src="..."> -> follow and search iframe page
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"].strip()
        iframe_url = abs_url(event_url, src)
        # Some iframes embed an m3u8 directly or contain players
        soup_if, html_if = fetch_page(iframe_url)
        if html_if:
            # find m3u8 in iframe HTML (cleaned)
            cand = extract_first_m3u8(html_if, base=iframe_url)
            if cand:
                results.append((iframe.get("title") or iframe.get("name") or cand, cand))
            # also check iframe anchors & sources
            if soup_if:
                for a in soup_if.find_all("a", href=True):
                    href = a["href"].strip()
                    candidate = extract_first_m3u8(href, base=iframe_url)
                    if candidate:
                        results.append((a.get_text(strip=True) or candidate, abs_url(iframe_url, candidate)))
                for s in soup_if.find_all(["source", "video"], src=True):
                    candidate = extract_first_m3u8(s["src"], base=iframe_url)
                    if candidate:
                        results.append((s.get("title") or s.get("alt") or candidate, abs_url(iframe_url, candidate)))

    # 4) Search inline JS / page HTML for m3u8 URLs (clean)
    cand = extract_first_m3u8(html, base=event_url)
    if cand:
        results.append((cand, cand))

    # 5) Look for data attributes commonly used e.g. data-src, data-href, data-m3u8
    for tag in soup.find_all(attrs=True):
        for attr, val in tag.attrs.items():
            if isinstance(val, str) and ".m3u8" in val:
                candidate = extract_first_m3u8(val, base=event_url) or val
                results.append((tag.get_text(strip=True) or candidate, abs_url(event_url, candidate)))

    # normalize & dedupe
    normalized = []
    seen = set()
    for name, url in results:
        if not url:
            continue
        url = url.strip()
        # If the url still contains a JS wrapper, try to extract clean again
        clean = extract_first_m3u8(url, base=event_url)
        if clean:
            url = clean
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            url = abs_url(event_url, url)
        if url in seen:
            continue
        seen.add(url)
        # sanitize name fallback
        nm = (name or "").strip()
        if not nm or nm.lower() == url.lower():
            # try H1 or title
            h1 = soup.find("h1")
            if h1 and h1.get_text(strip=True):
                nm = h1.get_text(strip=True)
            else:
                title = soup.find("title")
                nm = title.get_text(strip=True) if title else url
        normalized.append((nm, url))

    return normalized

# Parse category page and return candidates (event link text + event href)
def get_category_links(category_path):
    cat_url = urljoin(BASE_URL, category_path) if category_path else BASE_URL
    print(f"  → Fetching category: {cat_url}")
    soup, html = fetch_page(cat_url)
    if not soup:
        return []

    found = []
    # Candidate anchor selectors: event cards often use anchors; look for likely anchors
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)
        if not href:
            continue
        # skip mailto, javascript
        if href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        # If anchor is direct .m3u8, take it (cleaned)
        if ".m3u8" in href:
            candidate = extract_first_m3u8(href, base=cat_url) or href
            found.append((text or candidate, abs_url(cat_url, candidate)))
            continue
        # else if it looks like an event page (contains 'stream' or 'streams' or ends with a dash-number)
        if any(k in href.lower() for k in ("stream", "streams", "match", "event", "game")) or re.search(r"-\d+$", href):
            found.append((text or href, abs_url(cat_url, href)))

    # Deduplicate
    dedup = []
    seen = set()
    for name, href in found:
        if href in seen:
            continue
        seen.add(href)
        dedup.append((name, href))
    print(f"    Found {len(dedup)} candidate links on category")
    return dedup

def write_playlists(streams):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

    # VLC (no pipe headers)
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            f.write(f'#EXTINF:-1 group-title="{cat_name}",{ev_name}\n')
            f.write(f'{url}\n\n')

    # TiviMate (pipe headers appended to the URL)
    ua_enc = quote(USER_AGENT, safe="")
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            # Append referer & encoded UA using pipes
            f.write(f'#EXTINF:-1 group-title="{cat_name}",{ev_name}\n')
            f.write(f'{url}|referer={REFERER}|user-agent={ua_enc}\n\n')

def main():
    print("▶️ Starting RoxieStreams playlist generation...")
    all_streams = []
    seen_urls = set()

    for cat in CATEGORIES:
        cat_display = cat if cat else "Roxiestreams"
        try:
            candidates = get_category_links(cat)
        except Exception as e:
            print(f"  ❌ Category parse failed: {cat} -> {e}")
            continue

        for anchor_text, href in candidates:
            # If href already an m3u8, add directly (clean)
            if ".m3u8" in href:
                clean = extract_first_m3u8(href, base=href) or href
                if clean and clean not in seen_urls:
                    seen_urls.add(clean)
                    name = anchor_text or clean
                    # Build display name: Category - Name (avoid repetition)
                    display_name = f"{cat_display.title()} - {name}" if name and cat_display else name
                    all_streams.append((cat_display.title(), display_name, clean))
                continue

            # Otherwise inspect event page (this follows iframes etc)
            try:
                found = get_event_m3u8(href)
            except Exception as e:
                print(f"    ❌ Failed inspecting {href}: {e}")
                found = []

            if not found:
                continue

            for name, url in found:
                # Build a friendly event name
                anchor_text_clean = (anchor_text or "").strip()
                ev_name = name
                if anchor_text_clean:
                    # Avoid duplicates: if anchor_text already in name, don't repeat
                    if anchor_text_clean.lower() in (name or "").lower():
                        ev_name = f"{cat_display.title()} - {name}"
                    else:
                        ev_name = f"{cat_display.title()} - {anchor_text_clean} - {name}"
                else:
                    ev_name = f"{cat_display.title()} - {name}"

                # clean url again and dedupe
                clean = extract_first_m3u8(url, base=href) or url
                if clean and clean not in seen_urls:
                    seen_urls.add(clean)
                    all_streams.append((cat_display.title(), ev_name, clean))

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
