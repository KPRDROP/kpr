#!/usr/bin/env python3

from datetime import datetime
from urllib.parse import quote, urljoin, urlparse
import re
import requests
from bs4 import BeautifulSoup
import html
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json

BASE_URL = "https://roxiestreams.su"
CATEGORIES = [
    "soccer",
    "nba",
    "nfl",
    "nhl",
    "fighting"
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "Roxiestreams_VLC.m3u8"
TIVIMATE_OUTPUT = "Roxiestreams_TiviMate.m3u8"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

# Logo / Metadata Dictionary
TV_INFO = {
    "soccer": ("Soccer.Dummy.us", "https://i.postimg.cc/HsWHFvV0/Soccer.png", "Soccer"),
    "mlb": ("MLB.Baseball.Dummy.us", "https://i.postimg.cc/FsFmwC7K/Baseball3.png", "MLB"),
    "nba": ("NBA.Basketball.Dummy.us", "https://i.postimg.cc/jdqKB3LW/Basketball-2.png", "NBA"),
    "nfl": ("Football.Dummy.us", "https://i.postimg.cc/tRNpSGCq/Maxx.png", "NFL"),
    "nhl": ("NHL.Hockey.Dummy.us", "https://i.postimg.cc/mgMRQ7FR/nhl-logo-png-seeklogo-534236.png", "NHL"),
    "fighting": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/8c4GjMnH/Combat-Sports.png", "Combat Sports"),
    "motorsports": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Motorsports"),
    "ufc": ("UFC.Fight.Pass.Dummy.us", "https://i.postimg.cc/59Sb7W9D/Combat-Sports2.png", "UFC"),
    "ppv": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/mkj4tC62/PPV.png", "PPV"),
    "wwe": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/wTxHn47J/WWE2.png", "WWE"),
    "f1": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Formula 1"),
    "f1-streams": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Formula 1"),
    "nascar": ("Racing.Dummy.us", "https://i.postimg.cc/m2dR43HV/Motorsports2.png", "NASCAR Cup Series"),
    "misc": ("Sports.Dummy.us", "https://i.postimg.cc/qMm0rc3L/247.png", "Random Events"),
}

# Regex to find .m3u8 in arbitrary text
M3U8_RE = re.compile(r"(https?://[^\s\"'<>`]+?\.m3u8(?:\?[^\"'<>`\s]*)?)", re.IGNORECASE)

# Session
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.timeout = 15

# Cache
PAGE_CACHE = {}
CACHE_DURATION = 300


def fetch(url, timeout=15, use_cache=True):
    """Fetch a page and return (soup, text) or (None, '') on failure with caching."""
    cache_key = url
    
    if use_cache and cache_key in PAGE_CACHE:
        cache_time, cached_soup, cached_text = PAGE_CACHE[cache_key]
        if time.time() - cache_time < CACHE_DURATION:
            return cached_soup, cached_text
    
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        text = r.text
        soup = BeautifulSoup(text, "html.parser")
        
        if use_cache:
            PAGE_CACHE[cache_key] = (time.time(), soup, text)
        
        return soup, text
    except Exception as e:
        print(f"fetch failed: {url} -> {e}")
        return None, ""


def extract_m3u8_from_text(text, base=None):
    """Return first clean m3u8 URL found in text or None."""
    if not text:
        return None
    m = M3U8_RE.search(text)
    if m:
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        if base and not urlparse(url).scheme:
            url = urljoin(base, url)
        return url
    return None


def clean_event_title(raw_title):
    """Clean the raw title: strip, unescape, and remove common site suffix noise."""
    if not raw_title:
        return ""
    
    if raw_title.startswith("http://") or raw_title.startswith("https://"):
        return ""
    
    t = html.unescape(raw_title).strip()
    t = " ".join(t.split())
    t = re.sub(r'https?://[^\s]+', '', t)
    
    # Remove common suffixes
    t = re.sub(r"\s*-\s*Roxiestreams.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch Live.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Live Stream.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\|.*$", "", t)
    t = re.sub(r'^(Watch|Live|Stream|Event|Game|Match)\s+', '', t, flags=re.IGNORECASE)
    
    t = t.strip(" -,:")
    
    return t if len(t) >= 3 else ""


def derive_title_from_page(soup, fallback_url=None):
    """Pick best title from page."""
    if not soup:
        return ""
    
    # Try H1
    h1 = soup.find("h1")
    if h1:
        title = clean_event_title(h1.get_text(strip=True))
        if title:
            return title
    
    # Try meta og:title
    og = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "og:title"})
    if og and og.get("content"):
        title = clean_event_title(og.get("content"))
        if title:
            return title
    
    # Try title tag
    title_tag = soup.find("title")
    if title_tag:
        title = clean_event_title(title_tag.get_text(strip=True))
        if title:
            return title
    
    # Fallback from URL
    if fallback_url:
        path = urlparse(fallback_url).path.rstrip("/")
        if path:
            slug = path.split("/")[-1].replace("-", " ").replace("_", " ")
            slug = re.sub(r'\d+$', '', slug).strip()
            if slug and len(slug) > 3:
                return slug.title()
    
    return ""


