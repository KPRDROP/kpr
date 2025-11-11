#!/usr/bin/env python3
"""
update_rox.py

Scrape https://roxiestreams.live/ categories and event pages to extract .m3u8
stream URLs. Produce two playlists:
 - Roxiestreams_VLC.m3u8
 - Roxiestreams_TiviMate.m3u8

This version:
 - Robustly extracts .m3u8 from anchors, <source>/<video>, iframes and inline JS
 - Cleans event titles (removes site suffixes like "- Roxiestreams - Watch Live...")
 - Avoids HTML being injected into URLs or titles
 - Uses TV_INFO to include tvg-id and tvg-logo metadata
 - Encodes user-agent in TiviMate output
"""

from datetime import datetime
from urllib.parse import quote, urljoin, urlparse
import re
import requests
from bs4 import BeautifulSoup
import html
import sys

BASE_URL = "https://roxiestreams.live/"
CATEGORIES = ["", "soccer", "nba", "mlb", "nfl", "fighting", "motorsports", "motogp",
              "ufc", "ppv", "wwe", "f1-streams", "nascar"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "Roxiestreams_VLC.m3u8"
TIVIMATE_OUTPUT = "Roxiestreams_TiviMate.m3u8"

HEADERS = {"User-Agent": USER_AGENT, "Referer": REFERER, "Accept-Language": "en-US,en;q=0.9"}

# Logo / Metadata Dictionary (as you provided)
TV_INFO = {
    "ppv": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/mkj4tC62/PPV.png", "PPV"),
    "soccer": ("Soccer.Dummy.us", "https://i.postimg.cc/HsWHFvV0/Soccer.png", "Soccer"),
    "ufc": ("UFC.Fight.Pass.Dummy.us", "https://i.postimg.cc/59Sb7W9D/Combat-Sports2.png", "UFC"),
    "fighting": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/8c4GjMnH/Combat-Sports.png", "Combat Sports"),
    "nfl": ("Football.Dummy.us", "https://i.postimg.cc/tRNpSGCq/Maxx.png", "NFL"),
    "nba": ("NBA.Basketball.Dummy.us", "https://i.postimg.cc/jdqKB3LW/Basketball-2.png", "NBA"),
    "mlb": ("MLB.Baseball.Dummy.us", "https://i.postimg.cc/FsFmwC7K/Baseball3.png", "MLB"),
    "wwe": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/wTxHn47J/WWE2.png", "WWE"),
    "f1": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Formula 1"),
    "motorsports": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Motorsports"),
    "nascar": ("Racing.Dummy.us", "https://i.postimg.cc/m2dR43HV/Motorsports2.png", "NASCAR Cup Series"),
    "misc": ("Sports.Dummy.us", "https://i.postimg.cc/qMm0rc3L/247.png", "Random Events"),
}

# Regex to find .m3u8 in arbitrary text (capture the URL)
M3U8_RE = re.compile(r"(https?://[^\s\"'<>`]+?\.m3u8(?:\?[^\"'<>`\s]*)?)", re.IGNORECASE)

# Session
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.timeout = 10

def fetch(url, timeout=12):
    """Fetch a page and return (soup, text) or (None, '') on failure."""
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        text = r.text
        soup = BeautifulSoup(text, "html.parser")
        return soup, text
    except Exception as e:
        print(f"  ❌ fetch failed: {url} -> {e}")
        return None, ""

def abs_url(base, href):
    """Return absolute URL for href relative to base."""
    if not href:
        return None
    return urljoin(base, href)

def extract_m3u8_from_text(text, base=None):
    """Return first clean m3u8 URL found in text or None."""
    if not text:
        return None
    # search for HTTP m3u8
    m = M3U8_RE.search(text)
    if m:
        url = m.group(1)
        # fix protocol-relative
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
    t = html.unescape(raw_title).strip()

    # remove weird repeated whitespace and newlines
    t = " ".join(t.split())

    # common site-suffixes to remove (example patterns)
    # We remove trailing phrases like: "- Roxiestreams - Watch Live Sports, F1, UFC, NFL, TV"
    # or " - Watch Live Sports..." or " - Roxiestreams"
    t = re.sub(r"\s*-\s*Roxiestreams.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch Live.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Live Stream.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\|.*$", "", t)  # strip trailing pipe content
    t = t.strip(" -,:")
    return t

def derive_title_from_page(soup, fallback_url=None):
    """Pick best title from page: anchor text already preferred externally; fallback to H1 -> meta og:title -> title tag -> url slug"""
    if not soup:
        return ""
    # H1
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return clean_event_title(h1.get_text(strip=True))
    # meta og:title
    og = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name":"og:title"})
    if og and og.get("content"):
        return clean_event_title(og.get("content"))
    # title
    title = soup.find("title")
    if title and title.get_text(strip=True):
        return clean_event_title(title.get_text(strip=True))
    # fallback from url slug
    if fallback_url:
        path = urlparse(fallback_url).path.rstrip("/")
        if path:
            slug = path.split("/")[-1].replace("-", " ")
            return clean_event_title(slug)
    return ""

def get_event_m3u8(event_href, anchor_text=None):
    """
    Inspect event page (or direct m3u8 link) and return list of (event_title, clean_m3u8_url).
    anchor_text: text from category page anchor (preferred)
    """
    results = []
    if not event_href:
        return results

    # normalize event URL
    event_url = event_href if event_href.startswith("http") else urljoin(BASE_URL, event_href)

    # If the href already contains a direct m3u8, return it quickly (cleaned)
    direct = extract_m3u8_from_text(event_href, base=event_url)
    if direct:
        title = clean_event_title(anchor_text or derive_title_from_page(None, fallback_url=event_url) or direct)
        return [(title, direct)]

    # Fetch event page
    soup, html_text = fetch(event_url)
    if not soup and not html_text:
        return []

    # preferred base title sequence: anchor_text -> H1 -> meta -> title -> url slug
    base_title = clean_event_title(anchor_text) if anchor_text else ""
    if not base_title:
        base_title = derive_title_from_page(soup, fallback_url=event_url)

    seen = set()

    # 1) anchors with .m3u8 href
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # try to extract a clean m3u8 from the href or the anchor html
        cand = extract_m3u8_from_text(href, base=event_url) or extract_m3u8_from_text(str(a), base=event_url)
        if cand:
            cand = cand.strip()
            if cand.startswith("//"):
                cand = "https:" + cand
            if cand not in seen:
                seen.add(cand)
                title = a.get_text(strip=True) or base_title or cand
                title = clean_event_title(title)
                results.append((title, cand))

    # 2) <source src=...> or <video src=...>
    for tag in soup.find_all(["source", "video"], src=True):
        src = tag.get("src", "").strip()
        cand = extract_m3u8_from_text(src, base=event_url)
        if cand and cand not in seen:
            seen.add(cand)
            title = tag.get("title") or tag.get("alt") or base_title or cand
            title = clean_event_title(title)
            results.append((title, cand))

    # 3) iframes: fetch iframe content and search
    for iframe in soup.find_all("iframe", src=True):
        src = iframe.get("src", "").strip()
        iframe_url = urljoin(event_url, src)
        soup_if, html_if = fetch(iframe_url)
        # search iframe HTML for m3u8
        if html_if:
            cand = extract_m3u8_from_text(html_if, base=iframe_url)
            if cand and cand not in seen:
                seen.add(cand)
                title = iframe.get("title") or iframe.get("name") or base_title or cand
                title = clean_event_title(title)
                results.append((title, cand))
            # also inspect anchors/sources inside iframe
            if soup_if:
                for a in soup_if.find_all("a", href=True):
                    cand = extract_m3u8_from_text(a["href"], base=iframe_url)
                    if cand and cand not in seen:
                        seen.add(cand)
                        title = a.get_text(strip=True) or base_title or cand
                        title = clean_event_title(title)
                        results.append((title, cand))
                for tag in soup_if.find_all(["source", "video"], src=True):
                    cand = extract_m3u8_from_text(tag.get("src", ""), base=iframe_url)
                    if cand and cand not in seen:
                        seen.add(cand)
                        title = tag.get("title") or tag.get("alt") or base_title or cand
                        title = clean_event_title(title)
                        results.append((title, cand))

    # 4) inline JS / page HTML search for m3u8
    cand = extract_m3u8_from_text(html_text, base=event_url)
    if cand and cand not in seen:
        seen.add(cand)
        results.append((base_title or cand, cand))

    # Final normalization: absolute urls and dedupe
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
        # ensure title is clean and short
        title_clean = clean_event_title(t)
        if not title_clean:
            # fallback
            title_clean = derive_title_from_page(soup, fallback_url=event_url) or u
        final.append((title_clean, u))
    return final

def get_category_event_candidates(category_path):
    """
    Fetch category page and return a list of (anchor_text, href) candidates.
    We consider anchors whose href contains 'stream'/'streams' or looks like an event slug.
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
        # skip empty or mailto/javascript
        if not href or href.startswith(("mailto:", "javascript:")):
            continue
        full = href if href.startswith("http") else urljoin(cat_url, href)
        # heuristics: consider links that contain 'stream', 'streams', 'match', 'game', or end with -digits
        low = href.lower()
        if ".m3u8" in href or any(k in low for k in ("stream", "streams", "match", "game", "event")) or re.search(r"-\d+$", low):
            if full not in seen:
                seen.add(full)
                candidates.append((text.strip(), full))
    # If nothing found, try scanning JS blobs for m3u8 candidates
    if not candidates:
        # search inline for m3u8
        for m in M3U8_RE.findall(html_text):
            if m and m not in seen:
                seen.add(m)
                candidates.append(("", m))
    print(f"  → Found {len(candidates)} candidate links on category page")
    return candidates

def get_tv_data_for_category(cat_path):
    key = (cat_path or "misc").lower().strip()
    # normalize some paths (e.g., 'f1-streams' -> 'f1')
    key = key.replace("-streams", "").replace("streams", "")
    # try direct mapping
    if key in TV_INFO:
        return TV_INFO[key]
    # try partial match
    for k in TV_INFO:
        if k in key:
            return TV_INFO[k]
    return TV_INFO["misc"]

def write_playlists(streams):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

    # VLC output
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            tvg_id, logo, group_name = get_tv_data_for_category(cat_name)
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="Roxiestreams - {group_name}",{ev_name}\n')
            f.write(f'{url}\n\n')

    # TiviMate output (pipe headers with encoded UA)
    ua_enc = quote(USER_AGENT, safe="")
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            tvg_id, logo, group_name = get_tv_data_for_category(cat_name)
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="Roxiestreams - {group_name}",{ev_name}\n')
            f.write(f'{url}|referer={REFERER}|user-agent={ua_enc}\n\n')

def main():
    print("▶️ Starting RoxieStreams playlist generation...")
    all_streams = []
    seen_urls = set()

    for cat in CATEGORIES:
        try:
            candidates = get_category_event_candidates(cat)
        except Exception as e:
            print(f"  ❌ Failed to parse category {cat}: {e}")
            continue

        for anchor_text, href in candidates:
            # If candidate is direct .m3u8, take it
            if ".m3u8" in href:
                clean = extract_m3u8_from_text(href, base=href) or href
                if clean and clean not in seen_urls:
                    seen_urls.add(clean)
                    title = clean_event_title(anchor_text) or derive_title_from_page(None, fallback_url=href) or clean
                    display_name = f"{(cat or 'Roxiestreams').title()} - {title}"
                    all_streams.append(((cat or "misc"), display_name, clean))
                continue

            # Inspect event page
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
                # prefer event title; if anchor_text is meaningful and shorter, keep anchor_text
                final_title = ev_title or anchor_text or derive_title_from_page(None, fallback_url=href) or clean
                final_title = clean_event_title(final_title)
                display_name = f"{(cat or 'Roxiestreams').title()} - {final_title}"
                all_streams.append(((cat or "misc"), display_name, clean))

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
