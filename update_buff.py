#!/usr/bin/env python3
"""
update_buff.py

Scrape https://buffstreams.plus/ main page for event links and extract
JS-driven playlist URLs. Produce two playlists:
 - BuffStreams_VLC.m3u8
 - BuffStreams_TiviMate.m3u8
"""

import asyncio
import re
import html
from urllib.parse import quote, urljoin
from datetime import datetime

from bs4 import BeautifulSoup
import requests
from playwright.async_api import async_playwright

BASE_URL = "https://buffstreams.plus/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "BuffStreams_VLC.m3u8"
TIVIMATE_OUTPUT = "BuffStreams_TiviMate.m3u8"

HEADERS = {"User-Agent": USER_AGENT, "Referer": REFERER, "Accept-Language": "en-US,en;q=0.9"}

# Metadata dictionary
TV_INFO = {
    "misc": ("BuffStreams.Dummy.us", "https://i.postimg.cc/HsWHFvV0/Soccer.png", "BuffStreams")
}

# Regex to find playlist URLs in iframe content
PLAYLIST_RE = re.compile(r"https?://[^\s\"'<>]+/playlist/[^\s\"'<>|]+")

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def fetch(url, timeout=12):
    """Fetch page HTML."""
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  ❌ fetch failed: {url} -> {e}")
        return ""

def clean_event_title(raw_title):
    if not raw_title:
        return ""
    t = html.unescape(raw_title).strip()
    t = " ".join(t.split())
    t = re.sub(r"\s*-\s*BuffStreams.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch Live.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Watch.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*-\s*Live Stream.*$", "", t, flags=re.IGNORECASE)
    t = t.strip(" -,:")
    return t

def get_event_candidates(html_text):
    """Return list of (anchor_text, href) for potential event links on main page."""
    soup = BeautifulSoup(html_text, "html.parser")
    candidates = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True) or ""
        if not href or href.startswith(("mailto:", "javascript:")):
            continue
        full = href if href.startswith("http") else urljoin(BASE_URL, href)
        # Heuristic: contains stream/match/game or ends with digits
        low = href.lower()
        if any(k in low for k in ("stream", "streams", "match", "game", "event")) or re.search(r"-\d+$", low):
            if full not in seen:
                seen.add(full)
                candidates.append((text.strip(), full))
    print(f"✅ Found {len(candidates)} potential event links.")
    return candidates

async def get_event_stream(event_url):
    """Load event page via Playwright, click player, extract playlist URL."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await page.goto(event_url, timeout=30000)

        # Wait for iframe
        try:
            iframe_el = await page.wait_for_selector("iframe", timeout=10000)
            frame = await iframe_el.content_frame()
            if frame:
                # Try clicking play button if exists
                try:
                    await frame.click("button.play, .play-button", timeout=5000)
                except:
                    pass
                # Allow JS to generate playlist
                await asyncio.sleep(2)
                # Check iframe src
                src = await iframe_el.get_attribute("src")
                if src and "playlist" in src:
                    await browser.close()
                    return src
                # Inspect iframe HTML for playlist
                content = await frame.content()
                m = PLAYLIST_RE.search(content)
                if m:
                    await browser.close()
                    return m.group(0)
        except:
            pass
        await browser.close()
    return None

def write_playlists(streams):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

    ua_enc = quote(USER_AGENT, safe="")

    # VLC playlist
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for ev_name, url in streams:
            tvg_id, logo, group_name = TV_INFO["misc"]
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="{group_name}",{ev_name}\n')
            f.write(f'{url}\n\n')

    # TiviMate playlist
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for ev_name, url in streams:
            tvg_id, logo, group_name = TV_INFO["misc"]
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tvg_id}" group-title="{group_name}",{ev_name}\n')
            f.write(f'{url}|referer={BASE_URL}|user-agent={ua_enc}\n\n')

async def main():
    print("▶️ Starting BuffStreams playlist generation...")
    html_text = fetch(BASE_URL)
    candidates = get_event_candidates(html_text)

    streams = []
    seen_urls = set()

    for title, href in candidates:
        stream_url = await get_event_stream(href)
        if stream_url and stream_url not in seen_urls:
            seen_urls.add(stream_url)
            streams.append((clean_event_title(title) or stream_url, stream_url))

    if not streams:
        print("⚠️ No streams found.")
    else:
        print(f"✅ Found {len(streams)} streams.")

    write_playlists(streams)
    print(f"✅ Finished. Playlists written:\n - {VLC_OUTPUT}\n - {TIVIMATE_OUTPUT}")

if __name__ == "__main__":
    asyncio.run(main())
