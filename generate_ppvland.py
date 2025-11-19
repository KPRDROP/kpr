import asyncio
import aiohttp
from datetime import datetime
from playwright.async_api import async_playwright

API_URL = "https://ppv.to/api/streams"

CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppv.to',
    '#EXTVLCOPT:http-referrer=https://ppv.to/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
]

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://ppv.to",
    "Origin": "https://ppv.to"
}


async def check_m3u8_url(session, url):
    try:
        async with session.get(url, headers=HEADERS, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"❌ Error checking {url}: {e}")
        return False


async def get_streams():
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), headers=HEADERS) as session:
            async with session.get(API_URL) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"❌ API Error: {text[:500]}")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"❌ Error fetching streams: {e}")
        return None


async def fetch_direct_m3u8(session, iframe_url):
    """Try to fetch direct .m3u8 URLs from iframe HTML"""
    urls = set()
    try:
        async with session.get(iframe_url, headers=HEADERS, timeout=15) as resp:
            text = await resp.text()
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("http") and line.endswith(".m3u8"):
                    urls.add(line)
    except Exception as e:
        print(f"❌ Error fetching iframe {iframe_url}: {e}")
    return urls


async def fetch_m3u8_playwright(iframe_url):
    """Fallback to Playwright for JS-generated streams with retry, stealth, and longer timeout"""
    found_streams = set()
    max_attempts = 3

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36",
            java_script_enabled=True,
            viewport={"width": 1280, "height": 720},
        )
        page = await context.new_page()
        await page.set_extra_http_headers({
            "Referer": "https://ppv.to",
            "Origin": "https://ppv.to",
        })

        # Capture any network response containing .m3u8
        def handle_response(response):
            if ".m3u8" in response.url:
                found_streams.add(response.url)

        page.on("response", handle_response)

        for attempt in range(1, max_attempts + 1):
            try:
                print(f"⚡ Playwright loading iframe (attempt {attempt}): {iframe_url}")
                await page.goto(iframe_url, wait_until="networkidle", timeout=45000)
                await asyncio.sleep(3)
                if found_streams:
                    break
            except Exception as e:
                print(f"⚡ Attempt {attempt} failed: {e}")
                if attempt == max_attempts:
                    print(f"❌ Failed to load iframe after {max_attempts} attempts: {iframe_url}")

        await browser.close()

    return found_streams


async def get_valid_urls(session, iframe_url):
    """Try direct fetch first, fallback to Playwright if empty"""
    urls = await fetch_direct_m3u8(session, iframe_url)
    valid_urls = set()
    for url in urls:
        if await check_m3u8_url(session, url):
            valid_urls.add(url)
    if not valid_urls:
        print("⚡ Falling back to Playwright for dynamic JS iframe...")
        urls = await fetch_m3u8_playwright(iframe_url)
        for url in urls:
            if await check_m3u8_url(session, url):
                valid_urls.add(url)
    return valid_urls


def build_m3u(streams, url_map):
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen_names = set()

    for s in streams:
        name_lower = s["name"].strip().lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)

        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
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


async def main():
    print("🚀 Starting PPV Stream Fetcher")
    data = await get_streams()
    if not data or 'streams' not in data:
        print("❌ No valid data from API")
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

    # Deduplicate
    seen_names = set()
    deduped_streams = []
    for s in streams:
        key = s["name"].strip().lower()
        if key not in seen_names:
            seen_names.add(key)
            deduped_streams.append(s)
    streams = deduped_streams

    if not streams:
        print("🚫 No valid streams found.")
        return

    print(f"🔍 Processing {len(streams)} unique streams")

    url_map = {}
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for s in streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            valid_urls = await get_valid_urls(session, s["iframe"])
            url_map[key] = valid_urls
            if valid_urls:
                print(f"✅ Found {len(valid_urls)} stream(s) for {s['name']}")

    print("\n💾 Writing playlist to PPVLand.m3u8 ...")
    playlist = build_m3u(streams, url_map)
    with open("PPVLand.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist)

    print(f"✅ Done! Playlist saved as PPVLand.m3u8 at {datetime.utcnow().isoformat()} UTC")


if __name__ == "__main__":
    asyncio.run(main())
