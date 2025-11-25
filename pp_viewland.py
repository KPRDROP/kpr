import asyncio
from playwright.async_api import async_playwright
import aiohttp
from datetime import datetime
from urllib.parse import quote

API_URL = "https://ppv.to/api/streams"

# VLC headers
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

# -------------------------
# Inserted Data
# -------------------------
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

NFL_TEAMS = {
    "arizona cardinals", "atlanta falcons", "baltimore ravens", "buffalo bills",
    "carolina panthers", "chicago bears", "cincinnati bengals", "cleveland browns",
    "dallas cowboys", "denver broncos", "detroit lions", "green bay packers",
    "houston texans", "indianapolis colts", "jacksonville jaguars", "kansas city chiefs",
    "las vegas raiders", "los angeles chargers", "los angeles rams", "miami dolphins",
    "minnesota vikings", "new england patriots", "new orleans saints", "new york giants",
    "new york jets", "philadelphia eagles", "pittsburgh steelers", "san francisco 49ers",
    "seattle seahawks", "tampa bay buccaneers", "tennessee titans", "washington commanders"
}

COLLEGE_TEAMS = {
    "alabama crimson tide", "auburn tigers", "arkansas razorbacks", "georgia bulldogs",
    "florida gators", "lsu tigers", "ole miss rebels", "mississippi state bulldogs",
    "tennessee volunteers", "texas longhorns", "oklahoma sooners", "oklahoma state cowboys",
    "baylor bears", "tcu horned frogs", "kansas jayhawks", "kansas state wildcats",
    "iowa state cyclones", "iowa hawkeyes", "michigan wolverines", "ohio state buckeyes",
    "penn state nittany lions", "michigan state spartans", "wisconsin badgers",
    "minnesota golden gophers", "illinois fighting illini", "northwestern wildcats",
    "indiana hoosiers", "notre dame fighting irish", "usc trojans", "ucla bruins",
    "oregon ducks", "oregon state beavers", "washington huskies", "washington state cougars",
    "arizona wildcats", "stanford cardinal", "california golden bears", "colorado buffaloes",
    "florida state seminoles", "miami hurricanes", "clemson tigers", "north carolina tar heels",
    "duke blue devils", "nc state wolfpack", "wake forest demon deacons", "syracuse orange",
    "virginia cavaliers", "virginia tech hokies", "louisville cardinals", "pittsburgh panthers",
    "maryland terrapins", "rutgers scarlet knights", "nebraska cornhuskers", "purdue boilermakers",
    "texas a&m aggies", "kentucky wildcats", "missouri tigers", "vanderbilt commodores",
    "houston cougars", "utah utes", "byu cougars", "boise state broncos", "san diego state aztecs",
    "cincinnati bearcats", "memphis tigers", "ucf knights", "south florida bulls", "smu mustangs",
    "tulsa golden hurricane", "tulane green wave", "navy midshipmen", "army black knights",
    "arizona state sun devils", "texas tech red raiders", "florida atlantic owls"
}

# -------------------------
# Helper Functions
# -------------------------
async def check_m3u8_url(url, referer):
    """Check M3U8 URL with proper headers."""
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0'
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
            print(f"✅ Found M3U8 Stream: {response.url}")
            found_streams.add(response.url)

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
            player_frame = page.frame_locator("iframe").first
            await player_frame.locator("body").click(timeout=5000, force=True)
        else:
            await page.locator("body").click(timeout=5000, force=True)
    except Exception as e:
        pass

    await asyncio.sleep(8)
    page.remove_listener("response", handle_response)

    valid_urls = set()
    tasks = [check_m3u8_url(url, iframe_url) for url in found_streams]
    results = await asyncio.gather(*tasks)
    
    for url, is_valid in zip(found_streams, results):
        if is_valid:
            valid_urls.add(url)
    return valid_urls

async def grab_live_now_from_html(page, base_url="https://ppv.to/"):
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
    except:
        pass
    return live_now_streams

# -------------------------
# Build M3U playlists
# -------------------------
def build_m3u(streams, url_map):
    lines_vlc = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    lines_tm = ['#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"']
    seen_names = set()

    for s in streams:
        name_lower = s["name"].strip().lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)

        unique_key = f"{s['name']}::{s['category']}::{s['iframe']}"
        urls = url_map.get(unique_key, [])
        if not urls:
            continue

        orig_category = s.get("category") or "Misc"
        final_group = GROUP_RENAME_MAP.get(orig_category, f"PPVLand - {orig_category}")
        logo = s.get("poster") or CATEGORY_LOGOS.get(orig_category, "http://drewlive24.duckdns.org:9000/Logos/Default.png")
        tvg_id = CATEGORY_TVG_IDS.get(orig_category, "Misc.Dummy.us")

        # Handle NFL/College
        if orig_category == "American Football":
            matched_team = None
            for team in NFL_TEAMS:
                if team in name_lower:
                    tvg_id = "NFL.Dummy.us"
                    final_group = "PPVLand - NFL Action"
                    matched_team = team
                    break
            if not matched_team:
                for team in COLLEGE_TEAMS:
                    if team in name_lower:
                        tvg_id = "NCAA.Football.Dummy.us"
                        final_group = "PPVLand - College Football"
                        matched_team = team
                        break

        url = next(iter(urls))

        # VLC format
        lines_vlc.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{s["name"]}')
        lines_vlc.extend(CUSTOM_HEADERS)
        lines_vlc.append(url)

        # TiviMate format (pipe-separated, encoded UA)
        referer = s["iframe"]
        origin = "https://" + referer.split('/')[2]
        ua = quote("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0")
        tm_url = f"{url}|referer={referer}|origin={origin}|user-agent={ua}"
        lines_tm.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{s["name"]}')
        lines_tm.append(tm_url)

    return "\n".join(lines_vlc), "\n".join(lines_tm)

# -------------------------
# Main
# -------------------------
async def main():
    print("🚀 Starting PPV Stream Fetcher")
    data = await get_streams()
    if not data or 'streams' not in data:
        return

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

    # Deduplicate
    seen_names = set()
    deduped_streams = []
    for s in streams:
        name_key = s["name"].strip().lower()
        if name_key not in seen_names:
            seen_names.add(name_key)
            deduped_streams.append(s)
    streams = deduped_streams

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        url_map = {}

        total_streams = len(streams)
        for idx, s in enumerate(streams, start=1):
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[key] = urls

        live_now_streams = await grab_live_now_from_html(page)
        for s in live_now_streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[key] = urls
        streams.extend(live_now_streams)

        await browser.close()

    print("💾 Writing playlists...")
    playlist_vlc, playlist_tm = build_m3u(streams, url_map)
    with open("PP_Viewland.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist_vlc)
    with open("PP_Viewland_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist_tm)
    print("✅ Done! Playlists saved.")

if __name__ == "__main__":
    asyncio.run(main())
