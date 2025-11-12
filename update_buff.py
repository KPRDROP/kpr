#!/usr/bin/env python3
"""
update_buff.py
----------------
Scraper for https://buffstreams.plus/ using async Playwright.

✨ Features:
 - Auto-discovers categories (NFL, NBA, MLB, etc.)
 - Captures direct m3u8 / playlist URLs from HTML, iframes, and network requests
 - Encodes user-agent and referer for TiviMate compatibility
 - Writes #EXTM3U playlist with metadata and logos
"""

import asyncio
import re
import urllib.parse
from datetime import datetime
from playwright.async_api import async_playwright

BASE_URL = "https://buffstreams.plus/"
REFERER = BASE_URL
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
ENCODED_UA = urllib.parse.quote(USER_AGENT)

TV_INFO = {
    "nfl": ("Football.Dummy.us", "https://i.postimg.cc/tRNpSGCq/Maxx.png", "NFL"),
    "nba": ("NBA.Basketball.Dummy.us", "https://i.postimg.cc/jdqKB3LW/Basketball-2.png", "NBA"),
    "mlb": ("MLB.Baseball.Dummy.us", "https://i.postimg.cc/FsFmwC7K/Baseball3.png", "MLB"),
    "nhl": ("NHL.Hockey.Dummy.us", "https://i.postimg.cc/jjJGbN7F/Hockey.png", "NHL"),
    "soccer": ("Soccer.Dummy.us", "https://i.postimg.cc/HsWHFvV0/Soccer.png", "Soccer"),
    "mma": ("UFC.Fight.Pass.Dummy.us", "https://i.postimg.cc/59Sb7W9D/Combat-Sports2.png", "MMA"),
    "boxing": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/8c4GjMnH/Combat-Sports.png", "Boxing"),
    "f1": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Formula 1"),
    "ppv": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/mkj4tC62/PPV.png", "PPV"),
    "misc": ("Sports.Dummy.us", "https://i.postimg.cc/qMm0rc3L/247.png", "Random Events"),
}

STREAM_REGEX = re.compile(
    r"https?://[a-zA-Z0-9\.\-_/]+/(playlist|stream|load-playlist)[^\s\"'<>`]+",
    re.IGNORECASE,
)

async def extract_iframe_streams(page, depth=0):
    """Recursively scan iframes for hidden streams."""
    streams = set()
    iframes = page.frames
    for f in iframes:
        try:
            html = await f.content()
            found = re.findall(STREAM_REGEX, html)
            for s in found:
                streams.add(s)
        except Exception:
            pass
    if depth < 1:
        # Recurse once deeper for nested iframes
        for f in iframes:
            try:
                inner = await extract_iframe_streams(f, depth + 1)
                streams.update(inner)
            except Exception:
                continue
    return streams

async def extract_streams(page, url):
    """Visit an event URL and collect all possible playlist URLs."""
    streams = set()
    try:
        print(f"🎯 Visiting event: {url}")
        await page.goto(url, timeout=45000)
        await page.wait_for_load_state("domcontentloaded", timeout=15000)

        html = await page.content()
        for s in re.findall(STREAM_REGEX, html):
            streams.add(s)

        # Capture network requests
        for req in page.context.requests:
            if re.search(STREAM_REGEX, req.url):
                streams.add(req.url)

        # Check iframes
        iframe_streams = await extract_iframe_streams(page)
        streams.update(iframe_streams)

        print(f"  ➕ Found {len(streams)} possible streams.")
    except Exception as e:
        print(f"  ⚠️ Error visiting {url}: {e}")
    return streams

def get_tv_info(url):
    low = url.lower()
    for key, v in TV_INFO.items():
        if key in low:
            return v
    return TV_INFO["misc"]

async def main():
    print("▶️ Starting BuffStreams playlist generation...\n")
    header = [
        '#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"',
        f"# Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n",
    ]
    lines = header[:]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await page.goto(BASE_URL)
        await page.wait_for_load_state("domcontentloaded")

        print(f"🌐 Loaded {BASE_URL}")
        anchors = await page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)")
        event_links = [a for a in anchors if any(k in a.lower() for k in TV_INFO.keys())]
        event_links = list(dict.fromkeys(event_links))
        print(f"✅ Found {len(event_links)} potential event links.\n")

        for ev in event_links:
            tv_id, logo, group = get_tv_info(ev)
            title = ev.split("/")[-1].replace("-", " ").title()

            streams = await extract_streams(page, ev)
            for s in streams:
                lines.append(
                    f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tv_id}" '
                    f'group-title="BuffStreams - {group}",{title}'
                )
                lines.append(f"{s}|referer={REFERER}|user-agent={ENCODED_UA}")
                lines.append("")

        await browser.close()

    output = "BuffStreams_Playlist.m3u8"
    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n✅ Finished. Playlist saved as {output}")

if __name__ == "__main__":
    asyncio.run(main())
