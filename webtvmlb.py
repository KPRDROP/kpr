#!/usr/bin/env python3

import asyncio
import ast
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, quote_plus

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from selectolax.parser import HTMLParser

# ================= CONFIG =================

BASE_URL = os.environ.get("WEBTV_MLB_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing WEBTV_MLB_BASE_URL secret")

REFERER = BASE_URL
ORIGIN = BASE_URL.rstrip("/")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)

UA_ENC = quote_plus(USER_AGENT)

OUT_VLC = Path("webtvmlb_vlc.m3u8")
OUT_TIVI = Path("webtvmlb_tivimate.m3u8")

CACHE_FILE = "webtvmlb_cache.json"
CACHE_EXP = 3 * 60 * 60

TVG_ID = "MLB.Baseball.Dummy.us"
GROUP = "Live Events"
DEFAULT_LOGO = "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/mlb.png"

TAG = "EMELB"

# ================= TEAM LIST (Fallback if homepage parsing fails) =================

FALLBACK_TEAMS = [
    ("arizona-diamondbacks", "Arizona Diamondbacks", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Arizona-Diamondbacks.svg"),
    ("atlanta-braves", "Atlanta Braves", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Atlanta-Braves.svg"),
    ("baltimore-orioles", "Baltimore Orioles", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Baltimore-Orioles.svg"),
    ("boston-red-sox", "Boston Red Sox", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Boston-Red-Sox.svg"),
    ("chicago-cubs", "Chicago Cubs", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Chicago-Cubs.svg"),
    ("chicago-white-sox", "Chicago White Sox", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Chicago-White-Sox.svg"),
    ("cincinnati-reds", "Cincinnati Reds", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Cincinnati-Reds.svg"),
    ("cleveland-guardians", "Cleveland Guardians", "https://mlbwebcast.com/wp-content/uploads/2023/04/Logo-Cleveland-Guardians.svg"),
    ("colorado-rockies", "Colorado Rockies", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Colorado-Rockies.svg"),
    ("detroit-tigers", "Detroit Tigers", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Detroit-Tigers.svg"),
    ("houston-astros", "Houston Astros", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Houston-Astros.svg"),
    ("kansas-city-royals", "Kansas City Royals", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Kansas-City-Royals.svg"),
    ("los-angeles-angels", "Los Angeles Angels", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Los-Angeles-Angels.svg"),
    ("los-angeles-dodgers", "Los Angeles Dodgers", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Los-Angeles-Dodgers.svg"),
    ("miami-marlins", "Miami Marlins", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Miami-Marlins.svg"),
    ("milwaukee-brewers", "Milwaukee Brewers", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Milwaukee-Brewers.svg"),
    ("minnesota-twins", "Minnesota Twins", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Minnesota-Twins.svg"),
    ("new-york-mets", "New York Mets", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-New-York-Mets.svg"),
    ("new-york-yankees", "New York Yankees", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-New-York-Yankees.svg"),
    ("oakland-athletics", "Oakland Athletics", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Oakland-Athletics.svg"),
    ("philadelphia-phillies", "Philadelphia Phillies", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Philadelphia-Phillies.svg"),
    ("pittsburgh-pirates", "Pittsburgh Pirates", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Pittsburgh-Pirates.svg"),
    ("san-diego-padres", "San Diego Padres", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-San-Diego-Padres.svg"),
    ("san-francisco-giants", "San Francisco Giants", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-San-Francisco-Giants.svg"),
    ("seattle-mariners", "Seattle Mariners", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Seattle-Mariners.svg"),
    ("st-louis-cardinals", "St. Louis Cardinals", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-St-Louis-Cardinals.svg"),
    ("tampa-bay-rays", "Tampa Bay Rays", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Tampa-Bay-Rays.svg"),
    ("texas-rangers", "Texas Rangers", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Texas-Rangers.svg"),
    ("toronto-blue-jays", "Toronto Blue Jays", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Toronto-Blue-Jays.svg"),
    ("washington-nationals", "Washington Nationals", "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Washington-Nationals.svg"),
]

# ================= HELPERS =================

def log(msg):
    print(msg, flush=True)


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ================= FETCH PAGE WITH PLAYWRIGHT =================

async def fetch_page_with_playwright(url: str) -> str | None:
    """Fetch page content using Playwright to bypass 403/Cloudflare"""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={'width': 1280, 'height': 720},
                extra_http_headers={
                    "Referer": BASE_URL,
                    "Origin": BASE_URL,
                }
            )
            page = await context.new_page()
            
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                content = await page.content()
                return content
            finally:
                await browser.close()
    except Exception as e:
        log(f"  Playwright fetch error: {e}")
        return None


# ================= EVENT DETECTION (using Playwright) =================

async def get_events():
    """Extract team events from homepage using Playwright to bypass 403"""
    log(f"Loading homepage with Playwright: {BASE_URL}")
    
    html = await fetch_page_with_playwright(BASE_URL)
    if not html:
        log("Failed to load homepage, using fallback team list")
        # Use fallback team list
        events = []
        for slug, name, logo in FALLBACK_TEAMS:
            events.append({
                "sport": "MLB",
                "event": name,
                "link": f"{BASE_URL.rstrip('/')}/{slug}-live",
                "logo": logo
            })
        return events
    
    soup = HTMLParser(html)
    events = []
    
    # Find team logo links
    team_links = soup.css("li.team-logo a")
    
    if not team_links:
        team_links = soup.css("a[href*='-live']")
    
    if not team_links:
        # Try looking in the team-logo div
        team_div = soup.css_first("#team-logo")
        if team_div:
            team_links = team_div.css("a")
    
    log(f"Found {len(team_links)} team links on homepage")
    
    for a in team_links:
        href = a.attributes.get("href")
        if not href:
            continue
        
        # Only include team links
        if "-live" not in href:
            continue
        
        link = urljoin(BASE_URL, href)
        
        # Get team name from title attribute
        title = a.attributes.get("title", "")
        event_name = title.replace("Live Stream", "").strip()
        
        if not event_name:
            # Extract from href
            slug = href.rstrip("/").split("/")[-1].replace("-live", "")
            event_name = slug.replace("-", " ").title()
        
        # Get logo
        logo = DEFAULT_LOGO
        img = a.css_first("img")
        if img:
            src = img.attributes.get("src")
            if src:
                logo = src
        
        events.append({
            "sport": "MLB",
            "event": event_name,
            "link": link,
            "logo": logo
        })
    
    if not events:
        log("No events found on homepage, using fallback team list")
        for slug, name, logo in FALLBACK_TEAMS:
            events.append({
                "sport": "MLB",
                "event": name,
                "link": f"{BASE_URL.rstrip('/')}/{slug}-live",
                "logo": logo
            })
    
    return events


# ================= STREAM CAPTURE =================

async def capture_stream(team_url: str, team_name: str, team_logo: str) -> str | None:
    """Capture m3u8 stream URL for a team using Playwright"""
    
    log(f"  Fetching team page: {team_url}")
    
    # Fetch team page with Playwright
    html = await fetch_page_with_playwright(team_url)
    if not html:
        log(f"  Failed to fetch team page")
        return None
    
    soup = HTMLParser(html)
    
    # Find iframe with name="srcFrame"
    iframe = soup.css_first('iframe[name="srcFrame"]')
    if not iframe:
        iframe = soup.css_first('iframe[src*="stream"]')
    
    if not iframe:
        log(f"  No iframe found")
        return None
    
    iframe_src = iframe.attributes.get("src")
    if not iframe_src:
        log(f"  No iframe src")
        return None
    
    log(f"  Found iframe: {iframe_src[:80]}...")
    
    # Fetch iframe content with Playwright
    iframe_html = await fetch_page_with_playwright(iframe_src)
    if not iframe_html:
        log(f"  Failed to fetch iframe")
        return None
    
    # Extract event data from JavaScript
    # Pattern: var params=[134,1781299050,'9c323f2f8d80ab4e'];
    pattern = re.compile(r'var\s+\w*\s*=\s*\[(\d+),\s*(\d+),\s*[\'"]([a-fA-F0-9]+)[\'"]\];', re.DOTALL)
    
    match = pattern.search(iframe_html)
    if not match:
        # Try alternative pattern
        pattern2 = re.compile(r'\[(\d+),\s*(\d+),\s*[\'"]([a-fA-F0-9]+)[\'"]\]', re.DOTALL)
        match = pattern2.search(iframe_html)
    
    if not match:
        log(f"  Could not extract event data")
        return None
    
    ev_id = match.group(1)
    ev_ts = match.group(2)
    ev_pt = match.group(3)
    
    log(f"  Event data: id={ev_id}, ts={ev_ts}, pt={ev_pt[:20]}...")
    
    # Build API URL
    api_url = urljoin(BASE_URL, f"stream/check_stream.php?id={ev_id}&ts={ev_ts}&pt={ev_pt}")
    
    log(f"  Calling API: {api_url[:100]}...")
    
    # Fetch API response with Playwright
    api_html = await fetch_page_with_playwright(api_url)
    if not api_html:
        log(f"  Failed to fetch API")
        return None
    
    # Parse JSON response
    try:
        # The API returns JSON, but might have HTML wrapper
        json_match = re.search(r'\{[^{}]*"url"[^{}]*\}', api_html)
        if json_match:
            data = json.loads(json_match.group(0))
        else:
            data = json.loads(api_html)
        
        if data.get("error"):
            log(f"  API error: {data.get('error')}")
            return None
        
        stream_url = data.get("url")
        if stream_url:
            log(f"  ✓ Stream captured")
            return stream_url
        else:
            log(f"  No URL in API response")
            return None
            
    except json.JSONDecodeError as e:
        log(f"  JSON parse error: {e}")
        return None


# ================= WRITE OUTPUT =================

def write_outputs(entries):
    if not entries:
        log("No URLs to write")
        return

    # VLC
    with open(OUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, e in enumerate(entries, 1):
            safe_name = e["name"].replace(",", "").strip()
            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{TVG_ID}" '
                f'tvg-name="{safe_name}" tvg-logo="{e.get("logo", DEFAULT_LOGO)}" '
                f'group-title="{GROUP}",{safe_name}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={REFERER}\n")
            f.write(f"#EXTVLCOPT:http-origin={ORIGIN}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{e['url']}\n\n")

    # TiviMate
    with open(OUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, e in enumerate(entries, 1):
            safe_name = e["name"].replace(",", "").strip()
            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{TVG_ID}" '
                f'tvg-name="{safe_name}" tvg-logo="{e.get("logo", DEFAULT_LOGO)}" '
                f'group-title="{GROUP}",{safe_name}\n'
            )
            f.write(
                f"{e['url']}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}\n\n"
            )

    log(f"Playlists generated: {OUT_VLC} / {OUT_TIVI}")


# ================= MAIN =================

async def main():
    log("Starting MLB WebTV updater...")
    
    cache = load_cache()
    now = int(time.time())
    
    # Get events from homepage (with Playwright to bypass 403)
    events = await get_events()
    log(f"Detected {len(events)} events")
    
    if not events:
        log("No events found")
        return
    
    collected = []
    
    for i, ev in enumerate(events, 1):
        key = f"[{ev['sport']}] {ev['event']} ({TAG})"
        
        # Check cache
        if key in cache and now - cache[key]["ts"] < CACHE_EXP:
            log(f"[{i}/{len(events)}] {ev['event']} (cached)")
            collected.append(cache[key]["data"])
            continue
        
        log(f"[{i}/{len(events)}] {ev['event']}")
        
        # Capture stream
        stream_url = await capture_stream(ev["link"], ev["event"], ev.get("logo", DEFAULT_LOGO))
        
        if stream_url:
            entry = {
                "name": f"[MLB] {ev['event']}",
                "url": stream_url,
                "logo": ev.get("logo", DEFAULT_LOGO)
            }
            
            cache[key] = {
                "ts": now,
                "data": entry
            }
            
            collected.append(entry)
        else:
            log(f"  ✗ No stream found")
    
    save_cache(cache)
    write_outputs(collected)


# ================= RUN =================

if __name__ == "__main__":
    asyncio.run(main())
