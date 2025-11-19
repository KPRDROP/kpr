#!/usr/bin/env python3
"""
nflwebcast.py
Production-ready scraper for https://nflwebcast.com using Playwright (ms-playwright
GitHub Action environment). Captures playable .m3u8 or .ts streams, validates them,
and writes a TiviMate-compatible M3U playlist.

Usage:
  python nflwebcast.py

Notes:
- This script assumes it's run where Playwright browsers and deps are installed
  (e.g. microsoft/playwright-github-action or you've run `playwright install`).
- It runs Chromium in headful mode which improves chance to pass Cloudflare JS checks.
"""

import asyncio
import re
import sys
import time
import json
from typing import List, Optional, Set, Dict
from urllib.parse import urljoin, urlparse, quote

import aiohttp
import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# -----------------------
# Config
# -----------------------
START_URL = "https://nflwebcast.com/"
LISTING_PATHS = ["/sbl/", "/"]   # try /sbl/ then root
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ENCODED_UA = quote(USER_AGENT, safe="")
OUTPUT_FILE = "NFLWebcast.m3u8"

MAX_NAV_ATTEMPTS = 4
PAGE_TIMEOUT = 45_000        # ms
NETWORK_IDLE_TIMEOUT = 5_000 # ms
CLICK_WAIT = 2.0            # seconds after clicking for network calls to trigger
CAPTURE_SECONDS = 8         # seconds to sniff after play
CONCURRENT_PAGES = 3
VALIDATE_TIMEOUT = 8        # seconds for aiohttp validation

# Headers used when validating streams
VALIDATION_HEADERS = {"User-Agent": USER_AGENT, "Referer": START_URL, "Origin": START_URL.rstrip("/")}

# Patterns
M3U8_RE = re.compile(r"https?://[^\s'\"<>]+\.m3u8(?:\?[^'\"\s<>]*)?", re.IGNORECASE)
TS_RE = re.compile(r"https?://[^\s'\"<>]+\.ts(?:\?[^'\"\s<>]*)?", re.IGNORECASE)

# Play selectors to attempt clicking
PLAY_SELECTORS = [
    "button[class*=play]", ".vjs-big-play-button", ".jw-display-icon-display", "button.play",
    "div.play", "div[class*='play']", ".plyr__control", "video"
]

# -----------------------
# Helpers
# -----------------------

def log(*args, **kwargs):
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    print(now, "|", *args, **kwargs)

def safe_hostname(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""

async def validate_url_ok(url: str, session: aiohttp.ClientSession, timeout: int = VALIDATE_TIMEOUT) -> bool:
    """Make a HEAD/GET to validate stream (prefer HEAD then GET)."""
    try:
        # Try HEAD first
        async with session.head(url, timeout=timeout, allow_redirects=True) as resp:
            if resp.status == 200:
                return True
            # Some servers don't accept HEAD; fallthrough to GET
    except Exception:
        pass
    try:
        async with session.get(url, timeout=timeout, allow_redirects=True) as resp:
            return resp.status == 200
    except Exception:
        return False

# -----------------------
# Playwright helpers
# -----------------------

async def robust_goto(page, url: str, attempts: int = MAX_NAV_ATTEMPTS, timeout_ms: int = PAGE_TIMEOUT) -> bool:
    """Try navigating multiple times; detect Cloudflare/JS-challenge pages and wait."""
    for i in range(1, attempts + 1):
        try:
            log(f"→ goto {url} (attempt {i}/{attempts})")
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            # Wait short for network to settle
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT)
            except Exception:
                # networkidle may fail; still continue
                pass

            content = await page.content()
            # detect common challenge signs (Cloudflare IUAM or "Checking your browser")
            lower = content.lower()
            if ("checking your browser" in lower or "cloudflare" in lower or "please enable javascript" in lower or "verify you are human" in lower):
                log("  ⏳ Cloudflare/challenge detected; waiting and retrying")
                await asyncio.sleep(2 + i)
                continue
            # Looks okay
            return True
        except PlaywrightTimeout:
            log(f"  ⏳ Timeout when loading {url}")
            await asyncio.sleep(1 + i)
            continue
        except Exception as e:
            log(f"  ⚠ error loading {url}: {e}")
            await asyncio.sleep(1 + i)
            continue
    return False

