import asyncio
from playwright.async_api import async_playwright
import aiohttp
from datetime import datetime
from zoneinfo import ZoneInfo

API_URL = "https://ppv.to/api/streams"

CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppv.to',
    '#EXTVLCOPT:http-referrer=https://ppv.to/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
]

# Only new allowed categories
ALLOWED_CATEGORIES = {"Basketball", "Football", "Wrestling", "24/7 Streams"}

CATEGORY_LOGOS = {
    "24/7 Streams": "https://github.com/BuddyChewChew/ppv/blob/main/assets/24-7.png?raw=true",
    "Wrestling": "https://github.com/BuddyChewChew/ppv/blob/main/assets/wwe.png?raw=true",
    "Football": "https://github.com/BuddyChewChew/ppv/blob/main/assets/football.png?raw=true",
    "Basketball": "https://github.com/BuddyChewChew/ppv/blob/main/assets/nba.png?raw=true"
}

CATEGORY_TVG_IDS = {
    "24/7 Streams": "24.7.Dummy.us",
    "Football": "Soccer.Dummy.us",
    "Wrestling": "PPV.EVENTS.Dummy.us",
    "Basketball": "Basketball.Dummy.us"
}

GROUP_RENAME_MAP = {
    "24/7 Streams": "PPVLand - Live Channels 24/7",
    "Wrestling": "PPVLand - Wrestling Events",
    "Football": "PPVLand - Global Football Streams",
    "Basketball": "PPVLand - Basketball Hub"
}


async def check_m3u8_url(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://ppv.to",
            "Origin": "https://ppv.to"
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
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            print(f"🌐 Fetching streams from {API_URL}")
            async with session.get(API_URL) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"❌ API Error: {text[:500]}")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"❌ Error fetching streams: {e}")
        return None


async def grab_m3u8_from_iframe(page, iframe_url):
    found_streams = set()

    def handle_response(response):
        if ".m3u8" in response.url:
            found_streams.add(response.url)

    page.on("response", handle_response)
    try:
        await page.goto(iframe_url, timeout=15000)
    except Exception as e:
        print(f"❌ Failed to load iframe: {e}")
        page.remove_listener("response", handle_response)
        return set()

    await asyncio.sleep(2)

    # Optional click attempts to trigger streams
    box = page.viewport_size or {"width": 1280, "height": 720}
    cx, cy = box["width"] / 2, box["height"] / 2
    for i in range(4):
        if found_streams:
            break
        try:
            await page.mouse.click(cx, cy)
        except Exception:
            pass
        await asyncio.sleep(0.3)

    await asyncio.sleep(5)
    page.remove_listener("response", handle_response)

    valid_urls = set()
    for url in found_streams:
        if await check_m3u8_url(url):
            valid_urls.add(url)
        else:
            print(f"❌ Invalid or unreachable URL: {url}")
    return valid_urls


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
        final_group = GROUP_RENAME_MAP.get(orig_
