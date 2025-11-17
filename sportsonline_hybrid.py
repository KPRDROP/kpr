#!/usr/bin/env python3
"""
sportsonline_hybrid.py
Hybrid scraper (C):
 - parses https://sportsonline.sn/prog.txt (schedule)
 - maps events -> channel embed URLs (sportzonline.live)
 - opens embed pages with Playwright (chromium)
 - momentum double-click, safe ad-tab closing
 - captures .m3u8 and .ts requests (response+request listeners)
 - prefers tokenized m3u8s, prefers ts-host anchoring
 - validates m3u8 via aiohttp before accepting
 - produces multiple playlists: master, per-category (VLC + TiviMate)
"""

from __future__ import annotations
import asyncio
import re
import requests
import logging
from datetime import datetime
from urllib.parse import quote, urlparse, urlunparse
from collections import defaultdict, Counter
from typing import List, Tuple, Optional, Dict, Set
import aiohttp
import os
import sys

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ------------------------
# CONFIG
# ------------------------
SCHEDULE_URL = "https://sportsonline.sn/prog.txt"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

# Playlists filenames
OUT_MASTER = "Sportsonline_master.m3u8"
OUT_TIVI_MASTER = "Sportsonline_master_tivimate.m3u8"
OUT_DIR = "."  # output dir for per-category files

# VLC headers used for players that support EXTVLCOPT
VLC_HEADERS = [
    f'#EXTVLCOPT:http-user-agent={USER_AGENT}',
    '#EXTVLCOPT:http-referrer=https://dukehorror.net/'
]

# TiviMate: encoded UA will be appended behind '|' when writing TiviMate entries

# Category mapping: key words -> category slug
CATEGORY_KEYWORDS = {
    "NBA": "basketball",
    "UFC": "combat sports",
    "NFL": "american football",
    "TENNIS": "tennis",
    "CRICKET": "cricket",
    "RUGBY": "rugby",
    "MOTOR": "motorsports",
    "DART": "darts",
    "BOXING": "boxing",
    "SOCCER": "football",
    "FOOTBALL": "football",
    "BASKETBALL": "basketball",
    # add more as needed
}

# Fallback logos and TV IDs (expand as you like)
FALLBACK_LOGOS = {
    "football": "https://i.postimg.cc/FKq4YrPT/Rv-N0XSF.png",
    "basketball": "https://i.postimg.cc/FHBqZPjF/Basketball5.png",
    "combat sports": "https://i.postimg.cc/1Xr2rsKc/Combat-Sports.png",
    "american football": "https://i.postimg.cc/8P8zyHmf/Am-Football2.png",
    "miscellaneous": "https://i.postimg.cc/1Xr2rsKc/Combat-Sports.png",
}

TV_IDS = {k: f"{k.replace(' ', '.')}.Dummy.us" for k in FALLBACK_LOGOS.keys()}

# concurrency + timeouts
CONCURRENT_FETCHES = 6
NAV_TIMEOUT_MS = 60_000
RETRIES = 3
CLICK_WAIT = 3.0
VALIDATE_TIMEOUT = 10  # seconds for aiohttp validation
PAGES_NEW_TAB_WAIT = 0.25

# logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sportsonline_hybrid")

# ------------------------
# HELPERS
# ------------------------
def strip_non_ascii(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"[^\x00-\x7F]+", "", s).strip()

def parse_hostname(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""

def replace_hostname(original_url: str, new_hostname: str) -> str:
    try:
        p = urlparse(original_url)
        new_netloc = new_hostname
        if p.port:
            new_netloc = f"{new_hostname}:{p.port}"
        return urlunparse(p._replace(netloc=new_netloc))
    except Exception:
        return original_url

def has_token(url: str) -> bool:
    return ("?s=" in url) and ("&e=" in url or "&exp=" in url)

async def http_ok(url: str, session: aiohttp.ClientSession, timeout: int = VALIDATE_TIMEOUT) -> bool:
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT, "Referer": "https://dukehorror.net/"}, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False

