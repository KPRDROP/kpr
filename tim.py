import asyncio
import json
import logging
from urllib.parse import quote

import requests
from playwright.async_api import async_playwright

API_URL = "https://stra.viaplus.site/main"

OUTPUT_VLC = "tim_vlc.m3u8"
OUTPUT_TIVIMATE = "tim_tivimate.m3u8"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d | %H:%M:%S"
)

log = logging.getLogger(__name__)


# ---------------------------------------------------
# Fetch API events
# ---------------------------------------------------
def fetch_events():
    log.info("Fetching TIM API")

    r = requests.get(API_URL, timeout=30)
    r.raise_for_status()

    data = r.json()

    events = []
    for item in data:
        if item.get("category") == "Events":
            events = item.get("events", [])

    return events


# ---------------------------------------------------
# Capture m3u8 from embed
# ---------------------------------------------------
async def capture_stream(embed_url):
    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            user_agent=USER_AGENT
        )

        page = await context.new_page()

        stream_url = None

        def handle_request(request):
            nonlocal stream_url

            url = request.url

            if ".m3u8" in url:
                stream_url = url

        page.on("request", handle_request)

        try:
            await page.goto(embed_url, timeout=60000)

            # wait player to start
            await page.wait_for_timeout(10000)

        except Exception as e:
            log.warning(f"Embed error: {e}")

        await browser.close()

        return stream_url


# ---------------------------------------------------
# Write playlists
# ---------------------------------------------------
def write_playlists(entries):

    with open(OUTPUT_VLC, "w", encoding="utf-8") as f1, \
         open(OUTPUT_TIVIMATE, "w", encoding="utf-8") as f2:

        f1.write("#EXTM3U\n")
        f2.write("#EXTM3U\n")

        for name, logo, url in entries:

            f1.write(f'#EXTINF:-1 tvg-logo="{logo}",{name}\n')
            f1.write(f"{url}\n")

            encoded = quote(USER_AGENT)

            f2.write(f'#EXTINF:-1 tvg-logo="{logo}",{name}\n')
            f2.write(f"{url}|user-agent={encoded}\n")


# ---------------------------------------------------
# Main
# ---------------------------------------------------
async def main():

    log.info("Starting TIM Streams updater")

    events = fetch_events()

    log.info(f"Processing {len(events)} events")

    playlist_entries = []

    for i, ev in enumerate(events, 1):

        name = ev.get("name")
        logo = ev.get("logo")

        streams = ev.get("streams", [])

        if not streams:
            continue

        embed = streams[0]["url"]

        log.info(f"URL {i}) Opening embed")

        m3u8 = await capture_stream(embed)

        if not m3u8:
            log.warning(f"URL {i}) Stream not found")
            continue

        log.info(f"URL {i}) Stream captured")

        playlist_entries.append((name, logo, m3u8))

    write_playlists(playlist_entries)

    log.info("Playlists written successfully")


# ---------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
