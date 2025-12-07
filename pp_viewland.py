#!/usr/bin/env python3

from __future__ import annotations
import asyncio
import os
import sys
import urllib.parse
from typing import List, Dict, Set
import aiohttp
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime

# --- Configuration ---------------------------------------------------------

# Prefer API_URL from environment (useful for GitHub Actions secrets),
# otherwise try known endpoints as fallback.
API_URL = os.environ.get("API_URL") or "https://api.ppv.to/api/streams"
# secondary fallbacks (try in order if primary fails)
FALLBACK_API_URLS = [
    "https://api.ppvs.su/api/streams",
    
]

# VLC-style custom headers appended before each URL
CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppvs.su',
    '#EXTVLCOPT:http-referrer=https://ppvs.su',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
]

# Allowed categories to include — keep your original mapping
ALLOWED_CATEGORIES = {
    "24/7 Streams", "Wrestling", "Football", "Basketball", "Baseball",
    "Combat Sports", "Motorsports", "Miscellaneous", "Boxing", "Darts",
    "American Football", "Ice Hockey", "Live Now"
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
    "Live Now": "PPVLand - Live Now",
}

# Output filenames
OUT_VLC = "PPVland_VLC.m3u8"
OUT_TIVIMATE = "PPVland_TiviMate.m3u8"

# User-Agent to encode for TiviMate
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"

# --- Utilities -------------------------------------------------------------

def rewrite_to_index_m3u8(url: str) -> str:
    """
    Best-effort to convert '.../tracks-v1a1/mono.ts.m3u8' or similar to '/index.m3u8'
    Many players provide segmented path; index endpoint is often the canonical HLS.
    """
    if not url:
        return url
    # if already index.m3u8, return as-is
    if "/index.m3u8" in url:
        return url
    # common variation: /tracks-v1a1/mono.ts.m3u8 -> /index.m3u8
    if "tracks-v1a1" in url:
        # Replace trailing path after the stream key with index.m3u8
        # Find the part before 'tracks-v1a1'
        try:
            prefix, _ = url.split("tracks-v1a1", 1)
            # remove any trailing slash characters and append index.m3u8
            new = prefix.rstrip("/") + "/index.m3u8"
            return new
        except Exception:
            pass
    # if file ends with mono.ts.m3u8, replace with index.m3u8
    if url.endswith("mono.ts.m3u8"):
        return url.rsplit("/", 1)[0] + "/index.m3u8"
    return url

async def check_m3u8_url(session: aiohttp.ClientSession, url: str, referer: str | None = None) -> bool:
    """
    Validate that the URL returns something usable.
    Use Referer first (user requested referer appear before origin).
    Treat 200 and 403 as acceptable (some servers return 403 but allocation exists).
    """
    if not url:
        return False
    headers = {
        "User-Agent": USER_AGENT,
    }
    if referer:
        headers["Referer"] = referer
        # origin derived from referer (if present)
        try:
            origin = "https://" + urllib.parse.urlparse(referer).netloc
            headers["Origin"] = origin
        except Exception:
            pass

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with session.get(url, headers=headers, timeout=timeout) as resp:
            return resp.status in (200, 403)
    except Exception:
        return False

# --- API fetching ---------------------------------------------------------

async def fetch_api_json(session: aiohttp.ClientSession, url: str):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                text = await r.text()
                raise RuntimeError(f"API returned {r.status}: {text[:200]!s}")
            return await r.json()
    except Exception as e:
        raise

async def get_streams_from_api() -> dict | None:
    """
    Try primary API_URL then fallbacks. Returns parsed JSON or None.
    """
    urls_to_try = [API_URL] + [u for u in FALLBACK_API_URLS if u != API_URL]
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        for url in urls_to_try:
            try:
                print(f"🌐 Fetching streams from {url}")
                data = await fetch_api_json(session, url)
                print("🔍 Response OK")
                return data
            except Exception as e:
                print(f"⚠️ Failed to fetch from {url}: {e}")
                continue
    return None

# --- Playwright scraping --------------------------------------------------

