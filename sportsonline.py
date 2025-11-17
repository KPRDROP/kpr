#!/usr/bin/env python3
"""
sportsonline.py
Scrapes https://sportsonline.sn/prog.txt, opens each channel embed page on sportzonline.live,
triggers the player (momentum double-click), captures real .m3u8/.ts requests, validates them,
and writes playlists (VLC + TiviMate).
"""

import asyncio
import re
import requests
import logging
from datetime import datetime
from urllib.parse import quote, urlparse, urlunparse
from collections import defaultdict, Counter
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
import aiohttp
import os

# ------------------------
# Logging
# ------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sportsonline")

# ------------------------
# Config
# ------------------------
SCHEDULE_URL = "https://sportsonline.sn/prog.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

# output filenames
VLC_FILE = "Sportsonline_Playlist.m3u8"
TIVI_FILE = "Sportsonline_TiviMate.m3u8"

VLC_HEADERS = [
    f'#EXTVLCOPT:http-user-agent={USER_AGENT}',
    '#EXTVLCOPT:http-referrer=https://dukehorror.net/'
]

# Fallback logos and TV ids (small set; expand as needed)
FALLBACK_LOGOS = {
    "football": "https://i.postimg.cc/FKq4YrPT/Rv-N0XSF.png",
    "nba": "https://i.postimg.cc/FHBqZPjF/Basketball5.png",
    "basketball": "https://i.postimg.cc/FHBqZPjF/Basketball5.png",
    "ufc": "https://i.postimg.cc/1Xr2rsKc/Combat-Sports.png",
    "miscellaneous": "https://i.postimg.cc/1Xr2rsKc/Combat-Sports.png",
}

TV_IDS = {
    "football": "Soccer.Dummy.us",
    "nba": "NBA.Dummy.us",
    "basketball": "Basketball.Dummy.us",
    "ufc": "PPV.EVENTS.Dummy.us",
    "miscellaneous": "Sports.Dummy.us",
}

CATEGORY_KEYWORDS = {
    "NBA": "nba",
    "UFC": "ufc",
    "NFL": "football",
    "Football": "football",
    "Soccer": "football",
    "Basketball": "basketball",
}

CONCURRENT_FETCHES = 4
NAV_TIMEOUT = 60_000  # ms for playwright goto
RETRIES = 3
CLICK_WAIT = 3        # seconds to wait after clicks
VALIDATE_TIMEOUT = 10 # seconds aiohttp

# Acceptable hosts for validation (optional)
# If you want to restrict which hosts you accept, populate this list.
# For now we accept any tokenized https m3u8.
# TRUSTED_HOSTS = {"srvagu.6522236688.shop", "yzarygw.7380990745.xyz", ...}

# ------------------------
# Helpers
# ------------------------
def strip_non_ascii(text: str) -> str:
    return re.sub(r"[^\x00-\x7F]+", "", text) if text else ""

def has_token(url: str) -> bool:
    return ("?s=" in url) and ("&e=" in url or "&exp=" in url)

def hostname_of(url: str) -> str:
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

async def validate_url(url: str) -> bool:
    """Check if URL returns 200 OK (GET)."""
    try:
        timeout = aiohttp.ClientTimeout(total=VALIDATE_TIMEOUT)
        headers = {"User-Agent": USER_AGENT, "Referer": "https://dukehorror.net/"}
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, headers=headers) as resp:
                return resp.status == 200
    except Exception:
        return False

