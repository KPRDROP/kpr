#!/usr/bin/env python3

import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, quote_plus

import httpx
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

# ================= TEAM LIST (Static - avoids homepage parsing) =================

TEAM_SLUGS = [
    "arizona-diamondbacks",
    "atlanta-braves",
    "baltimore-orioles",
    "boston-red-sox",
    "chicago-cubs",
    "chicago-white-sox",
    "cincinnati-reds",
    "cleveland-guardians",
    "colorado-rockies",
    "detroit-tigers",
    "houston-astros",
    "kansas-city-royals",
    "los-angeles-angels",
    "los-angeles-dodgers",
    "miami-marlins",
    "milwaukee-brewers",
    "minnesota-twins",
    "new-york-mets",
    "new-york-yankees",
    "oakland-athletics",
    "philadelphia-phillies",
    "pittsburgh-pirates",
    "san-diego-padres",
    "san-francisco-giants",
    "seattle-mariners",
    "st-louis-cardinals",
    "tampa-bay-rays",
    "texas-rangers",
    "toronto-blue-jays",
    "washington-nationals",
]

# Team logo mapping
TEAM_LOGOS = {
    "arizona-diamondbacks": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Arizona-Diamondbacks.svg",
    "atlanta-braves": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Atlanta-Braves.svg",
    "baltimore-orioles": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Baltimore-Orioles.svg",
    "boston-red-sox": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Boston-Red-Sox.svg",
    "chicago-cubs": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Chicago-Cubs.svg",
    "chicago-white-sox": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Chicago-White-Sox.svg",
    "cincinnati-reds": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Cincinnati-Reds.svg",
    "cleveland-guardians": "https://mlbwebcast.com/wp-content/uploads/2023/04/Logo-Cleveland-Guardians.svg",
    "colorado-rockies": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Colorado-Rockies.svg",
    "detroit-tigers": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Detroit-Tigers.svg",
    "houston-astros": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Houston-Astros.svg",
    "kansas-city-royals": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Kansas-City-Royals.svg",
    "los-angeles-angels": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Los-Angeles-Angels.svg",
    "los-angeles-dodgers": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Los-Angeles-Dodgers.svg",
    "miami-marlins": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Miami-Marlins.svg",
    "milwaukee-brewers": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Milwaukee-Brewers.svg",
    "minnesota-twins": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Minnesota-Twins.svg",
    "new-york-mets": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-New-York-Mets.svg",
    "new-york-yankees": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-New-York-Yankees.svg",
    "oakland-athletics": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Oakland-Athletics.svg",
    "philadelphia-phillies": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Philadelphia-Phillies.svg",
    "pittsburgh-pirates": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Pittsburgh-Pirates.svg",
    "san-diego-padres": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-San-Diego-Padres.svg",
    "san-francisco-giants": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-San-Francisco-Giants.svg",
    "seattle-mariners": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Seattle-Mariners.svg",
    "st-louis-cardinals": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-St-Louis-Cardinals.svg",
    "tampa-bay-rays": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Tampa-Bay-Rays.svg",
    "texas-rangers": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Texas-Rangers.svg",
    "toronto-blue-jays": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Toronto-Blue-Jays.svg",
    "washington-nationals": "https://mlbwebcast.com/wp-content/uploads/2023/02/Logo-Washington-Nationals.svg",
}

# ================= HELPERS =================

def log(msg):
    print(msg, flush=True)


def clean_event_name(slug: str) -> str:
    """Convert slug to readable team name"""
    name = slug.replace("-", " ").title()
    # Special cases
    replacements = {
        "Mlb Network": "MLB Network",
        "Fox Sports": "FOX Sports",
    }
    return replacements.get(name, name)


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


# ================= HTTP REQUEST =================

async def fetch_html(url: str) -> str | None:
    """Fetch HTML content with proper headers"""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": BASE_URL,
        "Origin": BASE_URL,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            log(f"  Fetch error: {e}")
            return None


