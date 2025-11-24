#!/usr/bin/env python3
import asyncio
import aiohttp
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Set, Dict
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

API_URL = "https://ppv.to/api/streams"

CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppv.to',
    '#EXTVLCOPT:http-referrer=https://ppv.to/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0'
]

# (Your ALLOWED_CATEGORIES, CATEGORY_LOGOS, CATEGORY_TVG_IDS, GROUP_RENAME_MAP,
# NFL_TEAMS, COLLEGE_TEAMS remain unchanged. Paste them here exactly as you had.)
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

# utils
def get_display_time(timestamp):
    if not timestamp or timestamp <= 0: return ""
    try:
        dt_utc = datetime.fromtimestamp(timestamp).astimezone(ZoneInfo("UTC"))
        dt_est = dt_utc.astimezone(ZoneInfo("America/New_York"))
        est_str = dt_est.strftime("%I:%M %p ET")
        dt_mt = dt_utc.astimezone(ZoneInfo("America/Denver"))
        mt_str = dt_mt.strftime("%I:%M %p MT")
        dt_uk = dt_utc.astimezone(ZoneInfo("Europe/London"))
        uk_str = dt_uk.strftime("%H:%M UK")
        return f"{est_str} / {mt_str} / {uk_str}"
    except Exception:
        return ""

# -------------------------
# m3u8 checker — use one session
# -------------------------
async def check_m3u8_url(session: aiohttp.ClientSession, url: str, referer: str) -> bool:
    # quick allowlist
    if "gg.poocloud.in" in url or "poocloud" in url:
        return True
    try:
        origin = "https://" + referer.split('/')[2] if referer.startswith("http") else ""
        headers = {"User-Agent": "Mozilla/5.0", "Referer": referer, "Origin": origin}
        async with session.get(url, headers=headers, timeout=10) as resp:
            # accept 200 and 403 (some hosts block HEAD/GET but still play in players)
            return resp.status in (200, 403)
    except Exception:
        return False

