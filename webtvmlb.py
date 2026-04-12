#!/usr/bin/env python3

import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus, urljoin

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

# ================= HELPERS =================

def log(msg):
    print(msg, flush=True)


def clean_event_name(text: str) -> str:
    text = text.replace("@", "vs")
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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


# ================= EVENT DETECTION =================

async def get_events(page):
    log(f"Loading page with Playwright: {BASE_URL}")

    await page.goto(BASE_URL, timeout=30000)
    await page.wait_for_timeout(5000)

    html = await page.content()
    soup = HTMLParser(html)

    events = []
    seen = set()

    # MAIN selector (NEW STRUCTURE)
    for a in soup.css("a[href*='-live']"):
        href = a.attributes.get("href")
        title = a.attributes.get("title", "").strip()

        if not href:
            continue

        url = urljoin(BASE_URL, href)

        if url in seen:
            continue
        seen.add(url)

        name = clean_event_name(title) if title else "MLB TV"

        events.append({
            "event": name,
            "link": url
        })

    return events


# ================= STREAM CAPTURE =================

async def capture_stream(page, url, idx):
    stream_url = None

    def handle_response(res):
        nonlocal stream_url
        try:
            if ".m3u8" in res.url and not stream_url:
                stream_url = res.url
        except:
            pass

    page.on("response", handle_response)

    try:
        # STEP 1: open homepage first
        await page.goto(BASE_URL, timeout=30000)
        await page.wait_for_timeout(2000)

        # STEP 2: open event with referer
        await page.goto(url, timeout=30000, referer=BASE_URL)
        await page.wait_for_timeout(5000)

        # STEP 3: click to trigger player
        for _ in range(3):
            try:
                await page.mouse.click(500, 400)
                await asyncio.sleep(1)
            except:
                pass

        # STEP 4: iframe clicks
        for frame in page.frames:
            try:
                await frame.click("body", timeout=2000)
                await asyncio.sleep(1)
            except:
                pass

        # STEP 5: wait for stream
        waited = 0
        while waited < 20 and not stream_url:
            await asyncio.sleep(1)
            waited += 1

        # STEP 6: fallback regex
        if not stream_url:
            html = await page.content()
            m = re.search(r'https?://[^\s"\']+\.m3u8[^\s"\']*', html)
            if m:
                stream_url = m.group(0)

    except Exception as e:
        log(f"[{idx}] ERROR: {e}")

    finally:
        try:
            page.remove_listener("response", handle_response)
        except:
            pass

    return stream_url


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
                f"{e['url']}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}\n"
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

        collected = []

        for i, ev in enumerate(events, 1):
            key = ev["event"]

            if key in cache and now - cache[key]["ts"] < CACHE_EXP:
                collected.append(cache[key]["data"])
                continue

            log(f"[{i}/{len(events)}] {ev['event']}")

            p2 = await context.new_page()
            stream = await capture_stream(p2, ev["link"], i)
            await p2.close()

            if stream:
                log(f"STREAM FOUND: {stream}")

                entry = {
                    "name": f"[MLB] {ev['event']} (WEBCAST)",
                    "url": stream
                }

                cache[key] = {
                    "ts": now,
                    "data": entry
                }

                collected.append(entry)
            else:
                log("No stream found")

        await browser.close()

    save_cache(cache)
    write_outputs(collected)


# ================= RUN =================

if __name__ == "__main__":
    asyncio.run(main())