async def momentum_click_sequence(page):
    """Perform the two-click momentum sequence; attempt to detect and close ad tab."""
    try:
        await page.mouse.click(200, 200)  # first click - may open ads / popup
        log("  👆 First click")
        pages_before = list(page.context.pages)
        new_tab = None
        for _ in range(12):
            pages_now = list(page.context.pages)
            if len(pages_now) > len(pages_before):
                new_tab = [p for p in pages_now if p not in pages_before][0]
                break
            await asyncio.sleep(0.25)
        if new_tab:
            try:
                await asyncio.sleep(0.5)
                new_url = new_tab.url or ""
                log(f"  🚫 Found ad tab: {new_url}; closing")
                await new_tab.close()
            except Exception:
                log("  ⚠ failed to close ad tab")
        await asyncio.sleep(0.8)
        await page.mouse.click(200, 200)  # second click - hopefully starts player
        log("  ▶️ Second click")
    except Exception as e:
        log("  ⚠ Momentum click failed:", e)

async def try_click_play_selectors(page):
    """Try several selectors to start playback."""
    for sel in PLAY_SELECTORS:
        try:
            # query_selector may throw in some cases
            el = await page.query_selector(sel)
            if el:
                try:
                    await el.click(timeout=2000)
                    log(f"  ▶ clicked play selector {sel}")
                    return True
                except Exception:
                    # fallback to mouse click
                    pass
        except Exception:
            pass
    # fallback: try a center mouse click
    try:
        await page.mouse.click(200, 200)
        return True
    except Exception:
        return False

async def extract_from_clappr(page) -> List[str]:
    """Try to evaluate Clappr / jwplayer / window.player sources inside page."""
    out = []
    try:
        js = """
() => {
  try {
    const out = [];
    // Clappr global players
    if (window.Clappr && window.Clappr._players) {
      for (const p of Object.values(window.Clappr._players)) {
        try {
          const src = p.core && p.core.activePlayback && p.core.activePlayback.options && p.core.activePlayback.options.src;
          if (src) out.push(src);
        } catch(e){}
      }
    }
    // jwplayer / video.js / player objects
    try {
      if (window.jwplayer) {
         const pl = window.jwplayer();
         if (pl && pl.getPlaylist) {
            const list = pl.getPlaylist();
            if (Array.isArray(list) && list.length) {
              out.push(list[0].file || (list[0].sources && list[0].sources[0] && list[0].sources[0].file));
            }
         }
      }
    } catch(e){}
    if (window.player && window.player.getPlaylist) {
      try {
        const p = window.player.getPlaylist();
        if (p && p.length) out.push(p[0].file || p[0].sources && p[0].sources[0].file);
      } catch(e){}
    }
    return out;
  } catch (e) {
    return [];
  }
}
"""
        res = await page.evaluate(js)
        if isinstance(res, list):
            for s in res:
                if isinstance(s, str) and s:
                    out.append(s)
    except Exception:
        pass
    return out

# -----------------------
# Page scraping logic
# -----------------------

async def sniff_streams_from_page(page, base_url: str, capture_seconds: int = CAPTURE_SECONDS) -> Dict[str, Set[str]]:
    """
    Attach listeners and try to trigger the player. Return dict:
      { "m3u8": set(...), "ts": set(...) }
    """
    found = {"m3u8": set(), "ts": set()}
    def on_response(response):
        try:
            url = response.url
            if not url:
                return
            lower = url.lower()
            if ".m3u8" in lower:
                found["m3u8"].add(url)
            if lower.endswith(".ts") or ".ts?" in lower:
                found["ts"].add(url)
        except Exception:
            pass

    # register listener
    page.on("response", on_response)

    # initial sniff pass: try to click play etc
    await momentum_click_sequence(page)

    # try selectors
    await try_click_play_selectors(page)
    # short wait to allow requests to flow
    await asyncio.sleep(0.6)
    # attempt Clappr/JW extraction
    try:
        clappr = await extract_from_clappr(page)
        for s in clappr:
            if ".m3u8" in s:
                found["m3u8"].add(s)
    except Exception:
        pass

    # give it some time to load requests
    total_wait = 0.0
    while total_wait < capture_seconds:
        await asyncio.sleep(0.5)
        total_wait += 0.5

    # remove listener
    try:
        page.remove_listener("response", on_response)
    except Exception:
        # safe to ignore if already removed or event not present
        pass

    return found

