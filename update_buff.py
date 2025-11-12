#!/usr/bin/env python3
"""
update_buff.py (improved)

Scrape https://buffstreams.plus/ categories and event pages to extract .m3u8
stream URLs. Produce two playlists:
 - BuffStreams_VLC.m3u8
 - BuffStreams_TiviMate.m3u8

Improvements:
 - Recursively follows nested iframes (controlled depth)
 - Scans <script> blocks for m3u8 inside JS objects/strings, base64 (atob), hex/unicode escapes
 - Looks in data-* attributes, onclick/href JS snippets, and <source>/<video> tags
 - Robust cleaning and dedupe
 - Keeps TV_INFO metadata and TiviMate UA encoding
"""

from datetime import datetime
from urllib.parse import quote, urljoin, urlparse, unquote
import re
import requests
from bs4 import BeautifulSoup
import html
import base64
import codecs

# ---------- Config ----------
BASE_URL = "https://buffstreams.plus/"
CATEGORIES = ["", "soccer-live-streams", "f1streams2", "nflstreams2", "nhlstreams2",
              "mlb-live-streams", "mmastreams2", "boxingstreams2",
              "nbastreams2", "cfbstreams2", "ncaastreams", "wwestreams", "wnbastreams"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "BuffStreams_VLC.m3u8"
TIVIMATE_OUTPUT = "BuffStreams_TiviMate.m3u8"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# Logo / Metadata Dictionary
TV_INFO = {
    "soccer-live-streams": ("Soccer.Dummy.us", "https://i.postimg.cc/HsWHFvV0/Soccer.png", "Soccer"),
    "f1streams2": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Formula 1"),
    "nflstreams2": ("Football.Dummy.us", "https://i.postimg.cc/tRNpSGCq/Maxx.png", "NFL"),
    "nhlstreams2": ("NHL.Hockey.Dummy.us", "https://i.postimg.cc/8Cg8GJ9b/nhl.jpg", "NHL"),
    "mlb-live-streams": ("MLB.Baseball.Dummy.us", "https://i.postimg.cc/FsFmwC7K/Baseball3.png", "MLB"),
    "mmastreams2": ("UFC.Fight.Pass.Dummy.us", "https://i.postimg.cc/59Sb7W9D/Combat-Sports2.png", "UFC"),
    "boxingstreams2": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/8c4GjMnH/Combat-Sports.png", "Combat Sports"),
    "nbastreams2": ("NBA.Basketball.Dummy.us", "https://i.postimg.cc/jdqKB3LW/Basketball-2.png", "NBA"),
    "cfbstreams2": ("NCAA.Football.Dummy.us", "https://i.postimg.cc/HsF0Vp6g/cfb.png", "CFB"),
    "ncaastreams": ("NCAA.Football.Dummy.us", "https://i.postimg.cc/ryPxPVXD/ncaa.webp", "NCAA"),
    "wwestreams": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/43ycJyc3/WWE2.png", "WWE"),
    "wnbastreams": ("WNBA.dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "WNBA"),
    "misc": ("Sports.Dummy.us", "https://i.postimg.cc/qMm0rc3L/247.png", "Random Events")
}

# ---------- Regex / Session ----------
# captures https://... .m3u8 (with optional query)
M3U8_RE = re.compile(r"(https?://[^\s\"'<>`]+?\.m3u8(?:\?[^\"'<>`\s]*)?)", re.IGNORECASE)

# also search for quoted .m3u8 inside JS objects: file: "..." , "file":"...", "src":"...", "url":"..."
QUOTED_M3U8_RE = re.compile(r"""["'](https?://[^"']+?\.m3u8[^"']*)["']""", re.IGNORECASE)

# patterns to find potential encoded strings e.g. atob('...'), base64 blobs, escaped strings
ATOB_RE = re.compile(r"atob\(['\"]([A-Za-z0-9+/=]+)['\"]\)")
HEX_ESCAPE_RE = re.compile(r'\\x([0-9A-Fa-f]{2})')
UNICODE_ESCAPE_RE = re.compile(r'\\u([0-9A-Fa-f]{4})')

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.max_redirects = 5
# ---------- Helpers ----------

def fetch(url, timeout=12):
    """Fetch a URL and return (soup, text). On failure returns (None, '')"""
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        text = r.text or ""
        soup = BeautifulSoup(text, "html.parser")
        return soup, text
    except Exception as e:
        print(f"  ❌ fetch failed: {url} -> {e}")
        return None, ""

def abs_url(base, href):
    if not href:
        return None
    return urljoin(base, href)

def decode_escapes(s: str) -> str:
    """Unescape common JS/HTML escape sequences and URL-escapes."""
    if not s:
        return s
    # replace \/ -> /
    s = s.replace("\\/", "/")
    # decode hex escapes \xNN
    def hx(m):
        return chr(int(m.group(1), 16))
    s = HEX_ESCAPE_RE.sub(hx, s)
    # decode unicode escapes \uNNNN
    def un(m):
        return chr(int(m.group(1), 16))
    s = UNICODE_ESCAPE_RE.sub(un, s)
    # unescape HTML entities
    s = html.unescape(s)
    # URL unquote
    try:
        s = unquote(s)
    except Exception:
        pass
    return s

def extract_first_m3u8(text, base=None):
    """Try multiple extraction strategies and return first clean m3u8 or None."""
    if not text:
        return None
    # quick direct regex
    m = M3U8_RE.search(text)
    if m:
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        if base and not urlparse(url).scheme:
            url = urljoin(base, url)
        return url
    # quoted forms
    mq = QUOTED_M3U8_RE.search(text)
    if mq:
        url = mq.group(1)
        return url
    # try decode escapes and search again
    dec = decode_escapes(text)
    if dec != text:
        m2 = M3U8_RE.search(dec)
        if m2:
            url = m2.group(1)
            return url
    return None

def clean_event_title(raw_title):
    if not raw_title:
        return ""
    t = html.unescape(raw_title).strip()
    t = " ".join(t.split())
    # remove common site suffix noise
    t = re.sub(r"\s*-\s*BuffStreams.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch Live.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Live Stream.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\|.*$", "", t)
    t = t.strip(" -,:")
    return t

def derive_title_from_page(soup, fallback_url=None):
    if not soup:
        return ""
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return clean_event_title(h1.get_text(strip=True))
    og = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "og:title"})
    if og and og.get("content"):
        return clean_event_title(og.get("content"))
    title = soup.find("title")
    if title and title.get_text(strip=True):
        return clean_event_title(title.get_text(strip=True))
    if fallback_url:
        path = urlparse(fallback_url).path.rstrip("/")
        if path:
            slug = path.split("/")[-1].replace("-", " ")
            return clean_event_title(slug)
    return ""

