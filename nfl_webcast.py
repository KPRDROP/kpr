import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

# ================= CONFIG =================

EVENT_URLS = [
    "https://nflwebcast.com/pittsburgh-steelers-live-stream-online-free/",
]

OUT_VLC = "NFLWebcast_VLC.m3u8"
OUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)

# ==========================================


async def extract_m3u8_from_event(page, url):
    print(f"🔍 Visiting event: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)

    m3u8_urls = set()

    def on_response(resp):
        if ".m3u8" in resp.url:
            m3u8_urls.add(resp.url)

    page.on("response", on_response)

    # allow player scripts to run
    await page.wait_for_timeout(15000)

    return list(m3u8_urls)


def write_playlists(streams):
    vlc = ["#EXTM3U"]
    tivi = ["#EXTM3U"]

    for title, url in streams:
        vlc.append(f'#EXTINF:-1,{title}')
        vlc.append(url)

        ua = USER_AGENT.replace(" ", "%20")
        tivi.append(f'#EXTINF:-1,{title}')
        tivi.append(f'{url}|user-agent={ua}')

    Path(OUT_VLC).write_text("\n".join(vlc), encoding="utf-8")
    Path(OUT_TIVI).write_text("\n".join(tivi), encoding="utf-8")


async def main():
    print("🚀 Starting NFL Webcast scraper (DIRECT EVENT MODE)")

    streams = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        for event_url in EVENT_URLS:
            m3u8s = await extract_m3u8_from_event(page, event_url)

            if not m3u8s:
                print(f"⚠️ No m3u8 found: {event_url}")
                continue

            title = re.sub(
                r"-live-stream-online-free/?",
                "",
                event_url.split("/")[-2].replace("-", " ").title()
            )

            for m3u8 in m3u8s:
                streams.append((title, m3u8))
                print(f"✅ Found stream: {title}")

        await browser.close()

    if not streams:
        print("❌ No streams captured")
        return

    write_playlists(streams)
    print(f"🎉 Exported {len(streams)} stream(s)")


if __name__ == "__main__":
    asyncio.run(main())
