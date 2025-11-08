import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import aiohttp
from datetime import datetime
from urllib.parse import quote

API_URL = "https://ppv.to/api/streams"

# VLC-compatible headers
CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppv.to',
    '#EXTVLCOPT:http-referrer=https://ppv.to/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0'
]

# --- Category definitions ---
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

# --- helper functions ---
async def check_m3u8_url(url, referer):
    """Check M3U8 URL validity using referer."""
    if "gg.poocloud.in" in url:
        return True
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
    """Fetch stream list from API."""
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(API_URL) as resp:
                if resp.status != 200:
                    print(f"❌ API error: {resp.status}")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"❌ Error in get_streams: {e}")
        return None


async def grab_m3u8_from_iframe(page, iframe_url):
    """Extract playable m3u8 links from an iframe."""
    found_streams = set()

    def handle_response(response):
        if ".m3u8" in response.url:
            found_streams.add(response.url)

    page.on("response", handle_response)
    try:
        await page.goto(iframe_url, timeout=40000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"❌ Failed to load iframe: {e}")
        page.remove_listener("response", handle_response)
        return set()

    await page.wait_for_timeout(3000)
    try:
        await page.mouse.click(200, 200)
    except Exception:
        pass

    try:
        await page.wait_for_event("response", lambda r: ".m3u8" in r.url, timeout=10000)
    except PlaywrightTimeoutError:
        pass
    page.remove_listener("response", handle_response)

    valid_urls = set()
    tasks = [check_m3u8_url(url, iframe_url) for url in found_streams]
    results = await asyncio.gather(*tasks)
    for url, ok in zip(found_streams, results):
        if ok:
            valid_urls.add(url)
    return valid_urls


async def grab_live_now_from_html(page, base_url="https://ppv.to/"):
    """Scrape 'Live Now' streams from homepage."""
    streams = []
    try:
        await page.goto(base_url, timeout=20000)
        await asyncio.sleep(3)
        cards = await page.query_selector_all("#livecards a.item-card")
        for c in cards:
            href = await c.get_attribute("href")
            name_el = await c.query_selector(".card-title")
            img_el = await c.query_selector("img.card-img-top")
            name = await name_el.inner_text() if name_el else "Unnamed"
            poster = await img_el.get_attribute("src") if img_el else None
            if href:
                streams.append({
                    "name": name.strip(),
                    "iframe": f"{base_url.rstrip('/')}{href}",
                    "category": "Live Now",
                    "poster": poster
                })
    except Exception as e:
        print(f"❌ Failed Live Now scrape: {e}")
    return streams


def build_vlc_m3u(streams, url_map):
    """Generate VLC-compatible playlist."""
    lines = ['#EXTM3U']
    seen = set()
    for s in streams:
        if s["name"].lower() in seen:
            continue
        seen.add(s["name"].lower())
        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            continue
        cat = s["category"]
        group = GROUP_RENAME_MAP.get(cat, cat)
        logo = s.get("poster") or CATEGORY_LOGOS.get(cat, "")
        tvg = CATEGORY_TVG_IDS.get(cat, "Misc")
        lines.append(f'#EXTINF:-1 tvg-id="{tvg}" tvg-logo="{logo}" group-title="{group}",{s["name"]}')
        lines.extend(CUSTOM_HEADERS)
        lines.append(next(iter(urls)))
    return "\n".join(lines)


def build_tivimate_m3u(streams, url_map):
    """Generate TiviMate-compatible playlist."""
    lines = ['#EXTM3U']
    ua = quote("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0")
    seen = set()
    for s in streams:
        if s["name"].lower() in seen:
            continue
        seen.add(s["name"].lower())
        key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(key, [])
        if not urls:
            continue
        cat = s["category"]
        group = GROUP_RENAME_MAP.get(cat, cat)
        logo = s.get("poster") or CATEGORY_LOGOS.get(cat, "")
        tvg = CATEGORY_TVG_IDS.get(cat, "Misc")
        url = next(iter(urls))
        # TiviMate syntax with pipe separator and encoded UA
        tivimate_url = f"{url}|referrer=https://ppv.to/|https://ppv.to|user-agent={ua}"
        lines.append(f'#EXTINF:-1 tvg-id="{tvg}" tvg-logo="{logo}" group-title="{group}",{s["name"]}')
        lines.append(tivimate_url)
    return "\n".join(lines)


async def main():
    print("🚀 Fetching streams...")
    data = await get_streams()
    if not data or "streams" not in data:
        print("❌ Invalid API data")
        return
    streams = []
    for cat_data in data["streams"]:
        cat = cat_data.get("category", "Misc")
        for st in cat_data.get("streams", []):
            if not st.get("iframe"):
                continue
            streams.append({
                "name": st["name"],
                "iframe": st["iframe"],
                "category": cat,
                "poster": st.get("poster")
            })

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()
        url_map = {}
        for idx, s in enumerate(streams, start=1):
            print(f"🔎 {idx}/{len(streams)} {s['name']}")
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[f"{s['name']}::{s['category']}::{s['iframe']}"] = urls
        live = await grab_live_now_from_html(page)
        for s in live:
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[f"{s['name']}::{s['category']}::{s['iframe']}"] = urls
        streams.extend(live)
        await browser.close()

    # Write both playlist files
    with open("PPVLand_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write(build_vlc_m3u(streams, url_map))
    with open("PPVLand_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(build_tivimate_m3u(streams, url_map))
    print("✅ Done! Saved PPVLand_VLC.m3u8 and PPVLand_TiviMate.m3u8")


if __name__ == "__main__":
    asyncio.run(main())