# ---------- Core extraction (deep) ----------
def get_event_m3u8(event_href, anchor_text=None, depth=0, max_depth=3):
    """
    Inspect event page (or direct m3u8 link) and return list of (event_title, clean_m3u8_url).
    Recursively follows iframes up to max_depth.
    """
    results = []
    if not event_href:
        return results
    if depth > max_depth:
        return results

    event_url = event_href if event_href.startswith("http") else urljoin(BASE_URL, event_href)

    # If event_href already contains direct m3u8
    direct = extract_first_m3u8(event_href, base=event_url)
    if direct:
        title = clean_event_title(anchor_text or derive_title_from_page(None, fallback_url=event_url) or direct)
        return [(title, direct)]

    soup, html_text = fetch(event_url)
    if not soup and not html_text:
        return []

    base_title = clean_event_title(anchor_text) if anchor_text else derive_title_from_page(soup, fallback_url=event_url)
    seen = set()

    # 1) anchors / data-* / onclick attributes
    for a in soup.find_all(True, attrs=True):
        attrs = a.attrs
        # check typical attributes that may contain links
        for key in ("href", "data-src", "data-href", "data-url", "data-m3u8", "onclick", "value", "src"):
            val = attrs.get(key)
            if not val:
                continue
            # if it's a list (BeautifulSoup sometimes returns lists), pick first string
            if isinstance(val, (list, tuple)):
                val = val[0]
            text_to_check = str(val)
            cand = extract_first_m3u8(text_to_check, base=event_url)
            if cand and cand not in seen:
                seen.add(cand)
                # get friendly name
                name = (a.get_text(" ", strip=True) or base_title or cand)
                results.append((clean_event_title(name), cand))

    # 2) <source> / <video> tags
    for tag in soup.find_all(["source", "video"], src=True):
        src = tag.get("src") or tag.get("data-src") or ""
        cand = extract_first_m3u8(src, base=event_url)
        if cand and cand not in seen:
            seen.add(cand)
            title = tag.get("title") or tag.get("alt") or base_title or cand
            results.append((clean_event_title(title), cand))

    # 3) inline scripts: direct urls, JSON objects, atob base64, escaped strings
    for script in soup.find_all("script"):
        code = ""
        try:
            code = script.string or script.get_text()
        except Exception:
            continue
        if not code:
            continue
        # 3a) direct quoted m3u8 in JS
        for m in QUOTED_M3U8_RE.findall(code):
            if m and m not in seen:
                seen.add(m)
                results.append((base_title or m, m))
        # 3b) generic regex
        for m in M3U8_RE.findall(code):
            if m and m not in seen:
                seen.add(m)
                results.append((base_title or m, m))
        # 3c) atob base64 decode
        for enc in ATOB_RE.findall(code):
            try:
                decoded = base64.b64decode(enc + "===")  # pad just in case
                decoded_text = decoded.decode("utf-8", errors="ignore")
                # unescape and search again
                dec_unesc = decode_escapes(decoded_text)
                for m in M3U8_RE.findall(dec_unesc):
                    if m and m not in seen:
                        seen.add(m)
                        results.append((base_title or m, m))
            except Exception:
                pass
        # 3d) look for hex / unicode escapes in long JS strings
        dec_code = decode_escapes(code)
        for m in M3U8_RE.findall(dec_code):
            if m and m not in seen:
                seen.add(m)
                results.append((base_title or m, m))

    # 4) iframes (recursive)
    for iframe in soup.find_all("iframe", src=True):
        src = iframe.get("src").strip()
        if not src:
            continue
        iframe_url = urljoin(event_url, src)
        # avoid loops by marking iframe_url as seen text; but still allow different m3u8
        if iframe_url in seen:
            continue
        # recursively inspect iframe (increase depth)
        sub = get_event_m3u8(iframe_url, anchor_text=base_title, depth=depth + 1, max_depth=max_depth)
        for t, u in sub:
            if u and u not in seen:
                seen.add(u)
                results.append((t or base_title, u))

    # 5) final fallback: search whole page HTML
    fallback = extract_first_m3u8(html_text, base=event_url)
    if fallback and fallback not in seen:
        seen.add(fallback)
        results.append((base_title or fallback, fallback))

    # Normalize: absolute urls and dedupe final results
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
        title_clean = clean_event_title(t) or derive_title_from_page(soup, fallback_url=event_url) or u
        final.append((title_clean, u))
    return final

