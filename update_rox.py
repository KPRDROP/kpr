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

BASE_URL = "https://roxiestreams.su"
CATEGORIES = [
    "soccer",
    "mlb",
    "nba",
    "nfl",
    "nhl",
    "fighting",
    "motorsports"
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "Roxiestreams_VLC.m3u8"
TIVIMATE_OUTPUT = "Roxiestreams_TiviMate.m3u8"

HEADERS = {"User-Agent": USER_AGENT, "Referer": REFERER, "Accept-Language": "en-US,en;q=0.9"}

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

# Regex to find .m3u8 in arbitrary text (capture the URL)
M3U8_RE = re.compile(r"(https?://[^\s\"'<>`]+?\.m3u8(?:\?[^\"'<>`\s]*)?)", re.IGNORECASE)

# Session
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.timeout = 10

# Cache for fetched pages
PAGE_CACHE = {}
CACHE_DURATION = 300


def fetch(url, timeout=12, use_cache=True):
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


def get_event_m3u8(event_href, anchor_text=None):
    """
    Inspect event page (or direct m3u8 link) and return list of (event_title, clean_m3u8_url).
    """
    results = []
    if not event_href:
        return results

    event_url = event_href if event_href.startswith("http") else urljoin(BASE_URL, event_href)

    # If the href already contains a direct m3u8, return it quickly
    direct = extract_m3u8_from_text(event_href, base=event_url)
    if direct:
        title = ""
        if anchor_text:
            title = clean_event_title(anchor_text)
        if not title:
            title = derive_title_from_page(None, fallback_url=event_url)
        if not title:
            path = urlparse(event_url).path
            if path:
                title = path.split("/")[-1].replace("-", " ").replace("_", " ").title()
        if not title or title.startswith("http"):
            title = "Live Event"
        return [(title, direct)]

    # Fetch event page
    soup, html_text = fetch(event_url)
    if not soup and not html_text:
        return []

    # Get base title
    base_title = ""
    if anchor_text:
        base_title = clean_event_title(anchor_text)
    if not base_title:
        base_title = derive_title_from_page(soup, fallback_url=event_url)
    if not base_title:
        base_title = "Live Event"

    seen = set()

    # 1) anchors with .m3u8 href
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        cand = extract_m3u8_from_text(href, base=event_url) or extract_m3u8_from_text(str(a), base=event_url)
        if cand:
            cand = cand.strip()
            if cand.startswith("//"):
                cand = "https:" + cand
            if cand not in seen:
                seen.add(cand)
                title = a.get_text(strip=True)
                if title:
                    title = clean_event_title(title)
                if not title:
                    title = base_title
                if title and not title.startswith("http"):
                    results.append((title, cand))

    # 2) <source src=...> or <video src=...>
    for tag in soup.find_all(["source", "video"], src=True):
        src = tag.get("src", "").strip()
        cand = extract_m3u8_from_text(src, base=event_url)
        if cand and cand not in seen:
            seen.add(cand)
            title = tag.get("title") or tag.get("alt")
            if title:
                title = clean_event_title(title)
            if not title:
                title = base_title
            if title and not title.startswith("http"):
                results.append((title, cand))

    # 3) iframes: fetch iframe content and search
    for iframe in soup.find_all("iframe", src=True):
        src = iframe.get("src", "").strip()
        iframe_url = urljoin(event_url, src)
        soup_if, html_if = fetch(iframe_url)
        if html_if:
            cand = extract_m3u8_from_text(html_if, base=iframe_url)
            if cand and cand not in seen:
                seen.add(cand)
                title = iframe.get("title") or iframe.get("name")
                if title:
                    title = clean_event_title(title)
                if not title:
                    title = base_title
                if title and not title.startswith("http"):
                    results.append((title, cand))
            
            if soup_if:
                for a in soup_if.find_all("a", href=True):
                    cand = extract_m3u8_from_text(a["href"], base=iframe_url)
                    if cand and cand not in seen:
                        seen.add(cand)
                        title = a.get_text(strip=True)
                        if title:
                            title = clean_event_title(title)
                        if not title:
                            title = base_title
                        if title and not title.startswith("http"):
                            results.append((title, cand))
                
                for tag in soup_if.find_all(["source", "video"], src=True):
                    cand = extract_m3u8_from_text(tag.get("src", ""), base=iframe_url)
                    if cand and cand not in seen:
                        seen.add(cand)
                        title = tag.get("title") or tag.get("alt")
                        if title:
                            title = clean_event_title(title)
                        if not title:
                            title = base_title
                        if title and not title.startswith("http"):
                            results.append((title, cand))

    # 4) inline JS / page HTML search for m3u8
    if not results:
        cand = extract_m3u8_from_text(html_text, base=event_url)
        if cand and cand not in seen:
            seen.add(cand)
            results.append((base_title, cand))

    # Final normalization
    final = []
    final_seen = set()
    for t, u in results:
        if not u:
            continue
        u = u.strip()
        if u.startswith("//"):
            u = "https:" + u
        if not urlparse(u).scheme:
            u = urljoin(event_url, u)
        if u in final_seen:
            continue
        final_seen.add(u)
        
        title_clean = clean_event_title(t) if t and not t.startswith("http") else base_title
        if not title_clean or title_clean.startswith("http"):
            title_clean = base_title
        final.append((title_clean, u))
    
    return final


def get_category_event_candidates(category_path):
    """
    Fetch category page and return a list of (anchor_text, href) candidates.
    Uses the proven approach from the old code.
    """
    if not category_path:
        cat_url = BASE_URL
    else:
        cat_url = urljoin(BASE_URL, category_path)

    print(f"Processing category: {category_path or 'root'} -> {cat_url}")
    soup, html_text = fetch(cat_url)
    if not soup and not html_text:
        return []

    candidates = []
    seen = set()
    
    # Collect anchors that look like event pages or direct .m3u8
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True) or ""
        
        if not href or href.startswith(("mailto:", "javascript:")):
            continue
        
        full = href if href.startswith("http") else urljoin(cat_url, href)
        low = href.lower()
        
        # Heuristics: consider links that contain 'stream', 'streams', 'match', 'game', or end with -digits
        if (".m3u8" in href or 
            any(k in low for k in ("stream", "streams", "match", "game", "event")) or 
            re.search(r"-\d+$", low)):
            if full not in seen:
                seen.add(full)
                clean_text = clean_event_title(text)
                if not clean_text or clean_text.startswith("http"):
                    clean_text = ""
                candidates.append((clean_text, full))
    
    # If nothing found, search inline for m3u8
    if not candidates:
        for m in M3U8_RE.findall(html_text):
            if m and m not in seen:
                seen.add(m)
                candidates.append(("", m))
    
    print(f"  → Found {len(candidates)} candidate links on category page")
    return candidates


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