# ------------------------
# Schedule parsing (handles the format you provided)
# ------------------------
def fetch_schedule_text():
    try:
        log.info(f"🌐 Fetching schedule from {SCHEDULE_URL}")
        r = requests.get(SCHEDULE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.error(f"Failed to fetch schedule: {e}")
        return ""

def parse_schedule(raw_text: str):
    """
    parse lines like:
    15:00   Egypt x Algeria | https://sportzonline.live/channels/hd/hd11.php
    There are many repeated lines for the same match (different channel paths).
    We'll return a list of events as dicts {time, title, link}.
    """
    events = []
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # skip table headings / notices
        if line.startswith("===") or line.upper().startswith("INFO:") or "IMPORTANT" in line.upper():
            continue
        # Typical match lines have a time at start (HH:MM or 00:00)
        m = re.match(r"^(\d{1,2}:\d{2})\s+(.*?)\s*\|\s*(https?://\S+)$", line)
        if m:
            t, title, link = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            events.append({"time": t, "title": strip_non_ascii(title), "link": link})
            continue
        # Some lines might be without pipe but still end with URL
        m2 = re.match(r"^(\d{1,2}:\d{2})\s+(.*?)\s+(https?://\S+)$", line)
        if m2:
            t, title, link = m2.group(1).strip(), m2.group(2).strip(), m2.group(3).strip()
            events.append({"time": t, "title": strip_non_ascii(title), "link": link})
            continue
        # else ignore non-matching lines
    log.info(f"📺 Parsed {len(events)} events from schedule")
    return events

# ------------------------
# Extraction: open embed page, trigger clicks, capture m3u8/.ts
# ------------------------
async def extract_stream_for_link(page, link):
    """
    Returns (best_url, observed_ts_hosts, all_found_urls)
    best_url: a validated m3u8 or None
    observed_ts_hosts: list of ts hostnames observed
    all_found_urls: set of found m3u8 urls
    """
    found_m3u8 = set()
    found_ts = []
    main_page = page

    def on_response(resp):
        try:
            url = resp.url
            if not url:
                return
            if ".m3u8" in url:
                found_m3u8.add(url)
            if url.endswith(".ts"):
                found_ts.append(url)
        except Exception:
            pass

    # Attach response listener
    page.on("response", on_response)

    for attempt in range(1, RETRIES + 1):
        try:
            log.info(f"  ↳ Loading ({attempt}/{RETRIES}) {link}")
            await page.goto(link, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            # Momentum double-click with safe popup closing
            try:
                await page.mouse.click(200, 200)
                pages_before = list(page.context.pages)
                # wait briefly for popup
                new_tab = None
                for _ in range(12):
                    pages_now = list(page.context.pages)
                    # find any page that is not the main page and not in pages_before
                    for p in pages_now:
                        if p is not main_page and p not in pages_before:
                            new_tab = p
                            break
                    if new_tab:
                        break
                    await asyncio.sleep(0.25)
                if new_tab:
                    try:
                        # log and close popup
                        new_url = new_tab.url or "(blank/new)"
                        log.info(f"    🚫 Closing ad tab: {new_url}")
                        await new_tab.close()
                    except Exception:
                        log.warning("    ⚠️ Failed to close ad tab")
                await asyncio.sleep(0.6)
                await page.mouse.click(200, 200)
                log.info("    ▶️ Triggered player (momentum click)")
            except Exception as e:
                log.debug(f"    (momentum click failed) {e}")

            # try clicking common play buttons (best-effort)
            selectors = [
                "div.jw-icon-display[role='button']",
                ".jw-icon-playback",
                ".vjs-big-play-button",
                ".plyr__control",
                "div[class*='play']",
                "button",
                "canvas"
            ]
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click(timeout=200)
                        await asyncio.sleep(0.2)
                except Exception:
                    pass

            # give network a moment
            await asyncio.sleep(CLICK_WAIT)

            # If we've captured m3u8 or ts, break early
            if found_m3u8 or found_ts:
                break

        except PlaywrightTimeout:
            log.warning(f"    ⚠️ Timeout loading {link} (attempt {attempt})")
            await asyncio.sleep(0.5 * attempt)
        except Exception as e:
            # page might be closed unexpectedly in extremes; re-open a clean page handled by caller
            log.warning(f"    ⚠️ Error loading {link}: {e}")
            await asyncio.sleep(0.5 * attempt)

    # stop listening, small delay to let in-flight responses finish
    page.remove_listener("response", on_response)
    await asyncio.sleep(0.15)

    # If no m3u8 found, try fallback: search HTML for m3u8 or .ts
    if not found_m3u8:
        try:
            html = await page.content()
            matches = re.findall(r'https?://[^\s"<>]+\.m3u8(?:\?[^"<>]*)?', html)
            for m in matches:
                found_m3u8.add(m)
            ts_matches = re.findall(r'https?://[^\s"<>]+\.ts(?:\?[^"<>]*)?', html)
            for t in ts_matches:
                found_ts.append(t)
            if matches:
                log.info(f"    🕵️ HTML-found m3u8: {matches[0]}")
        except Exception:
            pass

    # Build candidate list (prefer tokenized)
    tokenized = [u for u in found_m3u8 if has_token(u)]
    candidates = tokenized if tokenized else list(found_m3u8)

    # If we observed ts hosts, pick most common host
    ts_hosts = [hostname_of(u) for u in found_ts if hostname_of(u)]
    preferred_host = None
    if ts_hosts:
        host_count = Counter(ts_hosts)
        preferred_host = host_count.most_common(1)[0][0]

    # scoring: prefer https, token, preferred_host
    def score(u):
        s = 0
        if u.startswith("https://"): s += 10
        if has_token(u): s += 25
        if preferred_host and hostname_of(u) == preferred_host: s += 50
        # longer query string (more token) slight preference
        try:
            if "?" in u:
                s += len(u.split("?",1)[1])//15
        except Exception:
            pass
        return s

    scored = sorted(candidates, key=lambda x: score(x), reverse=True)

    # Validate candidates via aiohttp
    valid = None
    if scored:
        async with aiohttp.ClientSession() as sess:
            for u in scored:
                try:
                    ok = await validate_url(u)
                    log.info(f"    🔹 Validated {u}: {ok}")
                    if ok:
                        valid = u
                        break
                except Exception:
                    continue

    # fallback: try any found_m3u8 not in scored
    if not valid and found_m3u8:
        async with aiohttp.ClientSession() as sess:
            for u in found_m3u8:
                try:
                    ok = await validate_url(u)
                    if ok:
                        valid = u
                        break
                except Exception:
                    continue

    # final fallback: if we saw ts only, attempt to construct a m3u8 by replacing host of any found candidate
    if not valid and found_ts and found_m3u8:
        # anchor host from ts
        ts_host = hostname_of(found_ts[0])
        # pick an existing m3u8 and replace its host
        for u in found_m3u8:
            newu = replace_hostname(u, ts_host)
            if newu != u:
                if await validate_url(newu):
                    log.info(f"    🔁 Replaced host to reach playable: {newu}")
                    valid = newu
                    break

    return valid, set(found_m3u8), set(found_ts)

# ------------------------
# Main orchestration
# ------------------------
async def main():
    start = datetime.utcnow()
    raw = fetch_schedule_text()
    events = parse_schedule(raw)
    if not events:
        log.error("No events parsed — exiting.")
        return

    # de-duplicate by title+time prefer first occurrence
    unique = {}
    for e in events:
        key = (e["time"], e["title"])
        if key not in unique:
            unique[key] = e
    events = list(unique.values())

    log.info(f"🔎 Attempting to fetch streams for {len(events)} unique events")

    results = []  # list of (event, stream_url)
    # concurrency with playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        sem = asyncio.Semaphore(CONCURRENT_FETCHES)

        async def worker(ev):
            async with sem:
                page = await context.new_page()
                # ensure page has sensible viewport & timeouts
                page.set_default_timeout(NAV_TIMEOUT)
                best = None
                try:
                    best, found_m3u8s, found_ts = await extract_stream_for_link(page, ev["link"])
                except Exception as e:
                    log.warning(f"Worker error for {ev['title']}: {e}")
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass
                return ev, best

        tasks = [worker(ev) for ev in events]
        for fut in asyncio.as_completed(tasks):
            ev, best = await fut
            if best:
                log.info(f"✅ Found playable for: {ev['time']} {ev['title']} -> {best}")
                results.append((ev, best))
            else:
                log.info(f"❌ No playable stream for: {ev['time']} {ev['title']}")

        await context.close()
        await browser.close()

    # Deduplicate result streams by URL (prefer first mapping)
    seen_urls = set()
    final = []
    for ev, url in results:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        final.append((ev, url))

    # ---------- Write playlists grouped by category ----------
    groups = defaultdict(list)
    for ev, url in final:
        title = ev["title"]
        # decide category using keywords
        cat = "miscellaneous"
        for k, v in CATEGORY_KEYWORDS.items():
            if k.lower() in title.lower():
                cat = v
                break
        groups[cat].append({"title": title, "time": ev["time"], "url": url})

    # Write VLC playlist
    with open(VLC_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for cat, items in groups.items():
            group_title = f"Sportsonline - {cat.title()}"
            for it in items:
                tvid = TV_IDS.get(cat, TV_IDS["miscellaneous"])
                logo = FALLBACK_LOGOS.get(cat, FALLBACK_LOGOS["miscellaneous"])
                name = f"{it['time']} {it['title']}"
                f.write(f'#EXTINF:-1 tvg-id="{tvid}" tvg-logo="{logo}" group-title="{group_title}",{name}\n')
                for h in VLC_HEADERS:
                    f.write(h + "\n")
                f.write(it["url"] + "\n\n")

    # Write TiviMate playlist (pipe headers)
    with open(TIVI_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for cat, items in groups.items():
            group_title = f"Sportsonline - {cat.title()}"
            for it in items:
                tvid = TV_IDS.get(cat, TV_IDS["miscellaneous"])
                logo = FALLBACK_LOGOS.get(cat, FALLBACK_LOGOS["miscellaneous"])
                name = f"{it['time']} {it['title']}"
                headers = f"referer=https://dukehorror.net/|origin=https://dukehorror.net|user-agent={ENCODED_USER_AGENT}"
                f.write(f'#EXTINF:-1 tvg-id="{tvid}" tvg-logo="{logo}" group-title="{group_title}",{name}\n')
                f.write(it["url"] + "|" + headers + "\n\n")

    end = datetime.utcnow()
    log.info(f"🎉 Done — wrote {len(final)} playable streams to playlists")
    log.info(f"Time elapsed: {(end-start).total_seconds():.2f}s")
    log.info(f"VLC playlist: {os.path.abspath(VLC_FILE)}")
    log.info(f"TiviMate playlist: {os.path.abspath(TIVI_FILE)}")

# ------------------------
# Entrypoint
# ------------------------
if __name__ == "__main__":
    asyncio.run(main())
