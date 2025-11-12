#!/usr/bin/env python3
"""
update_buff.py — enhanced playlist resolver

- Finds .m3u8 and "playlist" endpoints (e.g. /playlist/.../load-playlist)
- Resolves redirects / in-page JS wrappers
- Optional Playwright rendering fallback (tries to extract from dynamic pages)
- Produces BuffStreams_VLC.m3u8 and BuffStreams_TiviMate.m3u8
"""

from datetime import datetime
from urllib.parse import quote, urljoin, urlparse, unquote
import re
import requests
from bs4 import BeautifulSoup
import html
import base64
import sys

# ---------- Config ----------
BASE_URL = "https://buffstreams.plus/"
CATEGORIES = ["", "soccer-live-streams", "f1streams2", "nflstreams2", "nhlstreams2",
              "mlb-live-streams", "mmastreams2", "boxingstreams2",
              "nbastreams2", "cfbstreams2", "ncaastreams", "wwestreams", "wnbastreams"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
REFERER = BASE_URL

VLC_OUTPUT = "BuffStreams_VLC.m3u8"
TIVIMATE_OUTPUT = "BuffStreams_TiviMate.m3u8"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": REFERER,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# tv metadata
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

# ---------- Regex ----------
M3U8_RE = re.compile(r"(https?://[^\s\"'<>`]+?\.m3u8(?:\?[^\"'<>`\s]*)?)", re.IGNORECASE)
QUOTED_M3U8_RE = re.compile(r"""["'](https?://[^"']+?\.m3u8[^"']*)["']""", re.IGNORECASE)
# playlist-like endpoints (sometimes not ending with .m3u8)
PLAYLIST_ENDPOINT_RE = re.compile(r"(https?://[^\s\"'<>`]+?/playlist/[^\s\"'<>`]*)", re.IGNORECASE)

# detect wrapper showPlayer(... 'https:...') patterns
WRAPPER_RE = re.compile(r"showPlayer\([^,]*,[^\)]*['\"]([^'\"]+?)['\"]\)", re.IGNORECASE)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.max_redirects = 6

# optional playwright import (sync)
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# ---------- Helpers ----------
def fetch(url, timeout=12):
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser"), r.text, r
    except Exception as e:
        # print minimal debug, not to flood logs
        print(f"  ❌ fetch failed: {url} -> {e}")
        return None, "", None

def abs_url(base, href):
    if not href:
        return None
    return urljoin(base, href)

def decode_escapes(s: str) -> str:
    if not s:
        return s
    s = s.replace("\\/", "/")
    s = s.replace("\\\"", "\"").replace("\\'", "'")
    # hex \xNN
    s = re.sub(r'\\x([0-9A-Fa-f]{2})', lambda m: chr(int(m.group(1), 16)), s)
    # unicode \uNNNN
    s = re.sub(r'\\u([0-9A-Fa-f]{4})', lambda m: chr(int(m.group(1), 16)), s)
    try:
        s = unquote(s)
    except Exception:
        pass
    s = html.unescape(s)
    return s

def extract_first_m3u8(text, base=None):
    if not text:
        return None
    # direct
    m = M3U8_RE.search(text)
    if m:
        url = m.group(1)
        if url.startswith("//"):
            url = "https:" + url
        if base and not urlparse(url).scheme:
            url = urljoin(base, url)
        return url
    # quoted
    q = QUOTED_M3U8_RE.search(text)
    if q:
        return q.group(1)
    # decode escapes
    dec = decode_escapes(text)
    if dec != text:
        m2 = M3U8_RE.search(dec)
        if m2:
            return m2.group(1)
    return None

def extract_playlist_endpoint(text, base=None):
    """Find playlist-like endpoints even if they don't end with .m3u8"""
    if not text:
        return None
    m = PLAYLIST_ENDPOINT_RE.search(text)
    if m:
        url = m.group(1)
        if base and not urlparse(url).scheme:
            url = urljoin(base, url)
        return url
    # sometimes wrapped: showPlayer(... 'https:/.../playlist/...')
    w = WRAPPER_RE.search(text)
    if w:
        return w.group(1)
    # decode & re-search
    dec = decode_escapes(text)
    if dec != text:
        m2 = PLAYLIST_ENDPOINT_RE.search(dec)
        if m2:
            return m2.group(1)
    return None

def tidy_url_from_wrapper(u):
    """Remove JS wrappers like )' etc. Keep clean http(s) prefix."""
    if not u:
        return u
    # remove surrounding punctuation and trailing quotes/parens
    u = u.strip().strip('"\',); ')
    if u.startswith("//"):
        u = "https:" + u
    return u

def resolve_playlist_endpoint(endpoint_url, referer=None):
    """
    Try to resolve a playlist endpoint (which may return JSON, redirect, or text with final URL).
    Returns final URL (maybe an m3u8 or direct playable path) or None.
    """
    if not endpoint_url:
        return None
    try:
        # do a GET; allow redirects
        headers = HEADERS.copy()
        if referer:
            headers["Referer"] = referer
        r = SESSION.get(endpoint_url, headers=headers, timeout=12, allow_redirects=True)
        # if final location header changed, prefer it
        final = r.url or endpoint_url
        # check response body for m3u8 or playlist-like urls
        body = r.text or ""
        # search m3u8 first
        m = extract_first_m3u8(body, base=final)
        if m:
            return tidy_url_from_wrapper(m)
        # search playlist endpoints inside body
        p = extract_playlist_endpoint(body, base=final)
        if p:
            return tidy_url_from_wrapper(p)
        # sometimes the endpoint itself is the final playable url
        # e.g., /playlist/..../caxi might be playable directly (no .m3u8) — return the final URL
        # prefer r.url (post-redirect)
        if final:
            return tidy_url_from_wrapper(final)
    except Exception as e:
        print(f"  ⚠️ resolve_playlist_endpoint failed for {endpoint_url}: {e}")
    return None

def try_playwright_extract(url, timeout_ms=8000):
    """Optional: use Playwright (sync) to render and extract possible stream URLs."""
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            content = page.content()
            # look for m3u8 or playlist endpoints in page HTML or network requests if possible
            m = extract_first_m3u8(content, base=url)
            if m:
                page.close()
                browser.close()
                return tidy_url_from_wrapper(m)
            p = extract_playlist_endpoint(content, base=url)
            if p:
                page.close()
                browser.close()
                return tidy_url_from_wrapper(p)
            # fallback: inspect <iframe> srcs
            for iframe in page.query_selector_all("iframe[src]"):
                src = iframe.get_attribute("src") or ""
                if src:
                    cand = extract_first_m3u8(src, base=url) or extract_playlist_endpoint(src, base=url)
                    if cand:
                        page.close()
                        browser.close()
                        return tidy_url_from_wrapper(cand)
            page.close()
            browser.close()
    except Exception as e:
        # don't crash if playwright fails
        print(f"  ⚠️ Playwright fallback failed for {url}: {e}")
    return None

def clean_event_title(raw_title):
    if not raw_title:
        return ""
    t = html.unescape(raw_title).strip()
    t = " ".join(t.split())
    t = re.sub(r"\s*-\s*BuffStreams.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch Live.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch.*$", "", t, flags=re.IGNORECASE)
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

# ---------- Extraction ----------
def get_event_m3u8(event_href, anchor_text=None):
    """
    Inspect event page (or endpoint) and return list of (event_title, resolved_stream_url).
    Handles .m3u8, playlist endpoints, wrapper calls and uses Playwright fallback if enabled.
    """
    results = []
    if not event_href:
        return results

    event_url = event_href if event_href.startswith("http") else urljoin(BASE_URL, event_href)

    # quick: if event_href already contains .m3u8
    direct = extract_first_m3u8(event_href, base=event_url)
    if direct:
        results.append((clean_event_title(anchor_text or direct), tidy_url_from_wrapper(direct)))
        return results

    # quick: playlist endpoint present in href string
    pl = extract_playlist_endpoint(event_href, base=event_url)
    if pl:
        resolved = resolve_playlist_endpoint(pl, referer=event_url) or pl
        if resolved:
            results.append((clean_event_title(anchor_text or resolved), tidy_url_from_wrapper(resolved)))
            return results

    # fetch the page
    soup, html_text, resp = fetch(event_url)
    if soup is None and not html_text:
        # try Playwright fallback directly on the event URL
        pw = try_playwright_extract(event_url)
        if pw:
            results.append((clean_event_title(anchor_text or pw), tidy_url_from_wrapper(pw)))
        return results

    base_title = clean_event_title(anchor_text) or derive_title_from_page(soup, fallback_url=event_url)

    seen = set()

    # 1) anchors & common attributes
    for tag in soup.find_all(True, attrs=True):
        for attr in ("href", "data-href", "data-src", "data-url", "data-m3u8", "onclick", "src", "value"):
            val = tag.attrs.get(attr)
            if not val:
                continue
            if isinstance(val, (list, tuple)):
                val = val[0]
            text_to_check = str(val)
            # try m3u8
            cand_m3u8 = extract_first_m3u8(text_to_check, base=event_url)
            if cand_m3u8 and cand_m3u8 not in seen:
                seen.add(cand_m3u8)
                results.append((clean_event_title(tag.get_text(" ", strip=True) or base_title or cand_m3u8), tidy_url_from_wrapper(cand_m3u8)))
            # try playlist endpoint
            cand_pl = extract_playlist_endpoint(text_to_check, base=event_url)
            if cand_pl and cand_pl not in seen:
                seen.add(cand_pl)
                resolved = resolve_playlist_endpoint(cand_pl, referer=event_url) or cand_pl
                if resolved and resolved not in seen:
                    seen.add(resolved)
                    results.append((clean_event_title(tag.get_text(" ", strip=True) or base_title or resolved), tidy_url_from_wrapper(resolved)))

    # 2) <source> / <video>
    for tag in soup.find_all(["source", "video"], src=True):
        src = tag.get("src") or tag.get("data-src") or ""
        m = extract_first_m3u8(src, base=event_url)
        if m and m not in seen:
            seen.add(m)
            results.append((clean_event_title(tag.get("title") or tag.get("alt") or base_title or m), tidy_url_from_wrapper(m)))

    # 3) script blocks (JS): quoted m3u8, playlist endpoints, atob(base64)
    for script in soup.find_all("script"):
        code = script.string or script.get_text()
        if not code:
            continue
        # direct m3u8
        for mm in M3U8_RE.findall(code):
            mm = mm.strip()
            if mm and mm not in seen:
                seen.add(mm)
                results.append((base_title or mm, tidy_url_from_wrapper(mm)))
        # playlist-like
        for pp in PLAYLIST_ENDPOINT_RE.findall(code):
            pp = pp.strip()
            if pp and pp not in seen:
                seen.add(pp)
                resolved = resolve_playlist_endpoint(pp, referer=event_url) or pp
                if resolved and resolved not in seen:
                    seen.add(resolved)
                    results.append((base_title or resolved, tidy_url_from_wrapper(resolved)))
        # atob(base64)
        for b64 in re.findall(r"atob\(['\"]([A-Za-z0-9+/=]+)['\"]\)", code):
            try:
                dec = base64.b64decode(b64 + "===")
                dec_text = dec.decode("utf-8", errors="ignore")
                m = extract_first_m3u8(dec_text, base=event_url)
                if m and m not in seen:
                    seen.add(m)
                    results.append((base_title or m, tidy_url_from_wrapper(m)))
            except Exception:
                pass

    # 4) iframes: follow and inspect (one level)
    for iframe in soup.find_all("iframe", src=True):
        src = iframe.get("src").strip()
        if not src:
            continue
        iframe_url = urljoin(event_url, src)
        # first try static resolve on iframe URL (resolve endpoint)
        cand = extract_first_m3u8(src, base=iframe_url) or extract_playlist_endpoint(src, base=iframe_url)
        if cand:
            res = resolve_playlist_endpoint(cand, referer=event_url) or cand
            if res and res not in seen:
                seen.add(res)
                results.append((clean_event_title(iframe.get("title") or base_title or res), tidy_url_from_wrapper(res)))
            continue
        # fetch iframe content
        s2, text2, r2 = fetch(iframe_url)
        if text2:
            m = extract_first_m3u8(text2, base=iframe_url)
            if m and m not in seen:
                seen.add(m)
                results.append((clean_event_title(base_title or m), tidy_url_from_wrapper(m)))
            p = extract_playlist_endpoint(text2, base=iframe_url)
            if p and p not in seen:
                res = resolve_playlist_endpoint(p, referer=iframe_url) or p
                if res and res not in seen:
                    seen.add(res)
                    results.append((clean_event_title(base_title or res), tidy_url_from_wrapper(res)))
        # optional: playwright on the iframe page (if available)
        if PLAYWRIGHT_AVAILABLE:
            pw = try_playwright_extract(iframe_url)
            if pw and pw not in seen:
                seen.add(pw)
                results.append((clean_event_title(base_title or pw), tidy_url_from_wrapper(pw)))

    # 5) final fallback — look at page body as raw text
    final_m = extract_first_m3u8(html_text, base=event_url)
    if final_m and final_m not in seen:
        seen.add(final_m)
        results.append((clean_event_title(base_title or final_m), tidy_url_from_wrapper(final_m)))
    final_pl = extract_playlist_endpoint(html_text, base=event_url)
    if final_pl and final_pl not in seen:
        resolved = resolve_playlist_endpoint(final_pl, referer=event_url) or final_pl
        if resolved and resolved not in seen:
            seen.add(resolved)
            results.append((clean_event_title(base_title or resolved), tidy_url_from_wrapper(resolved)))

    # if still empty and Playwright available, try render once
    if not results and PLAYWRIGHT_AVAILABLE:
        pw = try_playwright_extract(event_url)
        if pw:
            results.append((clean_event_title(base_title or pw), tidy_url_from_wrapper(pw)))

    # normalize results (absolute urls)
    normalized = []
    norm_seen = set()
    for title, u in results:
        if not u:
            continue
        u = u.strip()
        if u.startswith("//"):
            u = "https:" + u
        if not urlparse(u).scheme:
            u = urljoin(event_url, u)
        if u in norm_seen:
            continue
        norm_seen.add(u)
        normalized.append((clean_event_title(title) or derive_title_from_page(soup, fallback_url=event_url) or u, u))
    return normalized

# ---------- Category parse ----------
def get_category_event_candidates(category_path):
    cat_url = BASE_URL if not category_path else urljoin(BASE_URL, category_path)
    print(f"Processing category: {category_path or 'root'} -> {cat_url}")
    soup, html_text, r = fetch(cat_url)
    if not soup and not html_text:
        return []
    candidates = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True) or ""
        if not href or href.startswith(("mailto:", "javascript:")):
            continue
        full = href if href.startswith("http") else urljoin(cat_url, href)
        low = href.lower()
        # include .m3u8, /playlist/, or anchors that look like event pages
        if ".m3u8" in href or "/playlist/" in href or any(k in low for k in ("stream", "streams", "match", "game", "event")) or re.search(r"-\d+$", low):
            if full not in seen:
                seen.add(full)
                candidates.append((text.strip(), full))
    # fallback: find raw playlist-like or m3u8 strings in inline JS
    if not candidates and html_text:
        for m in M3U8_RE.findall(html_text):
            if m and m not in seen:
                seen.add(m)
                candidates.append(("", m))
        for p in PLAYLIST_ENDPOINT_RE.findall(html_text):
            if p and p not in seen:
                seen.add(p)
                candidates.append(("", p))
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