# ---------- Category discovery ----------
def get_category_event_candidates(category_path):
    """Return list of (anchor_text, href) candidates from category page."""
    cat_url = BASE_URL if not category_path else urljoin(BASE_URL, category_path)
    print(f"Processing category: {category_path or 'root'} -> {cat_url}")
    soup, html_text = fetch(cat_url)
    if not soup and not html_text:
        return []

    candidates = []
    seen = set()
    # heuristic anchors: any anchor with 'stream', 'streams', 'match', 'game', 'event' or ends with -digits
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True) or ""
        if not href or href.startswith(("mailto:", "javascript:")):
            continue
        full = href if href.startswith("http") else urljoin(cat_url, href)
        low = href.lower()
        if ".m3u8" in href or any(k in low for k in ("stream", "streams", "match", "game", "event")) or re.search(r"-\d+$", low):
            if full not in seen:
                seen.add(full)
                candidates.append((text.strip(), full))
    # If nothing found, scan inline JS for raw .m3u8 strings
    if not candidates:
        for m in M3U8_RE.findall(html_text):
            if m and m not in seen:
                seen.add(m)
                candidates.append(("", m))
    print(f"  → Found {len(candidates)} candidate links on category page")
    return candidates

def get_tv_data_for_category(cat_path):
    key = (cat_path or "misc").lower().strip()
    key = key.replace("-streams", "").replace("streams", "")
    if key in TV_INFO:
        return TV_INFO[key]
    for k in TV_INFO:
        if k in key:
            return TV_INFO[k]
    return TV_INFO["misc"]

# ---------- Write outputs ----------
def write_playlists(streams):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

    # VLC output
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            tvg_id, logo, group_name = get_tv_data_for_category(cat_name)
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="BuffStreams - {group_name}",{ev_name}\n')
            f.write(f'{url}\n\n')

    # TiviMate output (pipe headers with encoded UA)
    ua_enc = quote(USER_AGENT, safe="")
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            tvg_id, logo, group_name = get_tv_data_for_category(cat_name)
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="BuffStreams - {group_name}",{ev_name}\n')
            # append Tivimate pipe headers: referer and encoded user-agent
            f.write(f'{url}|referer={REFERER}|user-agent={ua_enc}\n\n')

# ---------- Main ----------
def main():
    print("▶️ Starting BuffStreams playlist generation...")
    all_streams = []
    seen_urls = set()

    for cat in CATEGORIES:
        try:
            candidates = get_category_event_candidates(cat)
        except Exception as e:
            print(f"  ❌ Failed to parse category {cat}: {e}")
            continue

        for anchor_text, href in candidates:
            # If candidate is direct .m3u8
            if ".m3u8" in href:
                clean = extract_first_m3u8(href, base=href) or href
                if clean and clean not in seen_urls:
                    seen_urls.add(clean)
                    title = clean_event_title(anchor_text) or derive_title_from_page(None, fallback_url=href) or clean
                    display_name = f"{(cat or 'BuffStreams').title()} - {title}"
                    all_streams.append(((cat or "misc"), display_name, clean))
                continue

            # Inspect event page deeply
            found = get_event_m3u8(href, anchor_text)
            for ev_title, ev_url in found:
                if not ev_url or ".m3u8" not in ev_url:
                    continue
                clean = extract_first_m3u8(ev_url, base=href) or ev_url
                if not clean:
                    continue
                if clean in seen_urls:
                    continue
                seen_urls.add(clean)
                final_title = ev_title or anchor_text or derive_title_from_page(None, fallback_url=href) or clean
                final_title = clean_event_title(final_title)
                display_name = f"{(cat or 'BuffStreams').title()} - {final_title}"
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
