#!/usr/bin/env python3
"""
update_buff.py

Async Playwright scraper for https://buffstreams.plus/
Extracts streams from the root page, handles JS/iframe players, click-to-play, and
produces VLC + TiviMate playlists with encoded user-agent.
"""

import asyncio
from datetime import datetime
from urllib.parse import quote
from pathlib import Path
from playwright.async_api import async_playwright

# Output files
VLC_OUTPUT = "BuffStreams_VLC.m3u8"
TIVIMATE_OUTPUT = "BuffStreams_TiviMate.m3u8"

BASE_URL = "https://buffstreams.plus/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

# TV info: fallback data for playlist groups
TV_INFO = ("BuffStreams.Dummy.us", "https://i.postimg.cc/HsWHFvV0/Soccer.png", "BuffStreams")

# Helper for playlist header
def m3u_header():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

# Async function to extract all playable streams from main page
async def extract_streams():
    streams = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        print(f"🌐 Visiting main page: {BASE_URL}")
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Query all event links; usually clickable divs or anchors
        event_links = await page.query_selector_all("a, div.event-link")
        print(f"✅ Found {len(event_links)} potential event links.")

        for idx, el in enumerate(event_links, start=1):
            try:
                href = await el.get_attribute("href")
                title = (await el.inner_text()).strip() or f"Event {idx}"
                if not href or "javascript" in href:
                    continue

                # Open in new page to isolate JS/iframe
                event_page = await context.new_page()
                await event_page.goto(href, wait_until="domcontentloaded")
                await event_page.wait_for_timeout(1500)

                # Check for iframe / player
                iframe_el = await event_page.query_selector("iframe")
                playlist_url = None

                if iframe_el:
                    frame = await iframe_el.content_frame()
                    if frame:
                        # Try to click "play" button if exists
                        try:
                            play_btn = await frame.query_selector("button, .play-button")
                            if play_btn:
                                await play_btn.click()
                                await frame.wait_for_timeout(1000)
                        except:
                            pass

                        # Attempt to get playlist from <video> or <source>
                        video_el = await frame.query_selector("video, source")
                        if video_el:
                            playlist_url = await video_el.get_attribute("src")

                        # Fallback: check JS variables / window for playlist
                        if not playlist_url:
                            playlist_url = await frame.evaluate("""() => {
                                if (window && window.playlist) return window.playlist;
                                return null;
                            }""")

                # Direct href fallback (sometimes iframe not used)
                if not playlist_url and href.endswith(".space") or "playlist" in href:
                    playlist_url = href

                if playlist_url:
                    streams.append((title, playlist_url))
                    print(f"🎯 Found stream: {title}")
                await event_page.close()

            except Exception as e:
                print(f"  ⚠️ Error processing event {title}: {e}")

        await browser.close()
    return streams

# Write VLC + TiviMate playlists
def write_playlists(streams):
    if not streams:
        print("⚠️ No streams found.")
        Path(VLC_OUTPUT).write_text(m3u_header(), encoding="utf-8")
        Path(TIVIMATE_OUTPUT).write_text(m3u_header(), encoding="utf-8")
        return

    ua_enc = quote(USER_AGENT, safe="")

    # VLC
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u_header())
        for title, url in streams:
            f.write(f'#EXTINF:-1 tvg-logo="{TV_INFO[1]}" tvg-id="{TV_INFO[0]}" group-title="{TV_INFO[2]}",{title}\n')
            f.write(f"{url}\n\n")

    # TiviMate (pipe headers)
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(m3u_header())
        for title, url in streams:
            f.write(f'#EXTINF:-1 tvg-logo="{TV_INFO[1]}" tvg-id="{TV_INFO[0]}" group-title="{TV_INFO[2]}",{title}\n')
            f.write(f"{url}|referer={REFERER}|user-agent={ua_enc}\n\n")

    print(f"✅ Playlists written:\n - {VLC_OUTPUT}\n - {TIVIMATE_OUTPUT}")

# Main
async def main():
    print("▶️ Starting BuffStreams playlist generation...")
    streams = await extract_streams()
    write_playlists(streams)
    print("✅ Finished.")

if __name__ == "__main__":
    asyncio.run(main())