# ---------- Output ----------
def write_playlists(streams):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

    # VLC
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            tvg_id, logo, group_name = get_tv_data_for_category(cat_name)
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="BuffStreams - {group_name}",{ev_name}\n')
            f.write(f'{url}\n\n')

    # Tivimate - append pipe headers
    ua_enc = quote(USER_AGENT, safe="")
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for cat_name, ev_name, url in streams:
            tvg_id, logo, group_name = get_tv_data_for_category(cat_name)
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="BuffStreams - {group_name}",{ev_name}\n')
            # build origin from referer host (if available)
            origin = REFERER.rstrip('/')
            f.write(f'{url}|referer={REFERER}|origin={origin}|user-agent={ua_enc}\n\n')

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
            # if direct m3u8-like or playlist endpoint in href
            if ".m3u8" in href or "/playlist/" in href:
                # try to resolve playlist endpoints
                if "/playlist/" in href and not href.lower().endswith(".m3u8"):
                    resolved = resolve_playlist_endpoint(href, referer=BASE_URL) or href
                else:
                    resolved = extract_first_m3u8(href, base=href) or href
                if resolved:
                    clean = resolved.strip()
                    if clean not in seen_urls:
                        seen_urls.add(clean)
                        title = clean_event_title(anchor_text) or derive_title_from_page(None, fallback_url=href) or clean
                        display_name = f"{(cat or 'BuffStreams').title()} - {title}"
                        all_streams.append(((cat or "misc"), display_name, clean))
                continue

            # otherwise inspect event page deeply
            found = get_event_m3u8(href, anchor_text)
            for ev_title, ev_url in found:
                if not ev_url:
                    continue
                # treat both m3u8 and playlist endpoints as playable
                if (".m3u8" not in ev_url) and ("/playlist/" not in ev_url):
                    # skip improbable urls
                    continue
                # if it's a playlist endpoint, try resolve
                candidate = ev_url
                if "/playlist/" in ev_url and not ev_url.lower().endswith(".m3u8"):
                    resolved = resolve_playlist_endpoint(ev_url, referer=href) or ev_url
                    candidate = resolved
                clean = candidate.strip()
                if not clean or clean in seen_urls:
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
