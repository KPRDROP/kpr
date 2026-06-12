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

# ================= HELPERS =================

def log(msg):
    print(msg, flush=True)


def clean_event_name(text: str) -> str:
    text = text.replace("@", "vs")
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fix_event(s: str) -> str:
    """Convert @ to vs for event names"""
    return " vs ".join(s.split("@"))


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


# ================= HTTP REQUEST HELPERS =================

async def request(url, headers=None, params=None, max_retries=3):
    """Simple HTTP request using playwright with retries"""
    for attempt in range(max_retries):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=USER_AGENT)
                
                if headers:
                    await context.set_extra_http_headers(headers)
                
                page = await context.new_page()
                
                try:
                    if params:
                        from urllib.parse import urlencode
                        separator = '&' if '?' in url else '?'
                        url = f"{url}{separator}{urlencode(params)}"
                    
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    content = await page.content()
                    
                    class ResponseObj:
                        def __init__(self, content, url):
                            self.content = content
                            self._url = url
                        def text(self):
                            return self.content
                        def json(self):
                            return json.loads(self.content)
                        @property
                        def url(self):
                            return self._url
                    
                    return ResponseObj(content, url)
                finally:
                    await browser.close()
        except Exception as e:
            log(f"Request error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt == max_retries - 1:
                return None
            await asyncio.sleep(2)
    
    return None


# ================= EVENT DETECTION (from team-logo links) =================

async def get_events(page):
    """Extract team events from homepage using team-logo links (always visible)"""
    log(f"Loading page: {BASE_URL}")

    try:
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)
        
        html = await page.content()
        
    except PlaywrightTimeoutError:
        log("Page load timeout, attempting to get content anyway...")
        html = await page.content()
    except Exception as e:
        log(f"Error loading page: {e}")
        return []

    soup = HTMLParser(html)
    events = []
    sport = "MLB"

    # Extract team links from team-logo list (always visible)
    team_links = soup.css("li.team-logo a")
    log(f"Found {len(team_links)} team links")
    
    for a in team_links:
        href = a.attributes.get("href")
        title = a.attributes.get("title", "").strip()
        
        if not href:
            continue
        
        link = urljoin(BASE_URL, href)
        event_name = clean_event_name(title) if title else "MLB Team Game"
        
        # Get logo from img
        logo = DEFAULT_LOGO
        if img := a.css_first("img"):
            if src := img.attributes.get("src"):
                logo = src
        
        events.append({
            "sport": sport,
            "event": event_name,
            "link": link,
            "logo": logo
        })
        
        log(f"  Found team: {event_name} -> {link}")

    return events


# ================= STREAM CAPTURE (Original working method) =================

async def process_event(url: str, url_num: int, sport: str) -> str | None:
    """Process event page and extract m3u8 stream URL using iframe and Clappr data"""
    
    log(f"  Processing URL: {url}")
    
    # Step 1: Load the event page
    event_data = await request(url)
    if not event_data:
        log(f"  URL {url_num}) Failed to load url.")
        return None

    soup = HTMLParser(event_data.content)

    # Step 2: Find iframe with name="srcFrame"
    iframe = soup.css_first('iframe[name="srcFrame"]')
    if not iframe:
        # Try alternative iframe selectors
        iframe = soup.css_first('iframe[src*="stream"]')
    
    if not iframe:
        log(f"  URL {url_num}) No iframe element found.")
        return None

    if not (iframe_src := iframe.attributes.get("src")):
        log(f"  URL {url_num}) No iframe source found.")
        return None
    
    log(f"  Found iframe: {iframe_src[:100]}...")

    # Step 3: Load iframe source
    iframe_src_data = await request(iframe_src, headers={"Referer": url})
    if not iframe_src_data:
        log(f"  URL {url_num}) Failed to load iframe source.")
        return None

    # Step 4: Extract Clappr player data from JavaScript
    pattern = re.compile(r'var\s+\w*=\[([^"]*)\];', re.I)

    match = pattern.search(iframe_src_data.text())
    if not match:
        log(f"  URL {url_num}) No Clappr source found.")
        return None

    try:
        ev_data = ast.literal_eval(match[1])
        if len(ev_data) >= 3:
            ev_id, ev_ts, ev_pt = ev_data[0], ev_data[1], ev_data[2]
        else:
            log(f"  URL {url_num}) Invalid event data length.")
            return None
    except (ValueError, SyntaxError) as e:
        log(f"  URL {url_num}) Failed to parse event info: {e}")
        return None

    params = {
        "id": ev_id,
        "ts": ev_ts,
        "pt": ev_pt
    }

    log(f"  Making API request with params: id={ev_id}, ts={ev_ts}, pt={ev_pt}")

    # Step 5: Make PHP API request to get m3u8 URL
    api_url = urljoin(BASE_URL, "stream/check_stream.php")
    api_data = await request(
        api_url,
        headers={"Referer": iframe_src},
        params=params,
    )
    
    if not api_data:
        log(f"  URL {url_num}) Failed to make php request.")
        return None

    try:
        data = api_data.json()
    except json.JSONDecodeError:
        log(f"  URL {url_num}) Invalid JSON response.")
        return None
    
    if data.get("error"):
        log(f"  URL {url_num}) API returned error: {data.get('error')}")
        return None

    stream_url = data.get("url")
    if stream_url:
        log(f"  URL {url_num}) Captured M3U8")
        return stream_url
    else:
        log(f"  URL {url_num}) No URL in API response.")
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

        events = await get_events(page)
        log(f"Detected {len(events)} events")

        if not events:
            log("No events found")
            await browser.close()
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

            # Create a new page for each event to avoid state issues
            event_page = await context.new_page()
            try:
                stream = await process_event(ev["link"], i, ev["sport"])
            finally:
                await event_page.close()

            if stream:
                log(f"  ✓ STREAM CAPTURED")

                entry = {
                    "name": f"[MLB] {ev['event']}",
                    "url": stream,
                    "logo": ev.get("logo", DEFAULT_LOGO)
                }

                cache[key] = {
                    "ts": now,
                    "data": entry
                }

                collected.append(entry)
            else:
                log(f"  ✗ No stream found")

        await browser.close()

    save_cache(cache)
    write_outputs(collected)


# ================= RUN =================

if __name__ == "__main__":
    asyncio.run(main())
