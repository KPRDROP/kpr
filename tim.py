import os
import json
import asyncio
import logging
import urllib.request
from urllib.parse import urljoin, quote

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues, network

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d | %H:%M:%S",
)

log = logging.getLogger(__name__)

TAG = "TIMSTRMS"

API_URL = os.environ.get("TIM_API_URL")
BASE_URL = os.environ.get("TIM_BASE_URL")

if not API_URL:
    raise RuntimeError("Missing TIM_API_URL secret")

if not BASE_URL:
    raise RuntimeError("Missing TIM_BASE_URL secret")


SPORT_GENRES = {
    1: "Soccer",
    2: "Motorsport",
    3: "MMA",
    4: "Fight",
    5: "Boxing",
    6: "Wrestling",
    7: "Basketball",
    9: "Baseball",
    10: "Tennis",
    11: "Hockey",
}


OUTPUT_VLC = "tim_vlc.m3u8"
OUTPUT_TIVIMATE = "tim_tivimate.m3u8"


def fetch_api():
    log.info("Fetching TIM API")

    with urllib.request.urlopen(API_URL) as r:
        data = json.loads(r.read().decode())

    return data


def parse_events(api_data):

    events = []

    for info in api_data:

        if info.get("category") != "Events":
            continue

        for ev in info.get("events", []):

            genre = ev.get("genre")

            if genre not in SPORT_GENRES:
                continue

            streams = ev.get("streams")

            if not streams:
                continue

            embed = streams[0].get("url")

            if not embed:
                continue

            events.append(
                {
                    "sport": SPORT_GENRES[genre],
                    "event": ev.get("name"),
                    "logo": ev.get("logo"),
                    "embed": embed,
                    "page": urljoin(BASE_URL, f"watch?id={ev.get('URL')}"),
                }
            )

    return events


async def capture_stream(browser, embed_url, num):

    context = await browser.new_context()
    page = await context.new_page()

    stream_url = None

    def handle_response(resp):
        nonlocal stream_url
        url = resp.url

        if ".m3u8" in url and not stream_url:
            stream_url = url
            log.info(f"URL {num}) Stream captured")

    page.on("response", handle_response)

    try:

        await page.goto(embed_url, timeout=60000)

        # autoplay delay
        await page.wait_for_timeout(15000)

    except Exception as e:
        log.warning(f"URL {num}) error: {e}")

    await context.close()

    if not stream_url:
        log.warning(f"URL {num}) Stream not found")

    return stream_url


def write_playlists(entries):

    with open(OUTPUT_VLC, "w", encoding="utf8") as vlc, open(
        OUTPUT_TIVIMATE, "w", encoding="utf8"
    ) as tivimate:

        vlc.write("#EXTM3U\n")
        tivimate.write("#EXTM3U\n")

        for e in entries:

            if not e["stream"]:
                continue

            name = f"[{e['sport']}] {e['event']} ({TAG})"

            logo = e["logo"] or ""

            vlc.write(
                f'#EXTINF:-1 tvg-logo="{logo}",{name}\n{e["stream"]}\n'
            )

            ua = quote(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
            )

            tivimate.write(
                f'#EXTINF:-1 tvg-logo="{logo}",{name}\n{e["stream"]}|user-agent={ua}\n'
            )

    log.info("Playlists written successfully")


async def main():

    log.info("Starting TIM Streams updater")

    api_data = fetch_api()

    events = parse_events(api_data)

    log.info(f"Processing {len(events)} events")

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=True)

        for i, ev in enumerate(events, start=1):

            stream = await capture_stream(browser, ev["embed"], i)

            ev["stream"] = stream

            results.append(ev)

        await browser.close()

    write_playlists(results)


if __name__ == "__main__":
    asyncio.run(main())
