import asyncio
from playwright.async_api import async_playwright
import urllib.parse
import aiohttp
from datetime import datetime

API_URL = "https://api.ppv.to/api/streams"

CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppv.to',
    '#EXTVLCOPT:http-referrer=https://ppv.to/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0'
]

ALLOWED_CATEGORIES = {
    "24/7 Streams", "Wrestling", "Football", "Basketball", "Baseball",
    "Combat Sports", "American Football", "Darts", "Motorsports", "Ice Hockey"
}

CATEGORY_LOGOS = {
    "24/7 Streams": "http://drewlive24.duckdns.org:9000/Logos/247.png",
    "Wrestling": "http://drewlive24.duckdns.org:9000/Logos/Wrestling.png",
    "Football": "http://drewlive24.duckdns.org:9000/Logos/Football.png",
    "Basketball": "http://drewlive24.duckdns.org:9000/Logos/Basketball.png",
    "Baseball": "http://drewlive24.duckdns.org:9000/Logos/Baseball.png",
    "American Football": "http://drewlive24.duckdns.org:9000/Logos/NFL3.png",
    "Combat Sports": "http://drewlive24.duckdns.org:9000/Logos/CombatSports2.png",
    "Darts": "http://drewlive24.duckdns.org:9000/Logos/Darts.png",
    "Motorsports": "http://drewlive24.duckdns.org:9000/Logos/Motorsports2.png",
    "Live Now": "http://drewlive24.duckdns.org:9000/Logos/DrewLiveSports.png",
    "Ice Hockey": "http://drewlive24.duckdns.org:9000/Logos/Hockey.png"
}

CATEGORY_TVG_IDS = {
    "24/7 Streams": "24.7.Dummy.us",
    "Wrestling": "PPV.EVENTS.Dummy.us",
    "Football": "Soccer.Dummy.us",
    "Basketball": "Basketball.Dummy.us",
    "Baseball": "MLB.Baseball.Dummy.us",
    "American Football": "NFL.Dummy.us",
    "Combat Sports": "PPV.EVENTS.Dummy.us",
    "Darts": "Darts.Dummy.us",
    "Motorsports": "Racing.Dummy.us",
    "Live Now": "24.7.Dummy.us",
    "Ice Hockey": "NHL.Hockey.Dummy.us"
}

GROUP_RENAME_MAP = {
    "24/7 Streams": "PPVLand - Live Channels 24/7",
    "Wrestling": "PPVLand - Wrestling Events",
    "Football": "PPVLand - Global Football Streams",
    "Basketball": "PPVLand - Basketball Hub",
    "Baseball": "PPVLand - MLB",
    "American Football": "PPVLand - NFL Action",
    "Combat Sports": "PPVLand - Combat Sports",
    "Darts": "PPVLand - Darts",
    "Motorsports": "PPVLand - Racing Action",
    "Live Now": "PPVLand - Live Now",
    "Ice Hockey": "PPVLand - NHL Action"
}

# -----------------------------
# 🔧 PATCH APPLIED BELOW
# -----------------------------

def force_index_m3u8(url: str) -> str:
    """
    Convert any segment-style m3u8 to index.m3u8.
    Example:
       .../tracks-v1a1/mono.ts.m3u8  -->  .../index.m3u8
    """
    if "/index.m3u8" in url:
        return url

    if "/tracks" in url or ".ts.m3u8" in url:
        base = url.split("/")[0:4]     # keep: https://domain/key/
        base = "/".join(base)
        return f"{base}/index.m3u8"

    return url

# -----------------------------


async def check_m3u8_url(url, referer):
    try:
        origin = "https://" + referer.split('/')[2]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
            "Referer": referer,
            "Origin": origin
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                return resp.status in [200, 403]
    except Exception as e:
        print(f"❌ Error checking {url}: {e}")
        return False


