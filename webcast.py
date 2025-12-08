#!/usr/bin/env python3
"""
Rebuilt webcast.py

Goal:
- Crawl MLS Webcast (and similar pages), find event pages (URLs + human names + logos)
- For each event page: attempt to extract a playable .m3u8 URL by:
  * scanning HTML for direct .m3u8 strings
  * decoding common obfuscation patterns (reversed base64, plain base64)
  * using Playwright to load the page and capture network requests for .m3u8
  * attempting to click the player / "play" controls to trigger requests
- Verify discovered .m3u8s with aiohttp (HEAD/GET)
- Produce a TiviMate-compatible M3U playlist with metadata
Notes:
- Requires: playwright, aiohttp, beautifulsoup4
- Example: python3 webcast.py
"""

import asyncio
import re
import json
import base64
from typing import Optional, List, Dict, Tuple
from urllib.parse import urljoin, quote, urlparse
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# --- Config ---
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
OUTPUT_FILE = "SportsWebcast_TiviMate.m3u8"
STREAM_RE = re.compile(r"https?://[^\s'\"<>]+\.m3u8(?:[^\s'\"<>]*)", re.IGNORECASE)
# detect base64-ish tokens (common long base64 strings)
BASE64_TOKEN_RE = re.compile(r'["\']([A-Za-z0-9+/=]{32,})["\']')
# detect reversed-base64 assignment like var encoded = "...."; ... atob(encoded.split("").reverse().join(""))
REVERSED_ASSIGN_RE = re.compile(r'var\s+encoded\s*=\s*["\']([A-Za-z0-9+/=]+)["\']', re.IGNORECASE)
REVERSED_ATOB_PATTERN = re.compile(r'atob\s*\(\s*encoded\.split\(\s*""\s*\)\.reverse\(\)\.join\(\s*""\s*\)\s*\)', re.IGNORECASE)

# Target / seed pages
SEED_URLS = [
    "https://mlswebcast.com/",
    # add any additional league homepages here
]

# Optional tvg metadata defaults
DEFAULT_TVG_LOGO = "https://i.postimg.cc/vTYqKdKN/soccer-logo-png-seeklogo-380207.png"
DEFAULT_TVG_ID_PREFIX = "MLS.Soccer.Dummy.us"

# timeouts
PAGE_GOTO_TIMEOUT = 35000
NETWORK_IDLE_TIMEOUT = 10000
PLAY_TRIGGER_TIMEOUT = 6  # seconds to wait after clicking play


# -------------------------
# Utilities
# -------------------------
def normalize_name(s: str) -> str:
    if not s:
        return ""
    txt = " ".join(s.splitlines()).strip()
    txt = re.sub(r"\s+", " ", txt)
    # keep case as-is but trim
    return txt.strip()


def derive_tvg_id(name: str) -> str:
    n = re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower()
    return (DEFAULT_TVG_ID_PREFIX + "." + n)[:64]


async def verify_m3u8(session: aiohttp.ClientSession, url: str, referer: Optional[str] = None, origin: Optional[str] = None) -> bool:
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    try:
        # use HEAD first (some servers block HEAD); fallback to GET
        async with session.head(url, timeout=10, headers=headers, allow_redirects=True) as r:
            if r.status == 200:
                return True
        async with session.get(url, timeout=10, headers=headers, allow_redirects=True) as r:
            return r.status == 200
    except Exception:
        return False


def extract_m3u8_from_html(html: str, page_url: str) -> List[str]:
    """
    Heuristics to extract m3u8 URLs or encoded base64 strings that deobfuscate to m3u8.
    Returns possible absolute URLs (may be relative — we join later).
    """
    found = []
    # direct matches
    for m in STREAM_RE.finditer(html):
        found.append(m.group(0))

    # direct base64-ish tokens in scripts - try decode
    for m in BASE64_TOKEN_RE.finditer(html):
        token = m.group(1)
        try:
            decoded = base64.b64decode(token + "===").decode("utf-8", errors="ignore")
            for mm in STREAM_RE.finditer(decoded):
                found.append(mm.group(0))
        except Exception:
            pass

    # reversed-base64 pattern (common obfuscation: encoded string reversed before atob)
    # detect var encoded = "...."; and atob(encoded.split("").reverse()... )
    if REVERSED_ASSIGN_RE.search(html) and REVERSED_ATOB_PATTERN.search(html):
        enc = REVERSED_ASSIGN_RE.search(html).group(1)
        # sometimes the script already placed reversed string: try reversing then decoding
        try:
            reversed_enc = enc[::-1]
            dec = base64.b64decode(reversed_enc + "===").decode("utf-8", errors="ignore")
            for mm in STREAM_RE.finditer(dec):
                found.append(mm.group(0))
        except Exception:
            pass

    # search for patterns like atob("base64...") directly
    for m in re.finditer(r'atob\(\s*["\']([A-Za-z0-9+/=]{24,})["\']\s*\)', html):
        token = m.group(1)
        try:
            dec = base64.b64decode(token + "===").decode("utf-8", errors="ignore")
            for mm in STREAM_RE.finditer(dec):
                found.append(mm.group(0))
        except Exception:
            pass

    # also look for JSON-LD that might contain embed or player sources
    try:
        soups = BeautifulSoup(html, "html.parser")
        for script in soups.find_all("script", {"type": "application/ld+json"}):
            try:
                j = json.loads(script.string or "{}")
                js = json.dumps(j)
                for mm in STREAM_RE.finditer(js):
                    found.append(mm.group(0))
            except Exception:
                continue
    except Exception:
        pass

    # dedupe and return
    return list(dict.fromkeys(found))


