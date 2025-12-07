#!/usr/bin/env python3

import asyncio
import os
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import Set
import aiohttp
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ---- Configuration ----
# default API
API_URL = os.getenv("API_URL", "https://api.ppv.to/api/streams")

# VLC style custom headers (kept from your prior script)
CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppv.to',
    '#EXTVLCOPT:http-referrer=https://ppv.to/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0'
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
ENCODED_UA = urllib.parse.quote(USER_AGENT, safe="")

ALLOWED_CATEGORIES = {
    "24/7 Streams", "Wrestling", "Football", "Basketball", "Baseball",
    "Combat Sports", "Motorsports", "Miscellaneous", "Boxing", "Darts",
    "American Football", "Ice Hockey", "Live Now"
}

# keep category logos and tvg ids (copied / adjusted)
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
    "American Football": "https://github.com/BuddyChewChew/ppv/blob/main/assets/nfl.png?raw=true",
    "Live Now": "https://github.com/BuddyChewChew/ppv/blob/main/assets/24-7.png?raw=true",
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
    "American Football": "NFL.Dummy.us",
    "Live Now": "24.7.Dummy.us",
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
    "American Football": "PPVLand - NFL Action",
    "Live Now": "PPVLand - Live Now"
}

# ---- Utilities ----
def normalize_m3u8_url(url: str) -> str:
    """
    Normalize common variants so we prefer the index.m3u8 master file.
    Example: .../tracks-v1a1/mono.ts.m3u8  -> .../index.m3u8
    """
    if not url:
        return url
    # if it's a 'tracks-v1a1/mono...' rewrite to index.m3u8 in same directory
    if "tracks-v1a1" in url and "mono" in url and url.endswith(".m3u8"):
        # replace path's last segment with index.m3u8
        parts = url.split("/")
        base = "/".join(parts[:-1])
        return base + "/index.m3u8"
    # if it ends with /playlist.m3u8 or /index.m3u8 already return
    return url

async def check_m3u8_url(url: str, referer: str = None) -> bool:
    """
    Validate that the URL returns a usable status (200 or 403 acceptable).
    Use referer/origin headers if provided.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        headers = {"User-Agent": USER_AGENT}
        if referer:
            headers["Referer"] = referer
            # derive origin
            try:
                origin = "https://" + urllib.parse.urlparse(referer).netloc
                headers["Origin"] = origin
            except Exception:
                pass
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                return resp.status in (200, 403)
    except Exception:
        return False

# ---- API fetching ----
async def get_streams_from_api():
    """
    Fetch streams JSON from API_URL. Return parsed JSON or None.
    If API returns a dict with 'streams' key (old format) handle that.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            print(f"🌐 Fetching streams from {API_URL}")
            async with session.get(API_URL) as resp:
                print(f"🔍 Response status: {resp.status}")
                if resp.status != 200:
                    txt = await resp.text()
                    print("❌ API error:", txt[:300])
                    return None
                return await resp.json()
    except Exception as e:
        print("❌ Error fetching API:", e)
        return None

