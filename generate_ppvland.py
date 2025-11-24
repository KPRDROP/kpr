#!/usr/bin/env python3
"""
generate_ppvland.py
Playwright-based PPVLand scraper (categories kept) producing:
 - PPVland_VLC.m3u8
 - PPVland_TiviMate.m3u8

Resilient iframe scraper: http-scan, Playwright response listener, DOM scrape,
click retries, and robust validation with proper Referer/Origin headers.
"""

import asyncio
import re
import os
import time
import urllib.parse
from datetime import datetime

import aiohttp
from playwright.async_api import async_playwright

# ----------------- Configuration -----------------

API_URL = "https://ppv.to/api/streams"   # (unchanged)

# VLC headers (kept original)
CUSTOM_HEADERS_VLC = [
    '#EXTVLCOPT:http-origin=https://ppvs.su',
    '#EXTVLCOPT:http-referrer=https://ppvs.su',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
]

# TiviMate header pieces (we'll output them as a single pipe string per-channel)
TIVIMATE_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"

TIVIMATE_PIPE_HEADER = (
    f"User-Agent={urllib.parse.quote(TIVIMATE_USER_AGENT)}"
    f"|Referer=https://ppvs.su"
    f"|Origin=https://ppvs.su"
)

# Allowed categories & metadata (kept from your script)
ALLOWED_CATEGORIES = {
    "24/7 Streams", "Wrestling", "Football", "Basketball", "Baseball",
    "Combat Sports", "Motorsports", "Miscellaneous", "Boxing", "Darts",
    "American Football", "Ice Hockey"
}

CATEGORY_LOGOS = {
    "24/7 Streams": "https://github.com/BuddyChewChew/ppv/blob/main/assets/24-7.png?raw=true",
    "Wrestling": "https://github.com/BuddyChewChew/ppv/blob/main/assets/wwe.png?raw=true",
    "Football": "https://github.com/BuddyChewChew/ppv/blob/main/assets/football.png?raw=true",
    "Basketball": "https://github.com/BuddyChewChew/ppv/blob/main/assets/nba.png?raw=true",
    "Baseball": "https://github.com/BuddyChewChew/ppv/blob/main/assets/baseball.png?raw=true",
    "Combat Sports": "https://github.com/BuddyChewChew/ppv/blob/main/assets/mma.png?raw=true",
    "Motorsports": "https://github.com/BuddyChewChew/ppv/blob/main/assets/f1.png?raw=true",
    "Miscellaneous": "https://github.com/BuddyChewChew/ppv/blob/main/assets/24-7.png?raw=true",
    "Boxing": "https://github.com/BuddyChewChew/ppv/blob/main/assets/boxing.png?raw=true",
    "Darts": "https://github.com/BuddyChewChew/ppv/blob/main/assets/darts.png?raw=true",
    "Ice Hockey": "https://github.com/BuddyChewChew/ppv/blob/main/assets/hockey.png?raw=true",
    "American Football": "https://github.com/BuddyChewChew/ppv/blob/main/assets/nfl.png?raw=true"
}

CATEGORY_TVG_IDS = {
    "24/7 Streams": "24.7.Dummy.us",
    "Football": "Soccer.Dummy.us",
    "Wrestling": "PPV.EVENTS.Dummy.us",
    "Combat Sports": "PPV.EVENTS.Dummy.us",
    "Baseball": "MLB.Baseball.Dummy.us",
    "Basketball": "Basketball.Dummy.us",
    "Motorsports": "Racing.Dummy.us",
    "Miscellaneous": "PPV.EVENTS.Dummy.us",
    "Boxing": "PPV.EVENTS.Dummy.us",
    "Ice Hockey": "NHL.Hockey.Dummy.us",
    "Darts": "Darts.Dummy.us",
    "American Football": "NFL.Dummy.us"
}

GROUP_RENAME_MAP = {
    "24/7 Streams": "PPVLand - Live Channels 24/7",
    "Wrestling": "PPVLand - Wrestling Events",
    "Football": "PPVLand - Global Football Streams",
    "Basketball": "PPVLand - Basketball Hub",
    "Baseball": "PPVLand - Baseball Action HD",
    "Combat Sports": "PPVLand - MMA & Fight Nights",
    "Motorsports": "PPVLand - Motorsport Live",
    "Miscellaneous": "PPVLand - Random Events",
    "Boxing": "PPVLand - Boxing",
    "Ice Hockey": "PPVLand - Ice Hockey",
    "Darts": "PPVLand - Darts",
    "American Football": "PPVLand - NFL Action"
}

# ----------------- Utility functions -----------------

async def fetch_text(url: str, headers: dict | None = None, timeout_s: int = 15):
    """Simple GET returning text or None."""
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.get(url, allow_redirects=True) as resp:
                if resp.status == 200:
                    return await resp.text()
                return None
    except Exception:
        return None