async def grab_m3u8_from_iframe(page, iframe_url: str, session: aiohttp.ClientSession) -> set:
    """
    Navigate to iframe_url with Playwright page and capture network responses
    that look like .m3u8. Returns a set of validated m3u8 URLs.
    """
    found: Set[str] = set()

    def _on_response(response):
        try:
            u = response.url
            if ".m3u8" in u:
                # Collect raw; we'll validate later with aiohttp
                found.add(u)
                # print quick debug
                print(f"✅ Found M3U8: {u}")
        except Exception:
            pass

    page.on("response", _on_response)
    print(f"🌐 Navigating to iframe: {iframe_url}")
    try:
        await page.goto(iframe_url, timeout=30000, wait_until="domcontentloaded")
    except PlaywrightTimeoutError as e:
        print(f"❌ Failed to load iframe page: {e}")
        # try a second attempt with load wait
        try:
            await page.goto(iframe_url, timeout=20000, wait_until="load")
        except Exception:
            page.remove_listener("response", _on_response)
            return set()
    except Exception as e:
        print(f"❌ Error loading iframe: {e}")
        page.remove_listener("response", _on_response)
        return set()

    # Give page some interactions to trigger network requests
    try:
        await asyncio.sleep(1.0)
        # Try to click in nested iframe if present
        frames = page.frames
        # If there are frames beyond main, try clicking body in first child frame
        if len(frames) > 1:
            try:
                await frames[1].click("body", timeout=1000, force=True)
            except Exception:
                pass
        # fallback: click main body
        try:
            await page.click("body", timeout=1000, force=True)
        except Exception:
            pass
    except Exception:
        pass

    # wait a short while for network activity
    await asyncio.sleep(4.0)

    page.remove_listener("response", _on_response)

    # rewrite any tracks/.../mono.ts -> index.m3u8 and validate
    validated: Set[str] = set()
    async with session:
        # But session is passed in as an aiohttp session already open by caller,
        # so don't re-enter a context — instead expect a session param. (we'll not 'async with' here)
        pass

    # Validate using the provided session (caller must provide)
    val_tasks = []
    url_list = list(found)
    for u in url_list:
        # produce a candidate rewritten url too
        rewritten = rewrite_to_index_m3u8(u)
        # check rewritten first (more canonical)
        val_tasks.append((u, rewritten))

    good: Set[str] = set()
    # Validate sequentially to avoid hammering; that's okay for playlist scraping
    for orig, candidate in val_tasks:
        # prefer candidate (index) if different and valid
        check_candidates = [candidate] if candidate and candidate != orig else []
        check_candidates.append(orig)
        for to_check in check_candidates:
            ok = await check_m3u8_url(session, to_check, referer=iframe_url)
            if ok:
                # ensure we store the index form if that was valid
                final = rewrite_to_index_m3u8(to_check)
                good.add(final)
                # stop checking other candidates for this original
                break
            else:
                # debug log
                print(f"❌ Invalid or unreachable URL: {to_check}")
    return good

# --- Playlist builders ----------------------------------------------------

def build_m3u_vlc(streams: List[Dict], url_map: Dict[str, List[str]]) -> str:
    """Return VLC-style M3U content as a string."""
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen = set()
    for s in streams:
        name = s.get("name", "Unnamed").strip()
        name_key = name.lower()
        if name_key in seen:
            continue
        seen.add(name_key)

        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            print(f"⚠️ No working URLs for {name}")
            continue
        url = next(iter(urls))

        category = s.get("category", "Misc").strip()
        group = GROUP_RENAME_MAP.get(category, category)
        logo = s.get("poster") or CATEGORY_LOGOS.get(category, "")
        tvg = CATEGORY_TVG_IDS.get(category, "Sports.Dummy.us")

        lines.append(f'#EXTINF:-1 tvg-id="{tvg}" tvg-logo="{logo}" group-title="{group}",{name}')
        # append VLC custom headers
        lines.extend(CUSTOM_HEADERS)
        lines.append(url)
    return "\n".join(lines)