# ---- Playwright scraping: recursive iframe + JS trigger ----
async def grab_m3u8_from_iframe(page, iframe_url: str) -> Set[str]:
    """
    Load the embed page, trigger player JS, recursively scan frames and collect m3u8 responses.
    Returns a set of normalized m3u8 URLs that pass check_m3u8_url.
    """
    found_urls = set()

    def on_response(resp):
        try:
            url = resp.url
            if ".m3u8" in url:
                norm = normalize_m3u8_url(url)
                print("✅ Found M3U8:", url, "→", norm)
                found_urls.add(norm)
        except Exception:
            pass

    page.on("response", on_response)
    print(f"🌐 Navigating to iframe: {iframe_url}")

    try:
        # navigate; networkidle reduces chance of early return
        await page.goto(iframe_url, timeout=45000, wait_until="networkidle")
    except Exception as e:
        # try fallback: domcontentloaded
        try:
            print("⚠️ Primary goto failed; retrying with domcontentloaded:", e)
            await page.goto(iframe_url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e2:
            print(f"❌ Failed to load iframe page: {e2}")
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass
            return set()

    # small wait so initial JS runs
    await asyncio.sleep(1.5)

    # Best-effort: try to call common player loader functions to force stream requests
    try:
        await page.evaluate(
            """() => {
                try { if (window.player && typeof window.player.play === 'function') { window.player.play().catch(()=>{}); } } catch(e){}
                try { if (typeof loadStream === 'function') { loadStream(); } } catch(e){}
                try { if (typeof initPlayer === 'function') { initPlayer(); } } catch(e){}
            }"""
        )
    except Exception:
        # ignore evaluation errors
        pass

    # Recursive frame scanner: attempt clicks & triggers inside all frames
    async def scan_frame(frame):
        try:
            # try to click body to trigger player if clickable
            try:
                await frame.click("body", timeout=1500, force=True)
            except Exception:
                pass

            # small sleep to allow network requests
            await asyncio.sleep(0.7)

            # evaluate similar triggers inside the frame
            try:
                await frame.evaluate(
                    """() => {
                        try { if (window.player && typeof window.player.play === 'function') player.play().catch(()=>{}); } catch(e){}
                        try { if (typeof loadStream === 'function') loadStream(); } catch(e){}
                    }"""
                )
            except Exception:
                pass

            # recurse into child frames
            for child in frame.child_frames:
                await scan_frame(child)
        except Exception:
            pass

    # scan starting from top-level frames
    for f in page.frames:
        await scan_frame(f)

    # wait a bit for any delayed requests to be made
    await asyncio.sleep(4)

    # remove listener
    try:
        page.remove_listener("response", on_response)
    except Exception:
        pass

    # validate found urls (only keep reachable ones)
    valid = set()
    for u in found_urls:
        # prefer the normalized index m3u8
        final = normalize_m3u8_url(u)
        if await check_m3u8_url(final, iframe_url):
            valid.add(final)
        else:
            # try original before discard
            if u != final and await check_m3u8_url(u, iframe_url):
                valid.add(u)
            else:
                print("❌ Invalid or unreachable URL:", u)

    return valid

# ---- Build playlists ----
def build_m3u_vlc(streams, url_map):
    """
    Build VLC-style M3U (with #EXTVLCOPT custom headers per stream)
    """
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen = set()
    for s in streams:
        name = s.get("name", "Unnamed").strip()
        key = f"{s.get('name')}::{s.get('category')}::{s.get('iframe')}"
        if name.lower() in seen:
            continue
        seen.add(name.lower())

        urls = url_map.get(key, [])
        if not urls:
            print(f"⚠️ No working URLs for {name}")
            continue
        url = next(iter(urls))
        cat = s.get("category", "Misc")
        final_group = GROUP_RENAME_MAP.get(cat, cat)
        logo = s.get("poster") or CATEGORY_LOGOS.get(cat, "")
        tvg_id = CATEGORY_TVG_IDS.get(cat, "Sports.Dummy.us")

        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{name}')
        lines.extend(CUSTOM_HEADERS)
        lines.append(url)
    return "\n".join(lines)

def build_m3u_tivimate(streams, url_map):
    """
    Build TiviMate-style M3U (pipe headers). UA is encoded.
    order of pipe keys: referer first, then origin (as you requested earlier).
    """
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen = set()
    for s in streams:
        name = s.get("name", "Unnamed").strip()
        key = f"{s.get('name')}::{s.get('category')}::{s.get('iframe')}"
        if name.lower() in seen:
            continue
        seen.add(name.lower())

        urls = url_map.get(key, [])
        if not urls:
            continue
        url = next(iter(urls))
        referer = s.get("iframe") or ""
        origin = ""
        try:
            origin = "https://" + urllib.parse.urlparse(referer).netloc
        except Exception:
            origin = ""

        cat = s.get("category", "Misc")
        final_group = GROUP_RENAME_MAP.get(cat, cat)
        logo = s.get("poster") or CATEGORY_LOGOS.get(cat, "")
        tvg_id = CATEGORY_TVG_IDS.get(cat, "Sports.Dummy.us")

        # TiviMate pipe format: |referer=...|origin=...|user-agent=...
        pipe = f"|referer={referer}|origin={origin}|user-agent={ENCODED_UA}"
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{name}')
        lines.append(url + pipe)
    return "\n".join(lines)

# ---- Main ----
async def main():
    print("🚀 Starting PPV Stream Fetcher")
    data = await get_streams_from_api()
    if not data:
        print("❌ No data from API")
        return

    # accept both formats: top-level list or dict with 'streams'
    raw_streams = []
    if isinstance(data, dict) and "streams" in data:
        raw_streams = data.get("streams", [])
    elif isinstance(data, list):
        # older format perhaps: list of categories
        raw_streams = data
    else:
        print("❌ Unexpected API format:", type(data))
        return

    # collect only allowed categories and streams
    streams = []
    for cat_block in raw_streams:
        cat_name = cat_block.get("category", "").strip() if isinstance(cat_block, dict) else ""
        if not cat_name:
            # skip weird blocks
            continue
        if cat_name not in ALLOWED_CATEGORIES:
            # include new categories but flag
            ALLOWED_CATEGORIES.add(cat_name)
        for s in cat_block.get("streams", []) if isinstance(cat_block, dict) else []:
            iframe = s.get("iframe")
            name = s.get("name") or s.get("title") or "Unnamed Event"
            poster = s.get("poster") or ""
            if iframe:
                streams.append({"name": name.strip(), "iframe": iframe, "category": cat_name, "poster": poster})

    # dedupe by name (case-insensitive)
    seen = set()
    deduped = []
    for s in streams:
        key = s["name"].strip().lower()
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    streams = deduped

    if not streams:
        print("🚫 No streams to process")
        return

    print(f"🔍 Found {len(streams)} unique streams across {len({s['category'] for s in streams})} categories")

    # Launch Playwright and scrape each iframe with stealth-ish context
    async with async_playwright() as p:
        browser = await p.chromium.launch(
    headless=True,
    args=["--no-sandbox", "--disable-setuid-sandbox"]
)
context = await browser.new_context(
    user_agent=USER_AGENT,
    locale="en-US",
    timezone_id="UTC",
    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
)
page = await context.new_page()

        url_map = {}
        total = len(streams)
        for idx, s in enumerate(streams, start=1):
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            print(f"\n🔎 Scraping {idx}/{total}: {s['name']} ({s['category']})")
            try:
                urls = await grab_m3u8_from_iframe(page, s["iframe"])
                if urls:
                    print(f"✅ Got {len(urls)} stream(s) for {s['name']}")
                else:
                    print(f"⚠️ No valid streams for {s['name']}")
                url_map[key] = urls
            except Exception as e:
                print(f"❌ Error scraping {s['name']}: {e}")
                url_map[key] = set()

        await browser.close()

    # Build and write playlists
    print("\n💾 Writing final playlist to pp_landview.m3u8 ...")
    vlc_playlist = build_m3u_vlc(streams, url_map)
    with open("pp_landview.m3u8", "w", encoding="utf-8") as f:
        f.write(vlc_playlist)

    print("💾 Writing TiviMate playlist to pp_landview_TiviMate.m3u8 ...")
    tivi_playlist = build_m3u_tivimate(streams, url_map)
    with open("pp_landview_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(tivi_playlist)

    print(f"✅ Done! Playlists saved at {datetime.utcnow().isoformat()} UTC")

if __name__ == "__main__":
    asyncio.run(main())