def get_event_links_from_category(soup, category_path):
    """Extract event links from category page using the table structure."""
    events = []
    
    # Find the events table
    table = soup.find("table", {"id": "eventsTable"})
    if not table:
        # Try alternative selectors
        table = soup.find("table", class_=re.compile(r"event|schedule|match"))
    
    if table:
        # Look for rows in tbody
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")
        
        for row in rows:
            # Find the link in the row
            link_tag = row.find("a")
            if not link_tag:
                continue
            
            href = link_tag.get("href")
            if not href:
                continue
            
            # Get event name
            event_name = link_tag.get_text(strip=True)
            if not event_name:
                # Try other columns
                cells = row.find_all("td")
                for cell in cells:
                    text = cell.get_text(strip=True)
                    if text and len(text) > 5 and not text.replace(":", "").isdigit():
                        event_name = text
                        break
            
            if event_name and href:
                full_url = urljoin(BASE_URL, href)
                events.append((clean_event_title(event_name), full_url))
    
    # If table method failed, try finding direct links
    if not events:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(" ", strip=True)
            
            if not href or href.startswith(("mailto:", "javascript:", "#")):
                continue
            
            # Check if it looks like an event link
            if any(k in href.lower() for k in ["stream", "event", "match", "game"]) or re.search(r"/\d+/?$", href):
                full_url = urljoin(BASE_URL, href)
                if text and len(text) > 3:
                    events.append((clean_event_title(text), full_url))
    
    return events


def process_event_page(event_url, event_name, category):
    """Process an event page to extract m3u8 URL."""
    try:
        soup, html_text = fetch(event_url)
        if not soup:
            return None
        
        # Try to find m3u8 in various places
        m3u8_url = None
        
        # 1. Check for m3u8 in script tags
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string:
                content = script.string
                m3u8_match = M3U8_RE.search(content)
                if m3u8_match:
                    m3u8_url = m3u8_match.group(1)
                    break
        
        # 2. Check for m3u8 in iframe sources
        if not m3u8_url:
            iframes = soup.find_all("iframe")
            for iframe in iframes:
                src = iframe.get("src")
                if src:
                    if ".m3u8" in src:
                        m3u8_url = src
                        break
                    # Try fetching iframe content
                    iframe_url = urljoin(event_url, src)
                    iframe_soup, iframe_html = fetch(iframe_url)
                    if iframe_html:
                        m3u8_match = M3U8_RE.search(iframe_html)
                        if m3u8_match:
                            m3u8_url = m3u8_match.group(1)
                            break
        
        # 3. Check for m3u8 in anchor tags
        if not m3u8_url:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if ".m3u8" in href:
                    m3u8_url = href
                    break
        
        # 4. Check page HTML as last resort
        if not m3u8_url:
            m3u8_match = M3U8_RE.search(html_text)
            if m3u8_match:
                m3u8_url = m3u8_match.group(1)
        
        if m3u8_url:
            # Clean and normalize URL
            if m3u8_url.startswith("//"):
                m3u8_url = "https:" + m3u8_url
            if not urlparse(m3u8_url).scheme:
                m3u8_url = urljoin(event_url, m3u8_url)
            
            return m3u8_url
        
        return None
        
    except Exception as e:
        print(f"Error processing event {event_name}: {e}")
        return None