def build_m3u_tivimate(streams: List[Dict], url_map: Dict[str, List[str]]) -> str:
    """Return TiviMate-style M3U content (pipe headers) as a string."""
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen = set()
    encoded_ua = urllib.parse.quote(USER_AGENT, safe="")

    for s in streams:
        name = s.get("name", "Unnamed").strip()
        name_key = name.lower()
        if name_key in seen:
            continue
        seen.add(name_key)

        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            continue
        url = next(iter(urls))

        referer = s.get("iframe") or ""
        # ensure referer is absolute (it should be)
        origin = ""
        try:
            origin = "https://" + urllib.parse.urlparse(referer).netloc if referer else ""
        except Exception:
            origin = ""

        # Build the pipe headers; user requested referer first then origin
        pipe = ""
        if referer:
            pipe += f"|referer={referer}"
        if origin:
            pipe += f"|origin={origin}"
        pipe += f"|user-agent={encoded_ua}"

        category = s.get("category", "Misc").strip()
        group = GROUP_RENAME_MAP.get(category, category)
        logo = s.get("poster") or CATEGORY_LOGOS.get(category, "")
        tvg = CATEGORY_TVG_IDS.get(category, "Sports.Dummy.us")

        lines.append(f'#EXTINF:-1 tvg-id="{tvg}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines.append(url + pipe)
    return "\n".join(lines)

# --- Main -----------------------------------------------------------------

async def main():
    print("🚀 Starting PPVLand playlist builder")
    api_data = await get_streams_from_api()
    if not api_data:
        print("❌ API fetch failed, exiting.")
        return

    # Expect API to return structure with "streams" key containing categories
    if not isinstance(api_data, dict) or "streams" not in api_data:
        print("❌ ERROR: API returned invalid format (expected JSON with 'streams').")
        print(f"Response preview: {str(api_data)[:400]}")
        return

    categories = api_data.get("streams", [])
    print(f"✅ Found {len(categories)} categories in API response")

    # Build list of streams to process
    streams = []
    for category in categories:
        cat_name = (category.get("category") or "Misc").strip()
        # keep category even if new; user said do not remove categories
        if cat_name not in ALLOWED_CATEGORIES:
            # include but don't add to rename map unless present
            ALLOWED_CATEGORIES.add(cat_name)
        for item in category.get("streams", []) or []:
            iframe = item.get("iframe") or item.get("embed") or item.get("url")
            name = item.get("name") or "Unnamed Event"
            poster = item.get("poster") or item.get("logo")
            if iframe:
                streams.append({"name": name.strip(), "iframe": iframe.strip(), "category": cat_name, "poster": poster})

    # Deduplicate by lower-case name
    seen = set()
    dedup = []
    for s in streams:
        k = s["name"].strip().lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(s)
    streams = dedup

    if not streams:
        print("🚫 No streams to process after parsing API.")
        return

    print(f"🔍 Found {len(streams)} unique streams to process from {len({s['category'] for s in streams})} categories")

    # Create an aiohttp session for validation checks
    aio_session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=aiohttp.ClientTimeout(total=15))

    url_map: Dict[str, List[str]] = {}
    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            total = len(streams)
            for idx, s in enumerate(streams, start=1):
                key = f"{s['name']}::{s['category']}::{s['iframe']}"
                print(f"\n🔎 Scraping: {s['name']} ({s['category']}) [{idx}/{total}]")
                try:
                    urls = await grab_m3u8_from_iframe(page, s["iframe"], aio_session)
                except Exception as e:
                    print(f"❌ Scrape error for {s['name']}: {e}")
                    urls = set()
                if urls:
                    print(f"✅ Got {len(urls)} stream(s) for {s['name']}")
                else:
                    print(f"⚠️ No valid streams for {s['name']}")
                url_map[key] = list(urls)            
            

            await browser.close()
    finally:
        await aio_session.close()

    # Build playlists
    print(f"\n💾 Writing final playlists: {OUT_VLC} and {OUT_TIVIMATE} ...")
    playlist_vlc = build_m3u_vlc(streams, url_map)
    playlist_tivimate = build_m3u_tivimate(streams, url_map)

    # Write files atomically
    for filename, content in ((OUT_VLC, playlist_vlc), (OUT_TIVIMATE, playlist_tivimate)):
        tmp = filename + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
            # move/replace
            os.replace(tmp, filename)
            print(f"✅ Saved {filename}")
        except Exception as e:
            print(f"❌ Failed to write {filename}: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    print(f"✅ Done! Playlists saved at {datetime.utcnow().isoformat()} UTC")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")
        sys.exit(0)