async def get_streams():
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {
            'User-Agent': 'http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0'
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
            patched = force_index_m3u8(response.url)   # 🔧 PATCH HERE
            print(f"✅ Found M3U8: {response.url}")
            print(f"👉 Rewritten to: {patched}")
            found_streams.add(patched)

    page.on("response", handle_response)

    print(f"🌐 Navigating to iframe: {iframe_url}")
    try:
        await page.goto(iframe_url, timeout=30000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"❌ Failed to load iframe page: {e}")
        page.remove_listener("response", handle_response)
        return set()

    try:
        await page.wait_for_timeout(5000)
        nested_iframe = page.locator("iframe")
        if await nested_iframe.count() > 0:
            print("🔎 Found nested iframe...")
            frame = page.frame_locator("iframe").first
            await frame.locator("body").click(timeout=5000, force=True)
        else:
            await page.locator("body").click(timeout=5000, force=True)
    except:
        pass

    print("⏳ Waiting 8s for stream requests...")
    await asyncio.sleep(8)

    page.remove_listener("response", handle_response)

    if not found_streams:
        print(f"❌ No M3U8 found for {iframe_url}")
        return set()

    tasks = [check_m3u8_url(url, iframe_url) for url in found_streams]
    results = await asyncio.gather(*tasks)

    valid = {url for url, ok in zip(found_streams, results) if ok}
    return valid

async def grab_live_now_from_html(page, base_url="https://ppv.to/"):
    print("🌐 Scraping 'Live Now' streams from HTML...")
    live_now_streams = []
    try:
        await page.goto(base_url, timeout=20000)
        await asyncio.sleep(3)

        live_cards = await page.query_selector_all("#livecards a.item-card")
        for card in live_cards:
            href = await card.get_attribute("href")
            name_el = await card.query_selector(".card-title")
            poster_el = await card.query_selector("img.card-img-top")
            name = await name_el.inner_text() if name_el else "Unnamed Live"
            poster = await poster_el.get_attribute("src") if poster_el else None

            if href:
                iframe_url = f"{base_url.rstrip('/')}{href}"
                live_now_streams.append({
                    "name": name.strip(),
                    "iframe": iframe_url,
                    "category": "Live Now",
                    "poster": poster
                })
    except Exception as e:
        print(f"❌ Failed scraping 'Live Now': {e}")

    print(f"✅ Found {len(live_now_streams)} 'Live Now' streams")
    return live_now_streams

def build_m3u(streams, url_map):
    """
    Build a standard VLC-style M3U playlist (uses CUSTOM_HEADERS).
    Returns the playlist as a string.
    """
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen_names = set()

    for s in streams:
        name = s.get("name", "Unnamed").strip()
        name_lower = name.lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)

        key = f"{s.get('name')}::{s.get('category')}::{s.get('iframe')}"
        urls = url_map.get(key, [])
        if not urls:
            print(f"⚠️ No working URLs for {name}")
            continue

        url = next(iter(urls))  # use the first validated url

        orig_category = s.get("category", "Misc").strip()
        final_group = GROUP_RENAME_MAP.get(orig_category, orig_category)
        logo = s.get("poster") or CATEGORY_LOGOS.get(orig_category, "")
        tvg_id = CATEGORY_TVG_IDS.get(orig_category, "Misc.Dummy.us")

        # Write EXTINF line and headers
        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{name}'
        )
        # include VLC custom headers (each on its own line)
        lines.extend(CUSTOM_HEADERS)
        lines.append(url)

    return "\n".join(lines)
    
def build_m3u_tivimate(streams, url_map):
    """Builds a TiviMate-compatible M3U using pipe headers."""
    lines = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen_names = set()

    encoded_ua = urllib.parse.quote(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
    )

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

        # Extract domain for referer/origin
        referer = s["iframe"]
        origin = "https://" + referer.split('/')[2]

        # Build pipe headers for TiviMate
        url_with_headers = (
            f"{url}"
            f"|referer={referer}"
            f"|origin={origin}"
            f"|user-agent={encoded_ua}"
        )

        orig_category = s.get("category") or "Misc"
        final_group = GROUP_RENAME_MAP.get(orig_category, f"PPVLand - {orig_category}")
        logo = s.get("poster") or CATEGORY_LOGOS.get(orig_category, "")
        tvg_id = CATEGORY_TVG_IDS.get(orig_category, "Misc.Dummy.us")

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{s["name"]}'
        )
        lines.append(url_with_headers)

    return "\n".join(lines)


async def main():
    print("🚀 Starting PPV Stream Fetcher")
    data = await get_streams()
    if not data or 'streams' not in data:
        print("❌ No valid data received from the API")
        return

    print(f"✅ Found {len(data['streams'])} categories")
    streams = []
    for category in data.get("streams", []):
        cat = category.get("category", "").strip() or "Misc"
        if cat not in ALLOWED_CATEGORIES:
            ALLOWED_CATEGORIES.add(cat)
        for stream in category.get("streams", []):
            iframe = stream.get("iframe") 
            name = stream.get("name", "Unnamed Event")
            poster = stream.get("poster")
            if iframe:
                streams.append({
                    "name": name,
                    "iframe": iframe,
                    "category": cat,
                    "poster": poster
                })

    seen_names = set()
    deduped_streams = []
    for s in streams:
        name_key = s["name"].strip().lower()
        if name_key not in seen_names:
            seen_names.add(name_key)
            deduped_streams.append(s)
    streams = deduped_streams

    async with async_playwright() as p:
        # For debugging, you can set headless=False to watch the browser
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        url_map = {}

        total_streams = len(streams)
        for idx, s in enumerate(streams, start=1):
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            print(f"\n🔎 Scraping stream {idx}/{total_streams}: {s['name']} ({s['category']})")
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            if urls:
                print(f"✅ Got {len(urls)} stream(s) for {s['name']} ({idx}/{total_streams})")
            else:
                print(f"⚠️ No valid streams for {s['name']} ({idx}/{total_streams})")
            url_map[key] = urls

        # Process Live Now
        live_now_streams = await grab_live_now_from_html(page)
        for s in live_now_streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            if urls:
                print(f"✅ Got {len(urls)} 'Live Now' stream(s) for {s['name']}")
            else:
                print(f"⚠️ No valid 'Live Now' streams for {s['name']}")
            url_map[key] = urls
        streams.extend(live_now_streams)

        await browser.close()

    # Write normal playlist
    print("\n💾 Writing final playlist to pp_landview.m3u8 ...")
    playlist = build_m3u(streams, url_map)
    with open("pp_landview.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist)
    print(f"✅ Saved pp_landview.m3u8")

    # Write TiviMate playlist
    print("\n💾 Writing TiviMate playlist to pp_landview_TiviMate.m3u8 ...")
    tivi_playlist = build_m3u_tivimate(streams, url_map)
    with open("pp_landview_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(tivi_playlist)
    print(f"✅ Saved pp_landview_TiviMate.m3u8")



if __name__ == "__main__":
    asyncio.run(main())