# ================= STREAM CAPTURE =================

async def capture_stream(team_slug: str, team_name: str) -> str | None:
    """Capture m3u8 stream URL for a team"""
    
    team_url = f"{BASE_URL.rstrip('/')}/{team_slug}-live"
    log(f"  Processing: {team_name} ({team_url})")
    
    # Step 1: Fetch team page
    html = await fetch_html(team_url)
    if not html:
        log(f"  Failed to fetch team page")
        return None
    
    soup = HTMLParser(html)
    
    # Step 2: Find iframe with name="srcFrame"
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
    
    # Step 3: Fetch iframe content
    iframe_html = await fetch_html(iframe_src)
    if not iframe_html:
        log(f"  Failed to fetch iframe")
        return None
    
    # Step 4: Extract event data from JavaScript
    # Look for patterns like: var params=[134,1781299050,'9c323f2f8d80ab4e'];
    patterns = [
        r'var\s+\w+\s*=\s*\[(\d+),\s*(\d+),\s*[\'"](\w+)[\'"]\];',
        r'\[(\d+),\s*(\d+),\s*[\'"](\w+)[\'"]\]',
        r'id["\']?\s*:\s*(\d+).*?ts["\']?\s*:\s*(\d+).*?pt["\']?\s*:\s*[\'"]([^\'"]+)[\'"]',
    ]
    
    ev_id = ev_ts = ev_pt = None
    
    for pattern in patterns:
        match = re.search(pattern, iframe_html, re.DOTALL | re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) >= 3:
                ev_id = groups[0]
                ev_ts = groups[1]
                ev_pt = groups[2]
                break
    
    if not ev_id:
        # Try to find in script tags
        for script in soup.css("script"):
            script_text = script.text()
            for pattern in patterns:
                match = re.search(pattern, script_text, re.DOTALL)
                if match:
                    groups = match.groups()
                    if len(groups) >= 3:
                        ev_id = groups[0]
                        ev_ts = groups[1]
                        ev_pt = groups[2]
                        break
            if ev_id:
                break
    
    if not ev_id:
        log(f"  Could not extract event data")
        return None
    
    log(f"  Event data: id={ev_id}, ts={ev_ts}, pt={ev_pt[:20]}...")
    
    # Step 5: Call check_stream.php API
    api_url = urljoin(BASE_URL, "stream/check_stream.php")
    api_params = {
        "id": ev_id,
        "ts": ev_ts,
        "pt": ev_pt,
    }
    
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": team_url,
        "Origin": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
    }
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            response = await client.get(api_url, headers=headers, params=api_params)
            data = response.json()
            
            if data.get("error"):
                log(f"  API error: {data.get('error')}")
                return None
            
            stream_url = data.get("url")
            if stream_url:
                log(f"  ✓ Captured stream")
                return stream_url
            else:
                log(f"  No URL in response")
                return None
                
        except Exception as e:
            log(f"  API request failed: {e}")
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
    
    # Build events from static team list
    events = []
    for slug in TEAM_SLUGS:
        events.append({
            "slug": slug,
            "name": clean_event_name(slug),
            "logo": TEAM_LOGOS.get(slug, DEFAULT_LOGO),
        })
    
    log(f"Loaded {len(events)} teams")
    
    collected = []
    
    for i, ev in enumerate(events, 1):
        key = f"[MLB] {ev['name']} ({TAG})"
        
        # Check cache
        if key in cache and now - cache[key]["ts"] < CACHE_EXP:
            log(f"[{i}/{len(events)}] {ev['name']} (cached)")
            collected.append(cache[key]["data"])
            continue
        
        log(f"[{i}/{len(events)}] {ev['name']}")
        
        # Capture stream
        stream_url = await capture_stream(ev["slug"], ev["name"])
        
        if stream_url:
            entry = {
                "name": f"[MLB] {ev['name']}",
                "url": stream_url,
                "logo": ev["logo"]
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
