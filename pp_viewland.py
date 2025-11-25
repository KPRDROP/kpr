import asyncio
from playwright.async_api import async_playwright
import aiohttp
from datetime import datetime
import urllib.parse

API_URL = "https://ppv.to/api/streams"

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

# ================================
# ✔ ADDED: TiviMate Pipe Header Builder
# ================================
def build_m3u_tivimate(streams, url_map):
    """Builds a TiviMate-compatible playlist using |referer= |origin= |user-agent="""
    
    # URL-encoded UA
    encoded_ua = urllib.parse.quote(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
    )

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
            continue

        orig_category = s.get("category") or "Misc"
        final_group = GROUP_RENAME_MAP.get(orig_category, f"PPVLand - {orig_category}")
        logo = s.get("poster") or CATEGORY_LOGOS.get(orig_category)
        tvg_id = CATEGORY_TVG_IDS.get(orig_category, "Misc.Dummy.us")

        url = next(iter(urls))

        referer = s["iframe"]
        origin = referer.split('/')[0] + "//" + referer.split('/')[2]

        piped = (
            f"{url}"
            f"|referer={referer}"
            f"|origin={origin}"
            f"|user-agent={encoded_ua}"
        )

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{final_group}",{s["name"]}'
        )
        lines.append(piped)

    return "\n".join(lines)

# ================================
# ✔ EXISTING FUNCTIONS UNTOUCHED
# ================================

async def check_m3u8_url(url, referer): ...
async def get_streams(): ...
async def grab_m3u8_from_iframe(page, iframe_url): ...
async def grab_live_now_from_html(page): ...
def build_m3u(streams, url_map): ...

# ================================
# MAIN (Patch applied at end)
# ================================
async def main():
    print("🚀 Starting PPV Stream Fetcher")
    data = await get_streams()
    if not data or 'streams' not in data:
        print("❌ No valid data received from the API")
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

    # de-dup
    seen = set()
    deduped = []
    for s in streams:
        key = s["name"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    streams = deduped

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        url_map = {}

        for s in streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[key] = urls

        live_streams = await grab_live_now_from_html(page)
        for s in live_streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            urls = await grab_m3u8_from_iframe(page, s["iframe"])
            url_map[key] = urls

        streams.extend(live_streams)
        await browser.close()

    # ---------------------------------------
    # ✔ WRITE STANDARD VLC FILE
    # ---------------------------------------
    print("\n💾 Writing final playlist to PP_Viewland.m3u8 ...")
    playlist = build_m3u(streams, url_map)
    with open("PP_Viewland.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist)

    # ---------------------------------------
    # ✔ WRITE NEW TIVIMATE PIPE FILE
    # ---------------------------------------
    print("💾 Writing TiviMate playlist to PP_Viewland_TiviMate.m3u8 ...")
    playlist_tivi = build_m3u_tivimate(streams, url_map)
    with open("PP_Viewland_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist_tivi)

    print(f"✅ Done! Playlists saved at {datetime.utcnow().isoformat()} UTC")

if __name__ == "__main__":
    asyncio.run(main())