async def page_extract_candidates(context, target_url: str) -> List[str]:
    """Open page, robust goto, attempt to sniff streams and extract candidate m3u8 URLs and iframe srcs."""
    page = await context.new_page()
    page.set_default_navigation_timeout(PAGE_TIMEOUT)
    candidates = []
    try:
        ok = await robust_goto(page, target_url)
        if not ok:
            log(f"  ✖ couldn't load {target_url}")
            await page.close()
            return []

        # sniff requests for m3u8 and ts
        sniffed = await sniff_streams_from_page(page, target_url)
        # include sniffed directly
        candidates.extend(list(sniffed["m3u8"]))
        # check for m3u8/ts in page HTML
        html = await page.content()
        for m in M3U8_RE.findall(html):
            candidates.append(m)
        # check if there are iframes and fetch their src
        try:
            iframe_handles = await page.query_selector_all("iframe")
            for ifh in iframe_handles:
                src = await ifh.get_attribute("src")
                if src:
                    src_full = urljoin(target_url, src)
                    candidates.append(src_full)
        except Exception:
            pass

        # Clappr/jwplayer sources already attempted inside sniff
        # final dedupe
        candidates = list(dict.fromkeys([c for c in candidates if c and c.startswith("http")]))
        return candidates
    finally:
        try:
            await page.close()
        except Exception:
            pass

# -----------------------
# Top-level
# -----------------------

async def find_event_links(context) -> List[Dict]:
    """
    Extract event links from the site listing(s). Return list of dicts:
      [{ "title": "...", "url": "...", "logo": "..." }, ...]
    Tries a few selectors and falls back to simple <a> harvest.
    """
    results = []
    page = await context.new_page()
    page.set_default_navigation_timeout(PAGE_TIMEOUT)
    try:
        # attempt listing paths (sbl etc)
        for path in LISTING_PATHS:
            listing_url = urljoin(START_URL, path)
            ok = await robust_goto(page, listing_url)
            if not ok:
                log(f"  ⚠ listing {listing_url} not available or blocked")
                continue

            # try common selectors used at nflwebcast
            try:
                # look for anchor elements under match lists
                anchors = await page.query_selector_all("a.dracula-style-link, .team a, a[href*='live-stream']")
                for a in anchors:
                    href = await a.get_attribute("href")
                    text = (await a.inner_text() or "").strip()
                    img = None
                    try:
                        img_el = await a.query_selector("img")
                        if img_el:
                            img = await img_el.get_attribute("src")
                    except Exception:
                        img = None
                    if href and href.startswith("http"):
                        results.append({"title": text or href, "url": href, "logo": img})
                # fallback selectors specific to site (team links)
                anchors2 = await page.query_selector_all("a:has-text('@'), a:has-text('Live Stream'), a[href*='live-stream-online']")
                for a in anchors2:
                    href = await a.get_attribute("href")
                    text = (await a.inner_text() or "").strip()
                    if href and href.startswith("http"):
                        results.append({"title": text or href, "url": href, "logo": None})
            except Exception:
                pass

            if results:
                break

        # if no results from Playwright DOM (maybe challenge prevented DOM), fallback to HTTP GET parsing
        if not results:
            try:
                log("  ℹ Playwright DOM extraction empty — trying HTTP fallback parse")
                r = requests.get(urljoin(START_URL, "/sbl/"), headers={"User-Agent": USER_AGENT}, timeout=12)
                if r.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r.text, "html.parser")
                    for a in soup.find_all("a"):
                        href = a.get("href")
                        txt = (a.get_text() or "").strip()
                        if href and href.startswith("http") and ("live-stream" in href or "/sbl/" in href or href.count("/") > 4):
                            results.append({"title": txt or href, "url": href, "logo": None})
            except Exception as e:
                log("  ⚠ HTTP fallback failed:", e)

        # dedupe by url
        seen = set()
        deduped = []
        for r in results:
            url = r.get("url")
            if not url:
                continue
            if url in seen:
                continue
            seen.add(url)
            deduped.append(r)
        return deduped
    finally:
        try:
            await page.close()
        except Exception:
            pass

async def validate_and_choose(candidate_list: List[str], session: aiohttp.ClientSession) -> Optional[str]:
    """
    Validate list of candidates: prefer tokenized .m3u8 (s= & e=), prefer same ts-host anchoring if multiple,
    and return first validated (200) stream.
    """
    if not candidate_list:
        return None
    # dedupe
    candidates = list(dict.fromkeys(candidate_list))
    # rank: tokenized first
    def score(u: str) -> int:
        s = 0
        if u.startswith("https://"):
            s += 10
        qs = urlparse(u).query
        if "s=" in qs and ("e=" in qs or "exp=" in qs):
            s += 50
            s += len(qs) // 10
        if ".m3u8" in u:
            s += 5
        return s
    candidates_sorted = sorted(candidates, key=score, reverse=True)

    # Validate
    for u in candidates_sorted:
        try:
            ok = await validate_url_ok(u, session)
            log(f"🔹 Validated {u}: {ok}")
            if ok:
                return u
        except Exception:
            continue
    # last resort: return first candidate (even if validation failed)
    return None

