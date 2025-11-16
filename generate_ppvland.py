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
    "Basketball": "https://i.postimg.cc/DyzgDjMP/nba.png",
    "Baseball": "https://i.postimg.cc/28JKxNSR/Baseball3.png",
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
            "User-Agent": USER_AGENT,
            "Referer": "https://ppvs.su",
            "Origin": "https://ppvs.su"
        }
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                return resp.status == 200
    except Exception:
        return False


async def get_streams():
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {'User-Agent': USER_AGENT}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            print(f"🌐 Fetching streams from {API_URL}")
            async with session.get(API_URL) as resp:
                if resp.status != 200:
                    print(f"❌ Failed to fetch API ({resp.status})")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"❌ Error fetching streams: {e}")
        return None


async def grab_m3u8_from_iframe(page, iframe_url):
    found = set()

    def listener(response):
        if ".m3u8" in response.url:
            found.add(response.url)

    page.on("response", listener)
    print(f"🌐 Opening: {iframe_url}")

    try:
        await page.goto(iframe_url, wait_until="networkidle", timeout=20000)
        await asyncio.sleep(6)
    except Exception as e:
        print(f"⚠️ Load fail: {e}")

    page.remove_listener("response", listener)

    valid = set()
    for url in found:
        if await check_m3u8_url(url):
            valid.add(url)

    return valid


def write_playlists(streams, url_map):
    if not streams:
        return

    # VLC
    lines = ['#EXTM3U']
    used = set()

    for s in streams:
        name = s["name"]
        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            continue

        if name.lower() in used:
            continue
        used.add(name.lower())

        url = next(iter(urls))
        cat = s["category"]
        group = GROUP_RENAME_MAP.get(cat, cat)
        logo = CATEGORY_LOGOS.get(cat, "")
        tvg_id = CATEGORY_TVG_IDS.get(cat, "Sports.Dummy.us")

        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines.extend(CUSTOM_HEADERS)
        lines.append(url)

    with open("PPVLand.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ Wrote PPVLand.m3u8 ({len(used)})")

    # TiviMate
    lines = ['#EXTM3U']
    used.clear()

    for s in streams:
        name = s["name"]
        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            continue

        if name.lower() in used:
            continue
        used.add(name.lower())

        url = next(iter(urls))
        cat = s["category"]
        group = GROUP_RENAME_MAP.get(cat, cat)
        logo = CATEGORY_LOGOS.get(cat, "")
        tvg_id = CATEGORY_TVG_IDS.get(cat, "Sports.Dummy.us")

        headers = f"referer=https://ppvs.su/|origin=https://ppvs.su|user-agent={ENCODED_USER_AGENT}"

        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines.append(f"{url}|{headers}")

    with open("PPVLand_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ Wrote PPVLand_TiviMate.m3u8 ({len(used)})")


async def main():
    print("🚀 PPVLand Chromium Scraper Starting...")

    data = await get_streams()
    if not data or "streams" not in data:
        print("❌ No valid API response.")
        return

    streams = []
    for cat_block in data["streams"]:
        cat = cat_block.get("category", "").strip()
        if cat not in ALLOWED_CATEGORIES:
            continue

        for s in cat_block.get("streams", []):
            iframe = s.get("iframe")
            name = s.get("name", "Unnamed")
            if iframe:
                streams.append({"name": name, "category": cat, "iframe": iframe})

    print(f"📺 {len(streams)} streams found in API")

    # Deduplicate by name
    seen = set()
    streams = [s for s in streams if not (s["name"].lower() in seen or seen.add(s["name"].lower()))]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-web-security",
                "--disable-site-isolation-trials"
            ]
        )
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