m3u8_regex = re.compile(r"https?://[^\s'\"<>]+?\.m3u8(?:\?[^\s'\"<>]*)?", re.IGNORECASE)
ts_regex = re.compile(r"https?://[^\s'\"<>]+?\.(?:ts|aac|mp4)(?:\?[^\s'\"<>]*)?", re.IGNORECASE)

async def extract_m3u8_from_text(text: str):
    """Return set of candidate m3u8 urls from a body of text."""
    out = set()
    if not text:
        return out
    for m in m3u8_regex.findall(text):
        out.add(m)
    # also look for TS manifests or chunklist variants that might be .m3u8-like
    for m in ts_regex.findall(text):
        out.add(m)
    return out

async def check_m3u8_url(url: str, referer: str = "https://ppv.to"):
    """Verify the URL returns 200 with appropriate headers (supports redirects)."""
    try:
        headers = {
            "User-Agent": TIVIMATE_USER_AGENT,
            "Referer": referer,
            "Origin": "https://ppv.to"
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, headers=headers, allow_redirects=True) as resp:
                return resp.status == 200
    except Exception:
        return False

# ----------------- Robust Playwright iframe scraper -----------------

async def grab_m3u8_from_iframe(page, iframe_url: str, max_attempts: int = 2):
    """
    Try multiple approaches to discover playable .m3u8 URLs:
      1) Simple HTTP fetch of iframe_url and regex extraction
      2) Playwright: navigate, attach response listener for .m3u8
      3) DOM inspect: <video>, <source>, <iframe>, <a> hrefs
      4) optional click attempts (kept small for CI)
    Returns: set(valid_urls)
    """

    found = set()

    # 1) Quick HTTP fetch first (cheap)
    try:
        html = await fetch_text(iframe_url, headers={"User-Agent": TIVIMATE_USER_AGENT}, timeout_s=10)
        if html:
            cand = await extract_m3u8_from_text(html)
            if cand:
                # validate quickly
                for c in cand:
                    if await check_m3u8_url(c):
                        found.add(c)
                if found:
                    return found
    except Exception:
        pass

    # 2) Use Playwright (more heavy)
    ua = TIVIMATE_USER_AGENT
    extra_headers = {
        "Referer": "https://ppv.to",
        "Origin": "https://ppv.to",
        "User-Agent": ua
    }

    # Listener collects candidate URLs from any response
    candidates = set()

    def on_response(response):
        try:
            url = response.url
            if ".m3u8" in url.lower() or url.lower().endswith(".m3u8"):
                candidates.add(url)
            # sometimes chunklist/.../index.m3u8 is present as query param or redirect target
            # we also add final redirected url if content-type looks relevant
        except Exception:
            pass

    for attempt in range(max_attempts):
        candidates.clear()
        page.on("response", on_response)
        try:
            # set headers & ua
            try:
                await page.set_extra_http_headers(extra_headers)
            except Exception:
                pass
            try:
                await page.set_user_agent(ua)
            except Exception:
                pass

            # Navigate with a generous timeout, wait until networkidle to capture XHR loads
            try:
                await page.goto(iframe_url, timeout=60000, wait_until="networkidle")
            except Exception:
                # fallback to domcontentloaded
                try:
                    await page.goto(iframe_url, timeout=60000, wait_until="domcontentloaded")
                except Exception:
                    pass

            # DOM inspection (video/source/iframe/a[src/href])
            try:
                dom_candidates = await page.evaluate(
                    """() => {
                        const out = [];
                        const video = document.querySelectorAll('video, source, iframe, a');
                        video.forEach(n => {
                            if (n.src) out.push(n.src);
                            if (n.getAttribute && n.getAttribute('src')) out.push(n.getAttribute('src'));
                            if (n.href) out.push(n.href);
                            if (n.getAttribute && n.getAttribute('data-src')) out.push(n.getAttribute('data-src'));
                        });
                        return Array.from(new Set(out));
                    }"""
                )
                if dom_candidates:
                    for u in dom_candidates:
                        if isinstance(u, str) and (".m3u8" in u or u.endswith(".m3u8") or u.endswith(".m3u8?")):
                            candidates.add(u)
            except Exception:
                pass

            # Try a few gentle clicks (only center clicks, up to 3)
            try:
                box = page.viewport_size or {"width": 1280, "height": 720}
                cx, cy = box["width"] / 2, box["height"] / 2
                for i in range(3):
                    if candidates:
                        break
                    try:
                        await page.mouse.click(cx, cy)
                    except Exception:
                        pass
                    await asyncio.sleep(0.6)
            except Exception:
                pass

            # Wait briefly to let any XHRs finish
            await asyncio.sleep(2)
        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

        # Validate candidates discovered in this pass
        validated = set()
        for url in list(candidates):
            # Normalize relative -> absolute
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("/"):
                # build absolute from iframe_url origin
                from urllib.parse import urljoin
                url = urljoin(iframe_url, url)
            if not url.lower().startswith("http"):
                continue
            ok = await check_m3u8_url(url, referer="https://ppv.to")
            if ok:
                validated.add(url)
            else:
                # Some servers require special referer/origin or trailing param; try with alternate referer
                ok2 = await check_m3u8_url(url, referer=iframe_url)
                if ok2:
                    validated.add(url)
        if validated:
            found.update(validated)
            break

        # small backoff and retry
        await asyncio.sleep(1 + attempt * 1.0)

    # last resort: try fetching the iframe HTML again and look for playlist fragments
    if not found:
        try:
            html2 = await fetch_text(iframe_url, headers={"User-Agent": ua}, timeout_s=15)
            cand2 = await extract_m3u8_from_text(html2)
            for c in cand2:
                if await check_m3u8_url(c, referer=iframe_url):
                    found.add(c)
        except Exception:
            pass

    return found

