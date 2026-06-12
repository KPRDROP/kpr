#!/usr/bin/env python3

import asyncio
import ast
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, quote_plus

from playwright.async_api import async_playwright
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

async def request(url, headers=None, params=None):
    """Simple HTTP request using playwright"""
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
            
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            content = await response.text()
            return type('Response', (), {'content': content, 'text': lambda: content, 'json': lambda: json.loads(content)})()
        except Exception as e:
            log(f"Request error: {e}")
            return None
        finally:
            await browser.close()


# ================= EVENT DETECTION =================

async def get_events(page):
    log(f"Loading page: {BASE_URL}")

    await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(5000)

    html = await page.content()
    soup = HTMLParser(html)

    events = []
    sport = "MLB"

    # Extract events from table rows (original working method)
    for row in soup.css("tr.singele_match_date"):
        if not (vs_node := row.css_first("td.teamvs a")):
            continue

        event_name = vs_node.text(strip=True)

        # Remove date from event name
        for span in vs_node.css("span.mtdate"):
            date = span.text(strip=True)
            event_name = event_name.replace(date, "").strip()

        if not (href := vs_node.attributes.get("href")):
            continue

        event = fix_event(event_name)
        link = urljoin(BASE_URL, href)

        events.append({
            "sport": sport,
            "event": event,
            "link": link
        })

    # Fallback: look for team-logo links if no table rows found
    if not events:
        for a in soup.css("li.team-logo a"):
            href = a.attributes.get("href")
            title = a.attributes.get("title", "").strip()
            
            if not href:
                continue
            
            link = urljoin(BASE_URL, href)
            event_name = clean_event_name(title) if title else "MLB TV"
            
            events.append({
                "sport": sport,
                "event": event_name,
                "link": link
            })

    return events


# ================= STREAM CAPTURE (Original working method) =================

async def process_event(url: str, url_num: int, sport: str) -> str | None:
    """Process event page and extract m3u8 stream URL"""
    
    # Step 1: Load the event page
    event_data = await request(url)
    if not event_data:
        log(f"URL {url_num}) Failed to load url.")
        return None

    soup = HTMLParser(event_data.content)

    # Step 2: Find iframe with name="srcFrame"
    if not (iframe := soup.css_first('iframe[name="srcFrame"]')):
        log(f"URL {url_num}) No iframe element found.")
        return None

    if not (iframe_src := iframe.attributes.get("src")):
        log(f"URL {url_num}) No iframe source found.")
        return None

    # Step 3: Load iframe source
    if not (iframe_src_data := await request(iframe_src, headers={"Referer": url})):
        log(f"URL {url_num}) Failed to load iframe source.")
        return None

    # Step 4: Extract Clappr player data from JavaScript
    pattern = re.compile(r'var\s+\w*=\[([^"]*)\];', re.I)

    if not (match := pattern.search(iframe_src_data.text())):
        log(f"URL {url_num}) No Clappr source found.")
        return None

    try:
        ev_id, ev_ts, ev_pt = ast.literal_eval(match[1])
    except ValueError:
        log(f"URL {url_num}) Failed to parse event info.")
        return None

    params: dict[str, int | str] = dict(zip(["id", "ts", "pt"], [ev_id, ev_ts, ev_pt]))

    # Step 5: Make PHP API request to get m3u8 URL
    if not (api_data := await request(
        urljoin(BASE_URL, "stream/check_stream.php"),
        headers={"Referer": iframe_src},
        params=params,
    )):
        log(f"URL {url_num}) Failed to make php request.")
        return None

    data = api_data.json()
    
    if data.get("error"):
        log(f"URL {url_num}) API returned error: {data.get('error')}")
        return None

    log(f"URL {url_num}) Captured M3U8")
    
    return data.get("url")


# ================= WRITE OUTPUT =================

def write_outputs(entries):
    if not entries:
        log("No URLs to write")
        return

    # VLC
    with open(OUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, e in enumerate(entries, 1):
            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{TVG_ID}" '
                f'tvg-name="{e["name"]}" tvg-logo="{DEFAULT_LOGO}" '
                f'group-title="{GROUP}",{e["name"]}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={REFERER}\n")
            f.write(f"#EXTVLCOPT:http-origin={ORIGIN}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{e['url']}\n\n")

    # TiviMate
    with open(OUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, e in enumerate(entries, 1):
            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{TVG_ID}" '
                f'tvg-name="{e["name"]}" tvg-logo="{DEFAULT_LOGO}" '
                f'group-title="{GROUP}",{e["name"]}\n'
            )
            f.write(
                f"{e['url']}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}\n\n"
            )

    log("Playlists generated successfully")


# ================= MAIN =================

async def main():
    log("Starting MLB WebTV updater...")

    cache = load_cache()
    now = int(time.time())

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            user_agent=USER_AGENT,
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
        cached_urls = {}

        for i, ev in enumerate(events, 1):
            key = f"[{ev['sport']}] {ev['event']} ({TAG})"

            # Check cache
            if key in cache and now - cache[key]["ts"] < CACHE_EXP:
                log(f"[{i}/{len(events)}] {ev['event']} (cached)")
                collected.append(cache[key]["data"])
                cached_urls[key] = cache[key]["data"]
                continue

            log(f"[{i}/{len(events)}] {ev['event']}")

            # Process event to get stream URL
            stream = await process_event(ev["link"], i, ev["sport"])

            if stream:
                log(f"  ✓ STREAM CAPTURED")

                entry = {
                    "name": f"[MLB] {ev['event']} (WEBCAST)",
                    "url": stream
                }

                cache[key] = {
                    "ts": now,
                    "data": entry
                }

                cached_urls[key] = entry
                collected.append(entry)
            else:
                log(f"  ✗ No stream found")

        await browser.close()

    save_cache(cache)
    write_outputs(collected)


# ================= RUN =================

if __name__ == "__main__":
    asyncio.run(main())