async def process_event_entry(context, session: aiohttp.ClientSession, event: Dict) -> Optional[Dict]:
    """
    For an event entry (title,url,logo), open the page and try to gather playable stream.
    Returns: {title, url, final_stream, logo} or None
    """
    title = event.get("title") or event.get("url")
    page_url = event.get("url")
    logo = event.get("logo")
    log(f"\nScraping {title} | {page_url}")
    try:
        candidates = []
        # 1) direct sniff on page (open page and sniff requests)
        try:
            candidates = await page_extract_candidates(context, page_url)
            log(f"  ℹ initial candidates: {len(candidates)}")
        except Exception as e:
            log("  ⚠ error sniffing page:", e)
            candidates = []

        # 2) If page had iframes, also visit iframe srcs
        iframe_srcs = [c for c in candidates if "iframe" in c or ".php" in c or "embed" in c]
        for src in iframe_srcs:
            try:
                subc = await page_extract_candidates(context, src)
                candidates.extend(subc)
            except Exception:
                pass

        # 3) also attempt to fetch page HTML and extract .m3u8 occurrences (fallback)
        try:
            r = requests.get(page_url, headers={"User-Agent": USER_AGENT}, timeout=10)
            if r.status_code == 200:
                for m in M3U8_RE.findall(r.text):
                    candidates.append(m)
        except Exception:
            pass

        # dedupe and keep HTTP(s)
        candidates = [c for c in dict.fromkeys(candidates) if c and c.startswith("http")]
        if not candidates:
            log("  ⚠ No candidates found for event")
            return None

        # Validate candidates and pick first valid
        final = await validate_and_choose(candidates, session)
        if final:
            log(f"  ✅ Final stream for {title}: {final}")
            return {"title": title.strip(), "page": page_url, "stream": final, "logo": logo}
        else:
            log("  ❌ No validated stream for event after checks")
            return None
    except Exception as e:
        log("  ✖ processing failed:", e)
        return None

# -----------------------
# Main orchestration
# -----------------------

async def main():
    start_time = time.time()
    log("🚀 Starting NFLWebcast scraper (Playwright sniff + aiohttp validation)")

    results = []

    async with async_playwright() as pw:
        # launch Chromium headful (ms-playwright GH action provides the browsers)
        browser = await pw.chromium.launch(headless=False, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=USER_AGENT, java_script_enabled=True)
        # reduce concurrency pressure
        sem = asyncio.Semaphore(CONCURRENT_PAGES)

        try:
            # find events from listing
            events = await find_event_links(context)
            log(f"ℹ candidate event links: {len(events)}")

            if not events:
                # If none found, try explicit listing path(s) with HTTP fallback
                log("ℹ Attempting explicit listing fallback via requests")
                for p in LISTING_PATHS:
                    url = urljoin(START_URL, p)
                    try:
                        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
                        if r.status_code == 200:
                            for m in re.findall(r'href=["\'](https?://[^\']+)["\']', r.text):
                                if "live-stream" in m or "/sbl/" in m or m.count("/") > 4:
                                    events.append({"title": m, "url": m, "logo": None})
                    except Exception:
                        pass
                log(f"ℹ fallback candidates: {len(events)}")

            async def worker(ev):
                async with sem:
                    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
                        return await process_event_entry(context, session, ev)

            # run sequentially to be gentle (site is small). You can increase concurrency if desired.
            for ev in events:
                res = await worker(ev)
                if res:
                    results.append(res)

        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    # Write playlist (TiviMate pipe headers)
    if results:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for r in results:
                title = r["title"]
                stream = r["stream"]
                logo = r.get("logo") or ""
                headers = f"referer={START_URL.rstrip('/')}/|origin={START_URL.rstrip('/')}|user-agent={ENCODED_UA}"
                f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="NFLWebcast",{title}\n')
                f.write(f'{stream}|{headers}\n')
        log(f"✅ Playlist written: {OUTPUT_FILE} | streams: {len(results)} | time: {time.time()-start_time:.1f}s")
    else:
        # ensure file exists (empty playlist)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
        log(f"✅ Playlist written: {OUTPUT_FILE} | streams: 0 | time: {time.time()-start_time:.1f}s")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Interrupted")
        sys.exit(1)
