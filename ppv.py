import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import aiohttp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re 

API_URL = "https://ppv.to/api/streams"

CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppv.to',
    '#EXTVLCOPT:http-referrer=https://ppv.to/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0'
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

def get_display_time(timestamp):
    if not timestamp or timestamp <= 0: return ""
    try:
        dt_utc = datetime.fromtimestamp(timestamp, tz=ZoneInfo("UTC"))
        
        dt_est = dt_utc.astimezone(ZoneInfo("America/New_York"))
        est_str = dt_est.strftime("%I:%M %p ET")

        dt_mt = dt_utc.astimezone(ZoneInfo("America/Denver"))
        mt_str = dt_mt.strftime("%I:%M %p MT")
        
        dt_uk = dt_utc.astimezone(ZoneInfo("Europe/London"))
        uk_str = dt_uk.strftime("%H:%M UK")
        
        return f"{est_str} / {mt_str} / {uk_str}"
    except Exception as e:
        return ""

async def grab_m3u8_from_iframe(page, iframe_url):
    first_url = None

    def handle_response(response):
        nonlocal first_url
        url = response.url

        if ".m3u8" in url and first_url is None:
            print(f"✅ Found M3U8 Stream: {url}")
            first_url = url
            try:
                page.remove_listener("response", handle_response)
            except:
                pass

    page.on("response", handle_response)


    try:
        await page.goto(iframe_url, timeout=5000, wait_until="commit")
    except Exception:
        pass

    try:
        await page.wait_for_timeout(300)
        nested_iframe = page.locator("iframe")

        if await nested_iframe.count() > 0:
            await page.mouse.click(200, 200)
        else:
            await page.mouse.click(200, 200)

    except Exception as e:
        print(f"⚠️ Clicking failed, but proceeding anyway. Error: {e}")


    for _ in range(400): 
        if first_url:
            break
        await page.wait_for_timeout(25)

    try:
        page.remove_listener("response", handle_response)
    except:
        pass

    if not first_url:
        return set()

    valid = await check_m3u8_url(first_url, iframe_url)
    if valid:
        return {first_url}

    return set()

async def check_m3u8_url(url, referer):
    if "gg.poocloud.in" in url:
        return True
    try:
        origin = "https://" + referer.split('/')[2]
        headers = {"User-Agent": "Mozilla/5.0", "Referer": referer, "Origin": origin}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers=headers) as resp:
                return resp.status in (200, 403)
    except:
        return False

async def get_streams():
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={'User-Agent': 'Mozilla/5.0'}
        ) as session:
            print(f"🌐 Fetching streams from {API_URL}")
            resp = await session.get(API_URL)
            print(f"🔍 Response status: {resp.status}")
            if resp.status != 200:
                print(f"❌ Error response: {await resp.text()}")
                return None
            return await resp.json()
    except Exception as e:
        print(f"❌ Error in get_streams: {str(e)}")
        return None

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
                    "poster": poster,
                    "starts_at": -1, 
                    "clock_time": "LIVE"
                })
    except Exception as e:
        print(f"❌ Failed scraping 'Live Now': {e}")

    print(f"✅ Found {len(live_now_streams)} 'Live Now' streams")
    return live_now_streams

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
            continue

        orig_cat = s["category"]
        final_group = GROUP_RENAME_MAP.get(orig_cat, "PPVLand - Random Events")
        logo = s.get("poster") or CATEGORY_LOGOS.get(orig_cat)
        tvg_id = CATEGORY_TVG_IDS.get(orig_cat, "24.7.Dummy.us")

        if orig_cat == "American Football":
            nl = name_lower
            for t in NFL_TEAMS:
                if t in nl:
                    final_group = "PPVLand - NFL Action"
                    tvg_id = "NFL.Dummy.us"
            for t in COLLEGE_TEAMS:
                if t in nl:
                    final_group = "PPVLand - College Football"
                    tvg_id = "NCAA.Football.Dummy.us"
        
        display_name = s["name"]
        if s.get("category") != "24/7 Streams":
            clock = s.get("clock_time", "")
            if clock == "LIVE":
                display_name = f"{display_name} [LIVE]"
            elif clock:
                display_name = f"{display_name} [{clock}]"

        url = next(iter(urls))
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{display_name}')
        lines.extend(CUSTOM_HEADERS)
        lines.append(url)

    return "\n".join(lines)

async def main():
    print("🚀 Starting PPV Stream Fetcher")
    data = await get_streams()
    if not data or "streams" not in data:
        print("❌ No valid data received from API")
        return

    print(f"✅ Found {len(data['streams'])} categories")
    streams = []

    for cat_obj in data["streams"]:
        cat = cat_obj.get("category", "")
        for stream in cat_obj.get("streams", []):
            iframe = stream.get("iframe")
            name = stream.get("name")
            poster = stream.get("poster")
            
            starts_at = stream.get("starts_at", 0)
            

            if cat == "24/7 Streams":
                sort_key = float('inf') 
                clock_str = ""
            else:
                sort_key = starts_at
                clock_str = get_display_time(starts_at)

            if iframe:
                streams.append({
                    "name": name,
                    "iframe": iframe,
                    "category": cat,
                    "poster": poster,
                    "starts_at": sort_key, 
                    "clock_time": clock_str
                })

    seen = set()
    unique = []
    for s in streams:
        k = s["name"].lower()
        if k not in seen:
            seen.add(k)
            unique.append(s)
    streams = unique

    print("⏱️ Sorting streams: Events First -> 24/7 Last...")
    streams.sort(key=lambda x: x["starts_at"])

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        url_map = {}
        total = len(streams)

        for idx, s in enumerate(streams, start=1):
            display_name = s['name']
            if s.get("category") != "24/7 Streams" and s.get("clock_time"):
                 display_name = f"{s['name']} [{s['clock_time']}]"
            
            print(f"\n🔎 Scraping stream {idx}/{total}: {display_name} [{s['category']}]")

            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            url_map[key] = await grab_m3u8_from_iframe(page, s["iframe"])

      

        live_now = await grab_live_now_from_html(page)

        for s in live_now:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            url_map[key] = await grab_m3u8_from_iframe(page, s["iframe"])

        for s in live_now:
            s["category"] = "Live Now"

        streams = live_now + streams 

        await browser.close()

    print("\n💾 Writing final playlist to PPVLand.m3u8 ...")
    playlist = build_m3u(streams, url_map)

    with open("PPVLand.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist)

    print(f"✅ Done! Playlist saved as PPVLand.m3u8 at", datetime.utcnow().isoformat(), "UTC")

if __name__ == "__main__":
    asyncio.run(main())