# ------------------------
# SCHEDULE PARSING (exact for the provided prog.txt)
# ------------------------
def fetch_schedule_text() -> str:
    try:
        log.info("Fetching schedule from %s", SCHEDULE_URL)
        r = requests.get(SCHEDULE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.error("Failed to fetch schedule: %s", e)
        return ""

def parse_schedule(raw: str) -> List[Dict]:
    """
    Parse lines like:
    "15:00   Egypt x Algeria | https://sportzonline.live/channels/hd/hd11.php"
    Returns list of dicts: {time, title, link}
    """
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # skip headers and long blocks of text
        if line.startswith("=") or line.upper().startswith("INFO:") or "IMPORTANT" in line.upper():
            continue
        # common pattern: TIME  <spaces>  TITLE  |  URL
        m = re.match(r"^(\d{1,2}:\d{2})\s+(.*?)\s*\|\s*(https?://\S+)$", line)
        if m:
            t, title, link = m.group(1), m.group(2), m.group(3)
            events.append({"time": t, "title": strip_non_ascii(title), "link": link})
            continue
        # alternate space-separated last token is url
        m2 = re.match(r"^(\d{1,2}:\d{2})\s+(.*?)\s+(https?://\S+)$", line)
        if m2:
            t, title, link = m2.group(1), m2.group(2), m2.group(3)
            events.append({"time": t, "title": strip_non_ascii(title), "link": link})
            continue
        # ignore others
    log.info("Parsed %d event lines", len(events))
    return events

# ------------------------
# EXTRACTION: open embed page -> trigger -> capture m3u8/.ts
# ------------------------
async def extract_stream_from_embed(page, embed_url: str) -> Tuple[Optional[str], Set[str], Set[str]]:
    """
    Returns: (best_validated_m3u8_or_none, found_m3u8_set, found_ts_set)
    """
    found_m3u8: Set[str] = set()
    found_ts: Set[str] = set()
    main_page = page

    def handle_response(resp):
        try:
            u = resp.url
            if not u:
                return
            if ".m3u8" in u:
                found_m3u8.add(u)
            if u.endswith(".ts"):
                found_ts.add(u)
        except Exception:
            pass

    page.on("response", handle_response)

    for attempt in range(1, RETRIES + 1):
        try:
            log.debug("Loading %s (attempt %d)", embed_url, attempt)
            await page.goto(embed_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")

            # Momentum double-click sequence with safe popup handling
            try:
                await page.mouse.click(200, 200)
                pages_before = list(page.context.pages)
                new_tab = None
                for _ in range(12):
                    pages_now = list(page.context.pages)
                    # prefer a page that is not the main page
                    for p in pages_now:
                        if p is not main_page and p not in pages_before:
                            new_tab = p
                            break
                    if new_tab:
                        break
                    await asyncio.sleep(PAGES_NEW_TAB_WAIT)
                if new_tab:
                    try:
                        new_url = new_tab.url or "(blank)"
                        log.info("  closing ad popup: %s", new_url)
                        await new_tab.close()
                    except Exception:
                        log.debug("  failed to close ad tab")
                await asyncio.sleep(0.6)
                await page.mouse.click(200, 200)
                log.debug("  momentum clicks done")
            except Exception as e:
                log.debug("momentum clicks error: %s", e)

            # try clicking common play buttons (best-effort)
            play_selectors = [
                "div.jw-icon-display[role='button']",
                ".jw-icon-playback",
                ".vjs-big-play-button",
                ".plyr__control",
                "div[class*='play']",
                "button",
                "canvas"
            ]
            for sel in play_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click(timeout=400)
                        await asyncio.sleep(0.2)
                except Exception:
                    pass

            # allow network to produce requests
            await asyncio.sleep(CLICK_WAIT)

            # if found something, break early
            if found_m3u8 or found_ts:
                break
        except PlaywrightTimeout:
            log.warning("Timeout loading embed: %s (attempt %d)", embed_url, attempt)
            await asyncio.sleep(0.5 * attempt)
        except Exception as e:
            log.warning("Error loading embed %s: %s", embed_url, e)
            await asyncio.sleep(0.5 * attempt)

    # collect page HTML fallback
    try:
        html = await page.content()
        # find m3u8 urls in HTML as fallback
        matches = re.findall(r"https?://[^\s\"'<>]+\.m3u8(?:\?[^\"'<>]*)?", html)
        for m in matches:
            found_m3u8.add(m)
        ts_matches = re.findall(r"https?://[^\s\"'<>]+\.ts(?:\?[^\"'<>]*)?", html)
        for m in ts_matches:
            found_ts.add(m)
        if matches:
            log.debug("HTML fallback found m3u8s: %s", matches[:3])
    except Exception:
        pass

    # stop listener, give slight time for inflight to settle
    page.remove_listener("response", handle_response)
    await asyncio.sleep(0.15)

    # scoring & preferences
    tokenized = [u for u in found_m3u8 if has_token(u)]
    candidates = tokenized if tokenized else list(found_m3u8)

    ts_hosts = [parse_hostname(u) for u in found_ts if parse_hostname(u)]
    preferred_host = None
    if ts_hosts:
        preferred_host = Counter(ts_hosts).most_common(1)[0][0]

    def score(u):
        s = 0
        if u.startswith("https://"): s += 10
        if has_token(u): s += 30
        if preferred_host and parse_hostname(u) == preferred_host: s += 50
        if "?" in u:
            s += len(u.split("?", 1)[1]) // 15
        return s

    scored = sorted(candidates, key=lambda x: score(x), reverse=True)

    # validation
    validated = None
    if scored:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=VALIDATE_TIMEOUT)) as session:
            for u in scored:
                ok = await http_ok(u, session)
                log.debug("Validated candidate %s -> %s", u, ok)
                if ok:
                    validated = u
                    break

    # fallback: try any found m3u8s
    if not validated and found_m3u8:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=VALIDATE_TIMEOUT)) as session:
            for u in found_m3u8:
                ok = await http_ok(u, session)
                if ok:
                    validated = u
                    break

    # final fallback: replace hostname using ts host anchor
    if not validated and found_ts and found_m3u8:
        ts_host = parse_hostname(next(iter(found_ts)))
        for u in found_m3u8:
            new_u = replace_hostname(u, ts_host)
            if new_u != u:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=VALIDATE_TIMEOUT)) as session:
                    ok = await http_ok(new_u, session)
                    if ok:
                        log.info("Constructed working URL by host replacement: %s", new_u)
                        validated = new_u
                        break

    return validated, found_m3u8, found_ts

