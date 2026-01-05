import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

EVENT_URLS = [
    "https://nflwebcast.com/pittsburgh-steelers-live-stream-online-free/",
]

OUT_VLC = "NFLWebcast_VLC.m3u8"
OUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


async def extract_m3u8(page, url):
    found = set()

    def on_response(resp):
        if ".m3u8" in resp.url:
            found.add(resp.url)

    page.on("response", on_response)

    print(f"🔍 Visiting event: {url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)

    # --- CLICK EVERYTHING REASONABLE ---
    selectors = [
        "button",
        "a",
        "div",
        "body",
        "video",
        "iframe",
    ]

    for sel in selectors:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=1500, force=True)
                await page.wait_for_timeout(1000)
        except Exception:
            pass

    # --- CHECK IFRAMES ---
    for frame in page.frames:
        try:
            content = await frame.content()
            for m in re.findall(
                r"https?://[^\s\"'>]+\.m3u8[^\s\"'>]*", content
            ):
                found.add(m)
        except Exception:
            pass

    # --- FINAL WAIT (player delay) ---
    await page.wait_for_timeout(15000)

    return list(found)


def write_playlists(streams):
    vlc = ["#EXTM3U"]
    tivi = ["#EXTM3U"]

    for title, url in streams:
        vlc.append(f"#EXTINF:-1,{title}")
        vlc.append(url)

        ua = USER_AGENT.replace(" ", "%20")
        tivi.append(f"#EXTINF:-1,{title}")
        tivi.append(f"{url}|user-agent={ua}")

    Path(OUT_VLC).write_text("\n".join(vlc), encoding="utf-8")
    Path(OUT_TIVI).write_text("\n".join(tivi), encoding="utf-8")


async def main():
    print("🚀 Starting NFL Webcast scraper (PLAYER-AWARE FIX)")

    streams = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        for url in EVENT_URLS:
            m3u8s = await extract_m3u8(page, url)

            if not m3u8s:
                print(f"⚠️ No m3u8 found: {url}")
                continue

            title = url.split("/")[-2].replace("-", " ").title()

            for m in m3u8s:
                streams.append((title, m))
                print(f"✅ Found stream: {m}")

        await browser.close()

    if not streams:
        print("❌ No streams captured")
        return

    write_playlists(streams)
    print("🎉 Playlists generated")


if __name__ == "__main__":
    asyncio.run(main())
