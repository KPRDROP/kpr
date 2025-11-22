import asyncio
from playwright.async_api import async_playwright
import aiohttp
from datetime import datetime

API_URL = "https://ppv.to/api/streams"

# VLC headers
CUSTOM_HEADERS_VLC = [
    '#EXTVLCOPT:http-origin=https://ppvs.su',
    '#EXTVLCOPT:http-referrer=https://ppvs.su',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
]

# TiviMate headers (pipe encoded)
CUSTOM_HEADERS_TIVIMATE = [
    'http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0|http-referrer=https://ppvs.su|http-origin=https://ppvs.su'
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
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        }
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
        await page.goto(iframe_url, timeout=60000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"❌ Failed to load iframe: {e}")
        page.remove_listener("response", handle_response)
        return set()

    await asyncio.sleep(2)

    # mouse clicks optional, skip if not needed in CI
    page.remove_listener("response", handle_response)

    valid_urls = set()
    for url in found_streams:
        if await check_m3u8_url(url):
            valid_urls.add(url)
        else:
            print(f"❌ Invalid or unreachable URL: {url}")
    return valid_urls


def build_m3u(streams, url_map, headers_type='vlc'):
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen_names = set()

    headers_list = CUSTOM_HEADERS_VLC if headers_type == 'vlc' else CUSTOM_HEADERS_TIVIMATE

    for s in streams:
        name_lower = s["name"].strip().lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)

        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            continue
        url = next(iter(urls))

        orig_category = s["category"].strip()
        final_group = GROUP_RENAME_MAP.get(orig_category, orig_category)
        logo = CATEGORY_LOGOS.get(orig_category, "")
        tvg_id = CATEGORY_TVG_IDS.get(orig_category, "Sports.Dummy.us")

        if headers_type == 'vlc':
            lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{s["name"]}')
            lines.extend(headers_list)
            lines.append(url)
        else:  # TiviMate
            lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{s["name"]}|{headers_list[0]}')
            lines.append(url)

    return "\n".join(lines)


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

    # deduplicate
    seen = set()
    deduped_streams = []
    for s in streams:
        key = s["name"].strip().lower()
        if key not in seen:
            seen.add(key)
            deduped_streams.append(s)
    streams = deduped_streams

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 720})
        page = await context.new_page()

        url_map = {}
        for s in streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[key] = urls

        await browser.close()

    # Write VLC playlist
    playlist_vlc = build_m3u(streams, url_map, headers_type='vlc')
    with open("PPVland_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist_vlc)

    # Write TiviMate playlist
    playlist_tm = build_m3u(streams, url_map, headers_type='tivimate')
    with open("PPVland_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist_tm)

    print(f"✅ Done! Files saved at {datetime.utcnow().isoformat()} UTC")


if __name__ == "__main__":
    asyncio.run(main())