def process_category(category):
    """Process a single category and return streams."""
    streams = []
    seen_urls = set()
    
    try:
        cat_url = urljoin(BASE_URL, category)
        print(f"Processing category: {category} -> {cat_url}")
        
        soup, html_text = fetch(cat_url)
        if not soup:
            print(f"  ✗ Failed to fetch category: {category}")
            return streams
        
        # Get event links
        events = get_event_links_from_category(soup, category)
        print(f"  → Found {len(events)} events in {category}")
        
        if not events:
            # Try finding direct m3u8 links
            for m in M3U8_RE.findall(html_text):
                if m and m not in seen_urls:
                    seen_urls.add(m)
                    streams.append((category, f"{category.title()} Event", m))
            return streams
        
        # Process each event
        for event_name, event_url in events:
            try:
                # Skip if already seen
                if event_url in seen_urls:
                    continue
                seen_urls.add(event_url)
                
                # Get the stream URL
                m3u8_url = process_event_page(event_url, event_name, category)
                
                if m3u8_url:
                    final_name = event_name if event_name else f"{category.title()} Event"
                    streams.append((category, final_name, m3u8_url))
                    print(f"  ✓ {final_name[:50]}...")
                else:
                    print(f"  ✗ No stream: {event_name[:40]}...")
                    
            except Exception as e:
                print(f"  ✗ Error processing {event_name}: {e}")
        
    except Exception as e:
        print(f"Failed to process category {category}: {e}")
    
    return streams


def get_tv_data_for_category(cat_path):
    """Get TV metadata for category."""
    key = (cat_path or "misc").lower().strip()
    key = key.replace("-streams", "").replace("streams", "")
    
    if key in TV_INFO:
        return TV_INFO[key]
    
    for k in TV_INFO:
        if k in key:
            return TV_INFO[k]
    
    return TV_INFO["misc"]


def write_playlists(streams):
    """Write VLC and TiviMate playlists."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

    # VLC output
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            if ev_name.startswith("http"):
                ev_name = "Live Event"
            tvg_id, logo, group_name = get_tv_data_for_category(cat_name)
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="Roxie - {group_name}",{ev_name}\n')
            f.write(f'{url}\n\n')

    # TiviMate output
    ua_enc = quote(USER_AGENT, safe="")
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            if ev_name.startswith("http"):
                ev_name = "Live Event"
            tvg_id, logo, group_name = get_tv_data_for_category(cat_name)
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="Roxie - {group_name}",{ev_name}\n')
            f.write(f'{url}|referer={REFERER}|user-agent={ua_enc}\n\n')


def main():
    """Main function with parallel processing."""
    print("Starting Roxie playlist generation...")
    start_time = time.time()
    
    all_streams = []
    
    # Process categories in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_cat = {executor.submit(process_category, cat): cat for cat in CATEGORIES}
        
        for future in as_completed(future_to_cat):
            cat = future_to_cat[future]
            try:
                streams = future.result()
                all_streams.extend(streams)
                print(f"✓ Category '{cat}' completed: {len(streams)} streams found")
            except Exception as e:
                print(f"✗ Category '{cat}' failed: {e}")
    
    if not all_streams:
        print("No streams found.")
    else:
        print(f"Found {len(all_streams)} streams total.")
    
    write_playlists(all_streams)
    
    elapsed = time.time() - start_time
    print(f"VLC: {VLC_OUTPUT}")
    print(f"TiviMate: {TIVIMATE_OUTPUT}")
    print(f"Finished generating playlists in {elapsed:.2f} seconds.")


if __name__ == "__main__":
    main()