def process_category(category):
    """Process a single category and return streams."""
    streams = []
    seen_urls = set()
    
    try:
        candidates = get_category_event_candidates(category)
    except Exception as e:
        print(f"Failed to parse category {category}: {e}")
        return streams
    
    for anchor_text, href in candidates:
        # If candidate is direct .m3u8, take it
        if ".m3u8" in href:
            clean = extract_m3u8_from_text(href, base=href) or href
            if clean and clean not in seen_urls:
                seen_urls.add(clean)
                title = clean_event_title(anchor_text) if anchor_text else ""
                if not title:
                    title = derive_title_from_page(None, fallback_url=href)
                if not title or title.startswith("http"):
                    title = f"{category.title() if category else 'Sports'} Event"
                streams.append(((category or "misc"), title, clean))
            continue

        # Inspect event page using the proven method
        found = get_event_m3u8(href, anchor_text)
        for ev_title, ev_url in found:
            if not ev_url or ".m3u8" not in ev_url:
                continue
            clean = extract_m3u8_from_text(ev_url, base=href) or ev_url
            if not clean:
                continue
            if clean in seen_urls:
                continue
            seen_urls.add(clean)
            
            final_title = ev_title if ev_title and not ev_title.startswith("http") else ""
            if not final_title and anchor_text:
                final_title = clean_event_title(anchor_text)
            if not final_title:
                final_title = f"{category.title() if category else 'Sports'} Event"
            streams.append(((category or "misc"), final_title, clean))
    
    return streams


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
