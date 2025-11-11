#!/usr/bin/env python3
"""
update_rox.py

Scrape https://roxiestreams.live/ categories and event pages to extract .m3u8
stream URLs and produces two playlists:
 - Roxiestreams_VLC.m3u8
 - Roxiestreams_TiviMate.m3u8

Fixes:
- Avoid using the m3u8 URL as the event name
- Prefer anchor text; otherwise extract <h1> or <title> from the event page
- Clean and normalize event names (remove "Roxiestreams", "Watch Live..." suffixes)
- Robust extraction of m3u8 from anchors, iframes, inline JS
- TiviMate headers appended with pipe and encoded User-Agent
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

# Regex to find cleaned m3u8 URLs (avoid trailing quotes/paren)
M3U8_CLEAN_RE = re.compile(r"(https?://[^\s\"\'\)\]]+?\.m3u8(?:\?[^\"\'\)\]\s]*)?)", re.IGNORECASE)

# --- Utilities ----------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Normalize encoding and Unicode, trim whitespace."""
    if not text:
        return ""
    # try to fix common mojibake: latin1->utf-8 fallback (harmless if not needed)
    try:
        fixed = text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        fixed = text
    return unicodedata.normalize("NFC", fixed).strip()

def clean_event_name(name: str) -> str:
    """Remove site suffixes and noisy fragments, compact whitespace."""
    if not name:
        return ""
    s = normalize_text(name)
    # remove common suffixes added by site or SEO titles
    s = re.sub(r"\s*-\s*Roxiestreams.*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*-\s*Watch Live.*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*-\s*Watch Live Sports.*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*-\s*Watch.*", "", s, flags=re.IGNORECASE)
    # remove leftover site marketing phrases
    s = re.sub(r"\b(Watch Live Sports|Watch Live|Live Stream|Live)\b", "", s, flags=re.IGNORECASE)
    # remove undesirable characters, collapse whitespace
    s = re.sub(r"[\|\[\]\(\)\"‘’“”\*]+", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def fetch_page(url, headers=None, timeout=12):
    """Return tuple (soup, html_text) or (None, '') on failure."""
    try:
        resp = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        resp.raise_for_status()
        html = resp.text
        return BeautifulSoup(html, "html.parser"), html
    except Exception as e:
        # keep error visible but don't crash
        print(f"  ❌ fetch_page failed: {url} -> {e}")
        return None, ""

def abs_url(base, href):
    if not href:
        return None
    return urljoin(base, href)

def extract_first_m3u8(text, base=None):
    """Return first cleaned m3u8 URL found in arbitrary text, else None."""
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

def looks_like_url(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    return t.startswith("http") or ".m3u8" in t or re.match(r"^[\w\-]+\.[\w\-]+", t)

# --- Extraction ----------------------------------------------------------------

def get_event_m3u8(event_url):
    """
    Inspect an event page and return list of (name, m3u8_url) candidates.
    Name may be an extracted title or anchor text.
    """
    event_url = event_url if event_url.startswith("http") else urljoin(BASE_URL, event_url)
    print(f"    ↳ Inspecting event page: {event_url}")
    soup, html = fetch_page(event_url)
    if not soup and not html:
        return []

    results = []

    # 1) anchors with m3u8 or pointing to m3u8
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        candidate = extract_first_m3u8(href, base=event_url)
        if candidate:
            name = a.get_text(strip=True) or candidate
            results.append((name, candidate))

    # 2) source/video tags
    for tag in soup.find_all(["source", "video"], src=True):
        href = tag.get("src") or ""
        cand = extract_first_m3u8(href, base=event_url)
        if cand:
            name = tag.get("title") or tag.get("alt") or cand
            results.append((name, cand))

    # 3) iframes -> try to fetch iframe & search
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"].strip()
        iframe_url = abs_url(event_url, src)
        if not iframe_url:
            continue
        soup_if, html_if = fetch_page(iframe_url)
        if not (soup_if or html_if):
            continue
        # check iframe page content for m3u8
        cand = extract_first_m3u8(html_if or "", base=iframe_url)
        if cand:
            name = iframe.get("title") or iframe.get("name") or cand
            results.append((name, cand))
        if soup_if:
            for a in soup_if.find_all("a", href=True):
                href = a["href"].strip()
                cand = extract_first_m3u8(href, base=iframe_url)
                if cand:
                    results.append((a.get_text(strip=True) or cand, cand))
            for tag in soup_if.find_all(["source", "video"], src=True):
                cand = extract_first_m3u8(tag.get("src") or "", base=iframe_url)
                if cand:
                    results.append((tag.get("title") or cand, cand))

    # 4) inline JS / page HTML search
    cand = extract_first_m3u8(html or "", base=event_url)
    if cand:
        results.append((cand, cand))

    # 5) attributes that sometimes hold urls
    for tag in soup.find_all(attrs=True):
        for attr_value in tag.attrs.values():
            if isinstance(attr_value, str) and ".m3u8" in attr_value:
                cand = extract_first_m3u8(attr_value, base=event_url) or attr_value
                results.append((tag.get_text(strip=True) or cand, cand))

    # Normalize/dedupe and ensure full absolute URLs
    normalized = []
    seen = set()
    for raw_name, raw_url in results:
        if not raw_url:
            continue
        # try to clean url again if it has wrapper
        clean = extract_first_m3u8(raw_url, base=event_url) or raw_url
        if clean.startswith("//"):
            clean = "https:" + clean
        if not clean.startswith("http"):
            clean = abs_url(event_url, clean)
        if not clean or clean in seen:
            continue
        seen.add(clean)

        name = normalize_text(raw_name or "")
        # If name is missing or just looks like a URL, try H1 or title
        if not name or looks_like_url(name):
            h1 = soup.find("h1") if soup else None
            if h1 and h1.get_text(strip=True):
                name = h1.get_text(strip=True)
            else:
                title = soup.find("title") if soup else None
                if title and title.get_text(strip=True):
                    name = title.get_text(strip=True)
                else:
                    # as last fallback, use domain/path as a short name
                    name = clean

        normalized.append((name, clean))

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
        # direct m3u8 in anchor
        if ".m3u8" in href:
            candidate = extract_first_m3u8(href, base=cat_url) or href
            found.append((text, abs_url(cat_url, candidate)))
            continue
        # link that looks like an event page
        if any(k in href.lower() for k in ("stream", "match", "event", "game", "streams")) or re.search(r"-\d+$", href):
            found.append((text, abs_url(cat_url, href)))

    # dedupe by href
    dedup, seen = [], set()
    for name, href in found:
        if not href:
            continue
        if href in seen:
            continue
        seen.add(href)
        dedup.append((name or "", href))
    print(f"    Found {len(dedup)} candidate links on category")
    return dedup

# --- Playlist writer ----------------------------------------------------------

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
        for cat_name, ev_name, url in streams:
            # clean display name and avoid repeating URLs as names
            display = clean_event_name(ev_name)
            if not display or looks_like_url(display):
                # fallback to something sensible from url path
                display = re.sub(r"https?://(www\.)?", "", url)
                display = display.split("/")[1:]  # drop domain
                display = " ".join(display)[:80] or url

            # final name = "Category - Event"
            final_name = f"{cat_name} - {display}" if cat_name else display

            vlc.write(f'#EXTINF:-1 group-title="{cat_name}",{final_name}\n{url}\n\n')
            tivi.write(f'#EXTINF:-1 group-title="{cat_name}",{final_name}\n{url}|referer={REFERER}|user-agent={ua_enc}\n\n')

# --- Main --------------------------------------------------------------------

def main():
    print("▶️ Starting RoxieStreams playlist generation...")
    all_streams = []
    seen_urls = set()

    for cat in CATEGORIES:
        cat_display = cat.title() if cat else "Roxiestreams"
        try:
            candidates = get_category_links(cat)
        except Exception as e:
            print(f"  ❌ Category parse failed: {cat} -> {e}")
            continue

        for anchor_text, href in candidates:
            # If href is already an m3u8 (direct), try to use anchor_text as name if suitable
            if href and ".m3u8" in href:
                clean_url = extract_first_m3u8(href, base=href) or href
                if not clean_url or clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)

                # Decide name: prefer anchor_text if it looks like a proper title
                at = (anchor_text or "").strip()
                if at and not looks_like_url(at):
                    name = at
                else:
                    # try to fetch page title/h1 (fast attempt)
                    soup, _ = fetch_page(href)
                    if soup:
                        h1 = soup.find("h1")
                        if h1 and h1.get_text(strip=True):
                            name = h1.get_text(strip=True)
                        else:
                            title = soup.find("title")
                            name = title.get_text(strip=True) if title else clean_url
                    else:
                        name = clean_url

                all_streams.append((cat_display, name, clean_url))
                continue

            # otherwise it's an event page - inspect it
            if not href:
                continue
            try:
                candidates = get_event_m3u8(href)
            except Exception as e:
                print(f"    ❌ Failed inspecting {href}: {e}")
                candidates = []

            # candidates: list of (name, url)
            for cand_name, cand_url in candidates:
                if not cand_url:
                    continue
                clean_url = extract_first_m3u8(cand_url, base=href) or cand_url
                if not clean_url or clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)

                # Choose an anchor-level name if available and reasonable
                chosen_name = None
                # prefer page h1/title as returned from get_event_m3u8 (cand_name)
                if cand_name and not looks_like_url(cand_name):
                    chosen_name = cand_name
                # if anchor_text (the link on category page) is usable, prefer that
                at = (anchor_text or "").strip()
                if at and not looks_like_url(at):
                    # if anchor_text is a short friendly name, use it
                    if len(at) < 140:
                        # if cand_name already contains anchor_text, prefer cand_name (more detailed)
                        if not (at.lower() in (cand_name or "").lower()):
                            chosen_name = at
                if not chosen_name:
                    # final fallback to cand_name or url
                    chosen_name = cand_name or clean_url

                all_streams.append((cat_display, chosen_name, clean_url))

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
