import asyncio
from playwright.async_api import async_playwright
import aiohttp
from datetime import datetime
from urllib.parse import quote
import platform

API_URL = "https://ppv.to/api/streams"

# Custom headers for VLC/Kodi playlists
CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppvs.su',
    '#EXTVLCOPT:http-referrer=https://ppvs.su/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
]

# TiviMate encoded headers
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

ALLOWED_CATEGORIES = {
    "24/7 Streams", "Wrestling", "Football", "Basketball", "Baseball",
    "Combat Sports", "Motorsports", "Miscellaneous", "Boxing", "Darts",
    "American Football", "Ice Hockey"
}

CATEGORY_LOGOS = {
    "24/7 Streams": "https://i.postimg.cc/Nf04VJzs/24-7.png",
    "Wrestling": "https://i.postimg.cc/3JwB6ScC/wwe.png",
    "Football": "https://i.postimg.cc/mgkTPs69/football.png",
    "Basketball": "hhttps://i.postimg.cc/DyzgDjMP/nba.png",
    "Baseball": ""https://i.postimg.cc/28JKxNSR/Baseball3.png",
    "Combat Sports": "https://i.postimg.cc/B6crhYwg/Combat-Sports.png",
    "Motorsports": "https://i.postimg.cc/m2cdkpNp/f1.png",
    "Miscellaneous": "https://i.postimg.cc/Nf04VJzs/24-7.png",
    "Boxing": "https://i.postimg.cc/9FNpjP3h/boxing.png",
    "Darts": "https://i.postimg.cc/7YQVy1vq/darts.png",
    "Ice Hockey": "https://i.postimg.cc/9fx8z6Kj/hockey.png",
    "American Football": "https://i.postimg.cc/Kzw0Dnm6/nfl.png"
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
    except Exception:
        return False


async def get_streams():
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {
            'User-Agent': USER_AGENT
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            print(f"🌐 Fetching streams from {API_URL}")
            async with session.get(API_URL) as resp:
                if resp.status != 200:
                    print(f"❌ Failed to fetch, status {resp.status}")
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
    print(f"🌐 Navigating to iframe: {iframe_url}")

    try:
        await page.goto(iframe_url, timeout=15000)
        await asyncio.sleep(5)
    except Exception as e:
        print(f"❌ Failed to load iframe: {e}")

    page.remove_listener("response", handle_response)

    valid_urls = set()
    for url in found_streams:
        if await check_m3u8_url(url):
            valid_urls.add(url)
    return valid_urls


def write_playlists(streams, url_map):
    """Writes both VLC and TiviMate playlists."""
    if not streams:
        print("⚠️ No streams to write.")
        return

    # --- VLC/Kodi version ---
    lines_vlc = ['#EXTM3U']
    seen_names = set()

    for s in streams:
        name = s["name"].strip()
        name_lower = name.lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)

        urls = url_map.get(f"{s['name']}::{s['category']}::{s['iframe']}", [])
        if not urls:
            continue

        url = next(iter(urls))
        category = s["category"]
        group = GROUP_RENAME_MAP.get(category, category)
        logo = CATEGORY_LOGOS.get(category, "")
        tvg_id = CATEGORY_TVG_IDS.get(category, "Sports.Dummy.us")

        lines_vlc.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines_vlc.extend(CUSTOM_HEADERS)
        lines_vlc.append(url)

    with open("PPVLand.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(lines_vlc))
    print(f"✅ Wrote VLC/Kodi playlist: PPVLand.m3u8 ({len(seen_names)} entries)")

    # --- TiviMate version ---
    lines_tivi = ['#EXTM3U']
    seen_names.clear()

    for s in streams:
        name = s["name"].strip()
        name_lower = name.lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)

        urls = url_map.get(f"{s['name']}::{s['category']}::{s['iframe']}", [])
        if not urls:
            continue

        url = next(iter(urls))
        category = s["category"]
        group = GROUP_RENAME_MAP.get(category, category)
        logo = CATEGORY_LOGOS.get(category, "")
        tvg_id = CATEGORY_TVG_IDS.get(category, "Sports.Dummy.us")

        headers = f"referer=https://ppvs.su/|origin=https://ppvs.su|user-agent={ENCODED_USER_AGENT}"
        lines_tivi.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines_tivi.append(f"{url}|{headers}")

    with open("PPVLand_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(lines_tivi))
    print(f"✅ Wrote TiviMate playlist: PPVLand_TiviMate.m3u8 ({len(seen_names)} entries)")


async def main():
    print("🚀 Starting PPVLand Stream Fetcher...")
    data = await get_streams()

    if not data or 'streams' not in data:
        print("❌ No valid data received.")
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
    streams = [s for s in streams if not (s["name"].lower() in seen or seen.add(s["name"].lower()))]

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        url_map = {}
        for s in streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[key] = urls

        await browser.close()

    write_playlists(streams, url_map)
    print(f"🎉 Done at {datetime.utcnow().isoformat()} UTC")


if __name__ == "__main__":
    asyncio.run(main())
