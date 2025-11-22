import asyncio
import os
from playwright.async_api import async_playwright
import aiohttp
from datetime import datetime
from zoneinfo import ZoneInfo
import platform
import urllib.parse

# -------------------------------
#  API URL now comes from Secrets
# -------------------------------
API_URL = os.getenv("API_URL", "").strip()

if not API_URL:
    raise SystemExit("❌ ERROR: Missing API_URL environment variable. Set it in GitHub Secrets.")

CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppvs.su',
    '#EXTVLCOPT:http-referrer=https://ppvs.su/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
]

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

async def check_m3u8_url(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://ppvs.su",
            "Origin": "https://ppvs.su"
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"❌ Error checking {url}: {e}")
        return False

async def get_streams():
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {'User-Agent': 'Mozilla/5.0'}

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            print(f"🌐 Fetching streams from {API_URL}")
            async with session.get(API_URL) as resp:
                print(f"🔍 Response status: {resp.status}")
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"❌ Error response: {error_text[:500]}")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"❌ Error in get_streams: {str(e)}")
        return None

async def grab_m3u8_from_iframe(page, iframe_url):
    found_streams = set()

    def handle_response(response):
        if ".m3u8" in response.url:
            found_streams.add(response.url)

    page.on("response", handle_response)
    print(f"🌐 Navigating to iframe: {iframe_url}")

    try:
        await page.goto(iframe_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"❌ Failed to load iframe: {e}")
        page.remove_listener("response", handle_response)
        return set()

    await asyncio.sleep(2)

    try:
        box = page.viewport_size or {"width": 1280, "height": 720}
        cx, cy = box["width"] / 2, box["height"] / 2
        for i in range(4):
            if found_streams:
                break
            print(f"🖱️ Click #{i + 1}")
            try:
                await page.mouse.click(cx, cy)
            except Exception:
                pass
            await asyncio.sleep(0.3)
    except Exception as e:
        print(f"❌ Mouse click error: {e}")

    print("⏳ Waiting 5s for final stream load...")
    await asyncio.sleep(5)
    page.remove_listener("response", handle_response)

    valid_urls = set()
    for url in found_streams:
        if await check_m3u8_url(url):
            valid_urls.add(url)
        else:
            print(f"❌ Invalid or unreachable URL: {url}")
    return valid_urls


#────────────────────────────────────────────
#   ORIGINAL M3U BUILDER (VLC VERSION)
#────────────────────────────────────────────
def build_m3u(streams, url_map):
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen_names = set()

    for s in streams:
        name_lower = s["name"].strip().lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)

        unique_key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(unique_key, [])

        if not urls:
            print(f"⚠️ No working URLs for {s['name']}")
            continue

        orig_category = s["category"].strip()
        final_group = GROUP_RENAME_MAP.get(orig_category, orig_category)
        logo = CATEGORY_LOGOS.get(orig_category, "")
        tvg_id = CATEGORY_TVG_IDS.get(orig_category, "Sports.Dummy.us")

        url = next(iter(urls))

        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{s["name"]}')
        lines.extend(CUSTOM_HEADERS)
        lines.append(url)

    return "\n".join(lines)


#────────────────────────────────────────────
#   NEW: TiviMate playlist builder
#────────────────────────────────────────────
def build_m3u_tivimate(streams, url_map):
    lines = ['#EXTM3U']

    encoded_ua = urllib.parse.quote(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
    )

    for s in streams:
        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])

        if not urls:
            continue

        url = next(iter(urls))
        category = GROUP_RENAME_MAP.get(s["category"], s["category"])
        logo = CATEGORY_LOGOS.get(s["category"], "")
        tvg_id = CATEGORY_TVG_IDS.get(s["category"], "Sports.Dummy.us")

        pipe_url = (
            f"{url}"
            f"|Referer=https://ppvs.su/"
            f"|Origin=https://ppvs.su"
            f"|User-Agent={encoded_ua}"
        )

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{category}",{s["name"]}'
        )
        lines.append(pipe_url)

    return "\n".join(lines)


#────────────────────────────────────────────
#          MAIN SCRIPT
#────────────────────────────────────────────

async def main():
    print("🚀 Starting PPV Stream Fetcher")
    data = await get_streams()

    if not data or 'streams' not in data:
        print("❌ No valid data received from the API")
        return

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

    # Deduplicate by name
    seen = set()
    unique = []
    for s in streams:
        key = s["name"].lower()
        if key not in seen:
            unique.append(s)
            seen.add(key)

    streams = unique

    print(f"🔍 Found {len(streams)} unique streams")

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()

        url_map = {}
        for s in streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            print(f"\n🔍 Scraping: {s['name']} ({s['category']})")
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[key] = urls

        await browser.close()

    print("\n💾 Writing final playlists...")

    # VLC
    with open("PPVLand.m3u8", "w", encoding="utf-8") as f:
        f.write(build_m3u(streams, url_map))

    # NEW VLC copy for your request
    with open("PPVLand_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write(build_m3u(streams, url_map))

    # NEW TiviMate playlist
    with open("PPVLand_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(build_m3u_tivimate(streams, url_map))

    print("✅ Saved: PPVLand.m3u8, PPVLand_VLC.m3u8, PPVLand_TiviMate.m3u8")
    print(f"⏱️ Completed at {datetime.utcnow().isoformat()} UTC")


if __name__ == "__main__":
    asyncio.run(main())