# ------------------------
# MAIN orchestration
# ------------------------
async def run_scrape():
    start = datetime.utcnow()
    raw = fetch_schedule_text()
    if not raw:
        log.error("No schedule text fetched")
        return

    events = parse_schedule(raw)
    if not events:
        log.error("No events parsed")
        return

    # dedupe events by time+title, keep first
    unique = {}
    for e in events:
        key = (e["time"], e["title"])
        if key not in unique:
            unique[key] = e
    events = list(unique.values())
    log.info("Unique events to process: %d", len(events))

    results = []  # (event, url)

    # Playwright session
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        sem = asyncio.Semaphore(CONCURRENT_FETCHES)

        async def worker(ev):
            async with sem:
                page = await context.new_page()
                page.set_default_timeout(NAV_TIMEOUT_MS)
                try:
                    best, found_m3u8, found_ts = await extract_stream_from_embed(page, ev["link"])
                except Exception as e:
                    log.warning("Worker exception for %s: %s", ev["title"], e)
                    best = None
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass
                return ev, best

        tasks = [worker(ev) for ev in events]
        for fut in asyncio.as_completed(tasks):
            ev, url = await fut
            if url:
                log.info("FOUND: %s %s -> %s", ev["time"], ev["title"], url)
                results.append((ev, url))
            else:
                log.info("NO STREAM: %s %s", ev["time"], ev["title"])

        await context.close()
        await browser.close()

    # dedupe by url
    seen = set()
    final = []
    for ev, url in results:
        if url in seen:
            continue
        seen.add(url)
        final.append((ev, url))

    # group by category keyword detection
    groups = defaultdict(list)
    for ev, url in final:
        title = ev["title"]
        cat = "miscellaneous"
        for kw, slug in CATEGORY_KEYWORDS.items():
            if kw.lower() in title.lower():
                cat = slug
                break
        groups[cat].append({"title": title, "time": ev["time"], "url": url})

    # Write master playlists and per-category files
    # MASTER (VLC)
    with open(os.path.join(OUT_DIR, OUT_MASTER), "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for cat, items in groups.items():
            group_name = f"Sportsonline - {cat.title()}"
            for it in items:
                tv_id = TV_IDS.get(cat, TV_IDS.get("miscellaneous", "Sports.Dummy.us"))
                logo = FALLBACK_LOGOS.get(cat, FALLBACK_LOGOS.get("miscellaneous"))
                name = f"{it['time']} {it['title']}"
                f.write(f'#EXTINF:-1 tvg-id="{tv_id}" tvg-logo="{logo}" group-title="{group_name}",{name}\n')
                for h in VLC_HEADERS:
                    f.write(h + "\n")
                f.write(it["url"] + "\n\n")

    # MASTER (TIVIMATE)
    with open(os.path.join(OUT_DIR, OUT_TIVI_MASTER), "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for cat, items in groups.items():
            group_name = f"Sportsonline - {cat.title()}"
            for it in items:
                tv_id = TV_IDS.get(cat, TV_IDS.get("miscellaneous", "Sports.Dummy.us"))
                logo = FALLBACK_LOGOS.get(cat, FALLBACK_LOGOS.get("miscellaneous"))
                name = f"{it['time']} {it['title']}"
                headers = f"referer=https://dukehorror.net/|origin=https://dukehorror.net|user-agent={ENCODED_USER_AGENT}"
                f.write(f'#EXTINF:-1 tvg-id="{tv_id}" tvg-logo="{logo}" group-title="{group_name}",{name}\n')
                f.write(it["url"] + "|" + headers + "\n\n")

    # Per-category files
    for cat, items in groups.items():
        safe = cat.replace(" ", "_").lower()
        vlc_name = os.path.join(OUT_DIR, f"sportsonline_{safe}.m3u8")
        tiv_name = os.path.join(OUT_DIR, f"sportsonline_{safe}_tivimate.m3u8")
        with open(vlc_name, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for it in items:
                tv_id = TV_IDS.get(cat, TV_IDS.get("miscellaneous"))
                logo = FALLBACK_LOGOS.get(cat, FALLBACK_LOGOS.get("miscellaneous"))
                name = f"{it['time']} {it['title']}"
                f.write(f'#EXTINF:-1 tvg-id="{tv_id}" tvg-logo="{logo}" group-title="Sportsonline - {cat.title()}",{name}\n')
                for h in VLC_HEADERS:
                    f.write(h + "\n")
                f.write(it["url"] + "\n\n")
        with open(tiv_name, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for it in items:
                headers = f"referer=https://dukehorror.net/|origin=https://dukehorror.net|user-agent={ENCODED_USER_AGENT}"
                name = f"{it['time']} {it['title']}"
                f.write(f'#EXTINF:-1 tvg-id="{tv_id}" tvg-logo="{logo}" group-title="Sportsonline - {cat.title()}",{name}\n')
                f.write(it["url"] + "|" + headers + "\n\n")

    log.info("Wrote master playlists and per-category playlists. Total final streams: %d", len(final))
    log.info("Master VLC: %s", os.path.abspath(os.path.join(OUT_DIR, OUT_MASTER)))
    log.info("Master TiviMate: %s", os.path.abspath(os.path.join(OUT_DIR, OUT_TIVI_MASTER)))
    elapsed = (datetime.utcnow() - start).total_seconds()
    log.info("Elapsed: %.2fs", elapsed)

if __name__ == "__main__":
    try:
        asyncio.run(run_scrape())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(0)