# -------------------------
# Grab m3u8 from an iframe url — NEW page per attempt, safe listener removal
# -------------------------
async def grab_m3u8_from_iframe(context, iframe_url: str, aio_session: aiohttp.ClientSession) -> Set[str]:
    """
    Open a fresh page, attach response listener, navigate to iframe_url,
    simulate a click, and capture a .m3u8 URL from responses or HTML.
    Returns a set of validated m3u8 urls (maybe empty).
    """
    found_urls: Set[str] = set()
    page = await context.new_page()
    first_url = None

    # response handler - capture .m3u8 responses
    def _on_response(response):
        nonlocal first_url
        try:
            url = response.url
            if ".m3u8" in url and not first_url:
                first_url = url
        except Exception:
            pass

    try:
        page.on("response", _on_response)
        # try navigation (several possible timeouts or failures)
        try:
            await page.goto(iframe_url, timeout=12_000, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            # sometimes commit/load is enough, try again with longer timeout
            try:
                await page.goto(iframe_url, timeout=25_000, wait_until="load")
            except Exception:
                # give up navigation but we still continue - sometimes iframe content loads via JS
                pass
        except Exception:
            pass

        # small initial sleep (non-Playwright)
        await asyncio.sleep(0.35)

        # attempt to click at center to trigger players/ads that reveal .m3u8
        try:
            await page.mouse.click(200, 200)
        except Exception:
            # ignore click errors
            pass

        # wait for up to MAX_WAIT total seconds for a .m3u8 to be captured via responses
        MAX_WAIT = 8.0
        waited = 0.0
        INTERVAL = 0.05
        while waited < MAX_WAIT and not first_url:
            await asyncio.sleep(INTERVAL)
            waited += INTERVAL

        # fallback: search HTML for .m3u8 patterns
        if not first_url:
            try:
                html = await page.content()
                m = re.search(r'https?://[^\s"\'<>]+\.m3u8(?:\?[^"\'<>]*)?', html)
                if m:
                    first_url = m.group(0)
            except Exception:
                pass

        # if we found candidate, validate
        if first_url:
            ok = await check_m3u8_url(aio_session, first_url, iframe_url)
            if ok:
                found_urls.add(first_url)

    except PlaywrightError as e:
        print(f"⚠️ Playwright error in grab_m3u8_from_iframe: {e}")
    except Exception as e:
        print(f"⚠️ Unexpected error in grab_m3u8_from_iframe: {e}")
    finally:
        # best-effort removal of listener
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            try:
                page.off("response", _on_response)
            except Exception:
                pass
        try:
            if not page.is_closed():
                await page.close()
        except Exception:
            pass

    return found_urls

# -------------------------
# Scrape "Live Now" HTML cards
# -------------------------
async def grab_live_now_from_html(context, aio_session: aiohttp.ClientSession, base_url="https://ppv.to/"):
    print("🌐 Scraping 'Live Now' streams from HTML...")
    streams = []
    page = await context.new_page()
    try:
        try:
            await page.goto(base_url, timeout=20_000, wait_until="domcontentloaded")
        except Exception:
            pass
        await asyncio.sleep(1.5)
        cards = await page.query_selector_all("#livecards a.item-card")
        for card in cards:
            href = await card.get_attribute("href")
            name_el = await card.query_selector(".card-title")
            poster_el = await card.query_selector("img.card-img-top")
            name = (await name_el.inner_text()).strip() if name_el else "Unnamed Live"
            poster = await poster_el.get_attribute("src") if poster_el else None
            if href:
                iframe_url = href if href.startswith("http") else base_url.rstrip("/") + href
                streams.append({
                    "name": name,
                    "iframe": iframe_url,
                    "category": "Live Now",
                    "poster": poster,
                    "starts_at": -1,
                    "clock_time": "LIVE"
                })
    except Exception as e:
        print(f"❌ Failed scraping 'Live Now': {e}")
    finally:
        try:
            await page.close()
        except Exception:
            pass

    print(f"✅ Found {len(streams)} 'Live Now' streams")
    return streams

# -------------------------
# API fetch
# -------------------------
async def get_streams(session: aiohttp.ClientSession):
    try:
        print(f"🌐 Fetching streams from {API_URL}")
        async with session.get(API_URL, timeout=30) as resp:
            print(f"🔍 Response status: {resp.status}")
            if resp.status != 200:
                text = await resp.text()
                print(f"❌ API returned error: {text[:500]}")
                return None
            return await resp.json()
    except Exception as e:
        print(f"❌ Error in get_streams: {e}")
        return None

# -------------------------
# Build M3U
# -------------------------
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

# -------------------------
# Main workflow with browser restart resilience
# -------------------------
async def main():
    print("🚀 Starting PPV Stream Fetcher (robust mode)")

    # single aiohttp session reused for checks
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as aio_session:
        data = await get_streams(aio_session)
        if not data or "streams" not in data:
            print("❌ No valid data received from API")
            return

        # Normalize streams list
        streams_list = []
        for cat_obj in data["streams"]:
            cat = cat_obj.get("category", "")
            for stream in cat_obj.get("streams", []):
                iframe = stream.get("iframe")
                name = stream.get("name") or "Unknown"
                poster = stream.get("poster")
                starts_at = stream.get("starts_at", 0)
                sort_key = float('inf') if cat == "24/7 Streams" else (starts_at or 0)
                clock_str = "" if cat == "24/7 Streams" else get_display_time(starts_at)
                if iframe:
                    streams_list.append({
                        "name": name,
                        "iframe": iframe,
                        "category": cat,
                        "poster": poster,
                        "starts_at": sort_key,
                        "clock_time": clock_str
                    })

        # dedupe & sort
        seen = set()
        unique = []
        for s in streams_list:
            k = s["name"].lower()
            if k not in seen:
                seen.add(k)
                unique.append(s)
        streams_list = sorted(unique, key=lambda x: x["starts_at"])

        # Grab "live now" pages (optional)
        # We'll attempt to visit live-cards using a temporary browser page later.

        # Browser lifecycle with restart resilience
        max_restarts = 2
        restarts = 0
        url_map: Dict[str, Set[str]] = {}
        CHUNK = 40  # number of streams per browser instance (prevents memory leak)

        idx = 0
        total = len(streams_list)
        while idx < total:
            # start browser
            try:
                async with async_playwright() as p:
                    # prefer chromium for CI stability
                    browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
                    context = await browser.new_context()
                    # process CHUNK streams then restart browser to avoid leaks/crashes
                    end = min(idx + CHUNK, total)
                    subset = streams_list[idx:end]
                    page_count = 0
                    for s in subset:
                        page_count += 1
                        display_name = s['name']
                        if s.get("category") != "24/7 Streams" and s.get("clock_time"):
                            display_name = f"{s['name']} [{s['clock_time']}]"
                        print(f"\n🔎 Scraping stream {idx+1}/{total}: {display_name} [{s['category']}]")
                        key = f"{s['name']}::{s['category']}::{s['iframe']}"
                        try:
                            urls = await grab_m3u8_from_iframe(context, s["iframe"], aio_session)
                            url_map[key] = urls
                        except Exception as e:
                            print(f"⚠️ Error while scraping {s['iframe']}: {e}")
                        idx += 1

                    # also capture Live Now cards from the same browser/context
                    try:
                        live_now = await grab_live_now_from_html(context, aio_session)
                        for s in live_now:
                            key = f"{s['name']}::{s['category']}::{s['iframe']}"
                            url_map[key] = await grab_m3u8_from_iframe(context, s["iframe"], aio_session)
                        # prepend live_now to streams for output ordering
                        streams_list = live_now + streams_list
                        total = len(streams_list)
                    except Exception as e:
                        print(f"⚠️ Live-now scraping failed: {e}")

                    # close browser context (async with does on exit), then loop to possibly restart
                    try:
                        await context.close()
                    except Exception:
                        pass
                    try:
                        await browser.close()
                    except Exception:
                        pass

                    # reset restart count on success
                    restarts = 0

            except PlaywrightError as e:
                restarts += 1
                print(f"❌ Playwright error, restarting browser ({restarts}/{max_restarts}): {e}")
                if restarts > max_restarts:
                    print("❌ Too many browser failures, aborting.")
                    break
                # brief backoff before restart
                await asyncio.sleep(3)

            except Exception as e:
                print(f"❌ Unexpected outer loop error: {e}")
                break

        # Build playlist using url_map
        print("\n💾 Writing final playlist to PPVLand.m3u8 ...")
        playlist = build_m3u(streams_list, url_map)
        with open("PPVLand.m3u8", "w", encoding="utf-8") as f:
            f.write(playlist)

        print(f"✅ Done! Playlist saved as PPVLand.m3u8 at {datetime.utcnow().isoformat()} UTC")

if __name__ == "__main__":
    asyncio.run(main())