# ----------------- Playlist builders -----------------

def build_vlc_playlist(streams, url_map):
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen = set()
    for s in streams:
        name_lower = s["name"].strip().lower()
        if name_lower in seen:
            continue
        seen.add(name_lower)
        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            print(f"⚠️ No working URLs for {s['name']}")
            continue
        url = next(iter(urls))
        cat = s["category"]
        group = GROUP_RENAME_MAP.get(cat, cat)
        logo = CATEGORY_LOGOS.get(cat, "")
        tvg = CATEGORY_TVG_IDS.get(cat, "Sports.Dummy.us")
        lines.append(f'#EXTINF:-1 tvg-id="{tvg}" tvg-logo="{logo}" group-title="{group}",{s["name"]}')
        lines.extend(CUSTOM_HEADERS_VLC)
        lines.append(url)
    return "\n".join(lines)


def build_tivimate_playlist(streams, url_map):
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen = set()
    for s in streams:
        name_lower = s["name"].strip().lower()
        if name_lower in seen:
            continue
        seen.add(name_lower)
        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            continue
        url = next(iter(urls))
        cat = s["category"]
        group = GROUP_RENAME_MAP.get(cat, cat)
        logo = CATEGORY_LOGOS.get(cat, "")
        tvg = CATEGORY_TVG_IDS.get(cat, "Sports.Dummy.us")
        # For TiviMate we append pipe headers to the URL line per TiviMate format
        tiv_url = f"{url}|{TIVIMATE_PIPE_HEADER}"
        lines.append(f'#EXTINF:-1 tvg-id="{tvg}" tvg-logo="{logo}" group-title="{group}",{s["name"]}')
        lines.append(tiv_url)
    return "\n".join(lines)

# ----------------- Main -----------------

async def main():
    print("🚀 Starting PPVLand Playwright scraper (CI-friendly)")
    data = None
    try:
        # fetch API list
        async with aiohttp.ClientSession() as sess:
            async with sess.get(API_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    print(f"❌ API returned status {resp.status}")
                    text = await resp.text()
                    print("API response (truncated):", text[:200])
                    return
                data = await resp.json()
    except Exception as e:
        print("❌ Failed to fetch API:", e)
        return

    if not data or 'streams' not in data:
        print("❌ No valid data received from API")
        return

    # build list of streams (filtered by categories)
    streams = []
    for category in data.get("streams", []):
        cat = category.get("category", "").strip()
        if cat not in ALLOWED_CATEGORIES:
            continue
        for stream in category.get("streams", []):
            iframe = stream.get("iframe")
            name = stream.get("name", "Unnamed Event")
            if iframe:
                streams.append({"name": name, "iframe": iframe, "category": cat})

    # dedupe by name
    deduped = []
    seen = set()
    for s in streams:
        k = s['name'].strip().lower()
        if k not in seen:
            deduped.append(s)
            seen.add(k)
    streams = deduped

    if not streams:
        print("🚫 No valid streams found")
        return

    print(f"🔍 Found {len(streams)} streams across {len({s['category'] for s in streams})} categories")

    url_map = {}

    # Launch Playwright once, reuse page context for speed
    async with async_playwright() as pw:
        browser = await pw.firefox.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 720}, user_agent=TIVIMATE_USER_AGENT)
        page = await context.new_page()

        # iterate streams sequentially (parallel could be added but increases complexity)
        for s in streams:
            iframe_url = s['iframe']
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            print(f"\n🔍 Scraping: {s['name']} ({s['category']})")
            try:
                candidates = await grab_m3u8_from_iframe(page, iframe_url)
                if candidates:
                    print(f"✅ Found {len(candidates)} candidate(s) for {s['name']}")
                else:
                    print(f"⚠️ No working URLs for {s['name']}")
                url_map[key] = candidates
            except Exception as e:
                print(f"❌ Error scraping {s['name']}: {e}")
                url_map[key] = set()

        await browser.close()

    # Build playlists
    print("\n💾 Writing final playlists ...")
    vlc = build_vlc_playlist(streams, url_map)
    tiv = build_tivimate_playlist(streams, url_map)

    with open("PPVland_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write(vlc)
    with open("PPVland_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(tiv)

    print(f"✅ Done! Playlists saved as PPVland_VLC.m3u8 and PPVland_TiviMate.m3u8 at {datetime.utcnow().isoformat()} UTC")


if __name__ == "__main__":
    asyncio.run(main())