# -------------------------
# Core: scraping & capture
# -------------------------
async def discover_event_pages_from_home(session: aiohttp.ClientSession, seed_url: str) -> List[Tuple[str, str, Optional[str]]]:
    """
    Returns list of tuples: (event_page_url, event_name, logo_url)
    Heuristics:
     - look for .card elements and 'card-text' p elements (site structure provided by user)
     - look for anchors with hrefs to '/inter-' or '/game' etc.
    """
    print(f"🔍 Fetching homepage: {seed_url}")
    try:
        async with session.get(seed_url, timeout=20, headers={"User-Agent": USER_AGENT}) as r:
            html = await r.text()
    except Exception as e:
        print(f"❌ Failed to fetch {seed_url}: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Find card-based structure: <p class="card-text">Event Name</p> and a.btn-primary href
    cards = soup.select(".card")
    if cards:
        for c in cards:
            a = c.select_one("a.btn-primary") or c.select_one("a[href]")
            name_el = c.select_one(".card-text") or c.select_one(".card-title") or c.select_one("h5")
            logo_el = c.select_one("img")
            if a and name_el:
                href = a.get("href")
                name = normalize_name(name_el.get_text())
                logo = logo_el.get("src") if logo_el and logo_el.get("src") else None
                if href:
                    results.append((urljoin(seed_url, href), name, urljoin(seed_url, logo) if logo else None))

    # Fallback: anchors that look like iframe pages
    anchors = soup.select("a[href]")
    for a in anchors:
        href = a.get("href")
        if href and any(x in href for x in ["/iframe/", "/game/", "/inter-", "/live/"]):
            text = normalize_name(a.get_text() or a.get("title") or "")
            if text == "":
                # Try to find neighbouring p.card-text
                parent = a.find_parent()
                p = parent.select_one(".card-text") if parent else None
                text = normalize_name(p.get_text()) if p else href.split("/")[-1]
            results.append((urljoin(seed_url, href), text, None))

    # dedupe by url (keep first encountered name/logo)
    seen = {}
    deduped = []
    for url_, name_, logo_ in results:
        if url_ not in seen:
            seen[url_] = (name_, logo_)
            deduped.append((url_, name_, logo_))
    print(f"📌 Found {len(deduped)} event page(s) from homepage.")
    return deduped


async def capture_stream_from_page(context, session: aiohttp.ClientSession, page_url: str, referer: Optional[str]) -> Optional[str]:
    """
    Use multiple strategies to capture playable m3u8:
    1) fetch page HTML and look for m3u8 / encoded tokens
    2) open page using Playwright, attach request listeners, attempt clicks on player
    3) inspect iframes and their inner HTML too
    Return first verified m3u8 URL or None.
    """
    # 1) quick HTML scan
    try:
        async with session.get(page_url, timeout=15, headers={"User-Agent": USER_AGENT, "Referer": referer or ""}) as r:
            html = await r.text()
    except Exception:
        html = ""

    found = extract_m3u8_from_html(html, page_url)
    # resolve relative urls, verify
    for u in found:
        full = urljoin(page_url, u)
        if await verify_m3u8(session, full, referer=referer, origin=urlparse(page_url).scheme + "://" + urlparse(page_url).netloc):
            return full

    # 2) Playwright capture (network requests)
    async def _playwright_capture():
        async with async_playwright() as p:
            # prefer chromium for best compatibility
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = await browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()
            candidate_urls: List[str] = []

            def on_request(req):
                url = req.url
                if ".m3u8" in url and url not in candidate_urls:
                    candidate_urls.append(url)
                    print(f" ↳ network captured candidate: {url}")

            page.on("request", on_request)
            try:
                print(f" ↳ Playwright navigating to {page_url}")
                try:
                    await page.goto(page_url, wait_until="domcontentloaded", timeout=PAGE_GOTO_TIMEOUT)
                except PlaywrightTimeoutError:
                    print(" ⚠️ Playwright goto timeout, continuing")

                # wait for some network idleness (best-effort)
                try:
                    await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
                except Exception:
                    pass

                # Attempt to click common play buttons and player container to trigger streams
                play_selectors = [
                    "button.play", ".play-button", ".vjs-big-play-button", "#player", ".player", ".clappr", ".plyr__controls",
                    "div[id^=player]", "iframe"
                ]
                for sel in play_selectors:
                    try:
                        els = await page.locator(sel).all()
                        if not els:
                            continue
                        # click the first one
                        try:
                            await els[0].click(timeout=2000, force=True)
                            await asyncio.sleep(PLAY_TRIGGER_TIMEOUT)
                            # after clicking wait a bit for requests
                            try:
                                await page.wait_for_load_state("networkidle", timeout=5000)
                            except Exception:
                                pass
                        except Exception:
                            # try JS click fallback
                            try:
                                await page.evaluate(
                                    "(sel)=>{const el=document.querySelector(sel); if(el){ el.click(); return true } return false }",
                                    sel
                                )
                                await asyncio.sleep(PLAY_TRIGGER_TIMEOUT)
                            except Exception:
                                pass
                    except Exception:
                        pass

                # Inspect iframes and click inside them where possible
                frames = page.frames
                for fr in frames:
                    try:
                        # skip main frame
                        if fr == page.main_frame:
                            continue
                        # try to evaluate scripts in frame to reveal encoded urls
                        try:
                            content = await fr.content()
                            for u in extract_m3u8_from_html(content, page_url):
                                if u not in candidate_urls:
                                    candidate_urls.append(urljoin(page_url, u))
                        except Exception:
                            pass
                        # attempt click in iframe
                        try:
                            await fr.evaluate("() => { const b = document.querySelector('button.play') || document.querySelector('.play-button') || document.querySelector('#player'); if(b){ b.click(); return true } return false }")
                            await asyncio.sleep(PLAY_TRIGGER_TIMEOUT)
                        except Exception:
                            pass
                    except Exception:
                        pass

                # after interaction, check captured candidate_urls
                # consider reversed order to prefer newest
                for cand in reversed(candidate_urls):
                    full = urljoin(page_url, cand)
                    if await verify_m3u8(session, full, referer=referer, origin=urlparse(page_url).scheme + "://" + urlparse(page_url).netloc):
                        return full

                # final fallback: scan page.content() for any remaining encoded tokens
                page_html = await page.content()
                for u in extract_m3u8_from_html(page_html, page_url):
                    full = urljoin(page_url, u)
                    if await verify_m3u8(session, full, referer=referer, origin=urlparse(page_url).scheme + "://" + urlparse(page_url).netloc):
                        return full

            finally:
                try:
                    page.remove_listener("request", on_request)
                except Exception:
                    pass
                try:
                    await context.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass
        return None

    # execute playwright capture
    try:
        cand = await _playwright_capture()
        if cand:
            return cand
    except Exception as e:
        print(f" ⚠️ Playwright capture error: {e}")

    return None


# -------------------------
# Orchestration
# -------------------------
async def main():
    print("🚀 Starting MLS Webcast scraper (rebuilt)...")
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        # aggregate results
        results = []

        for seed in SEED_URLS:
            event_pages = await discover_event_pages_from_home(session, seed)
            for page_url, pretty_name, logo in event_pages:
                print(f"🔎 Processing event: {pretty_name} -> {page_url}")
                try:
                    m3u8 = await capture_stream_from_page(None, session, page_url, referer=seed)
                    if m3u8:
                        print(f"✅ Found m3u8 for {pretty_name}: {m3u8}")
                        results.append({
                            "name": pretty_name,
                            "url": m3u8,
                            "logo": logo or DEFAULT_TVG_LOGO,
                            "tvg_id": derive_tvg_id(pretty_name),
                            "group": "MLSWebcast - Live Games",
                            "ref": page_url,
                        })
                    else:
                        print(f"⚠️ No m3u8 found for {page_url}")
                except Exception as e:
                    print(f"⚠️ Error processing {page_url}: {e}")

        # Save playlist
        if not results:
            print("❌ No streams captured.")
            return

        with open(OUTPUT_FILE, "w", encoding="utf-8") as wf:
            wf.write("#EXTM3U\n")
            for e in results:
                wf.write(f'#EXTINF:-1 tvg-id="{e["tvg_id"]}" tvg-name="{e["name"]}" tvg-logo="{e["logo"]}" group-title="{e["group"]}",{e["name"]}\n')
                # append headers in-line for some players (TiviMate supports | options)
                ua = quote(USER_AGENT, safe="")
                ref = e.get("ref", "")
                origin = urlparse(ref).scheme + "://" + urlparse(ref).netloc if ref else ""
                wf.write(f'{e["url"]}|Referer={ref}|Origin={origin}|User-Agent={ua}\n')
        print(f"✅ Playlist written to {OUTPUT_FILE} ({len(results)} streams).")


if __name__ == "__main__":
    asyncio.run(main())
