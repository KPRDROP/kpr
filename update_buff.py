#!/usr/bin/env python3
"""
update_buff.py
--------------
BuffStreams Root Scraper (Deep Playwright version)

✅ Scans only main page (https://buffstreams.plus/)
✅ Captures stream URLs injected by JS or inside iframes
✅ Encodes referer + user-agent for TiviMate
✅ Writes clean #EXTM3U playlist
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
    "default": ("Sports.Dummy.us", "https://i.postimg.cc/qMm0rc3L/247.png", "Live Sports"),
}

STREAM_REGEX = re.compile(
    r"https?://[a-zA-Z0-9\.\-_/]+/(playlist|load-playlist)[^\s\"'<>`]+",
    re.IGNORECASE,
)

async def collect_all_streams(context, page):
    """Capture all network requests and iframe HTMLs containing playlist URLs."""
    found_streams = set()

    # Network event listener
    def on_request(request):
        url = request.url
        if re.search(STREAM_REGEX, url):
            found_streams.add(url)

    context.on("request", on_request)

    # Load main page
    print(f"🌐 Visiting main page: {BASE_URL}")
    await page.goto(BASE_URL, timeout=90000)
    await page.wait_for_load_state("networkidle")
    await asyncio.sleep(10)  # Allow extra JS time

    # Check all frames (main + iframes)
    for frame in page.frames:
        try:
            html = await frame.content()
            matches = re.findall(STREAM_REGEX, html)
            for match in matches:
                found_streams.add(match)
        except Exception:
            continue

    return found_streams


async def main():
    print("▶️ Starting BuffStreams playlist generation...\n")

    header = [
        '#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"',
        f"# Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n",
    ]
    playlist_lines = header[:]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        streams = await collect_all_streams(context, page)

        print(f"✅ Found {len(streams)} potential streams.\n")

        for s in streams:
            tv_id, logo, group = TV_INFO["default"]
            title = s.split("/")[-1].replace("-", " ").title()

            playlist_lines.append(
                f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tv_id}" group-title="BuffStreams - {group}",{title}'
            )
            playlist_lines.append(f"{s}|referer={REFERER}|user-agent={ENCODED_UA}")
            playlist_lines.append("")

        await browser.close()

    output_file = "BuffStreams_Playlist.m3u8"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(playlist_lines))

    print(f"\n✅ Finished. Playlist saved as {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
