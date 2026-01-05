#!/usr/bin/env python3
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

EVENT_URLS = [
    "https://nflwebcast.com/pittsburgh-steelers-live-stream-online-free/",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

HAR_FILE = "nflwebcast.har"
OUT_VLC = "NFLWebcast_VLC.m3u8"
OUT_TIVI = "NFLWebcast_TiviMate.m3u8"


def extract_from_har(har_path: Path):
    """Extract m3u8 URLs from HAR"""
    m3u8s = set()

    data = json.loads(har_path.read_text(encoding="utf-8"))
    for entry in data.get("log", {}).get("entries", []):
        url = entry.get("request", {}).get("url", "")
        if ".m3u8" in url:
            m3u8s.add(url)

    return list(m3u8s)


async def extract_iframe_sources(page):
    """Extract iframe provider URLs"""
    providers = set()

    for frame in page.frames:
        if frame.url and "nflwebcast.com" not in frame.url:
            providers.add(frame.url)

        try:
            html = await frame.content()
            for m in re.findall(r'<iframe[^>]+src=["\']([^"\']+)', html):
                providers.add(urljoin(page.url, m))
        except Exception:
            pass

    return list(providers)


async def visit_event(playwright, url):
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=USER_AGENT,
        record_har_path=HAR_FILE,
        record_har_content="embed",
    )

    page = await context.new_page()
    print(f"🔍 Visiting event: {url}")

    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(15000)

    iframe_providers = await extract_iframe_sources(page)

    await context.close()
    await browser.close()

    return iframe_providers


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
    print("🚀 Starting NFL Webcast scraper (HAR + iframe mode)")

    all_streams = []

    async with async_playwright() as p:
        for url in EVENT_URLS:
            providers = await visit_event(p, url)

            print(f"🔗 iframe providers:")
            for purl in providers:
                print(f"  → {purl}")

            if Path(HAR_FILE).exists():
                m3u8s = extract_from_har(Path(HAR_FILE))
                for m in m3u8s:
                    print(f"✅ HAR stream found: {m}")
                    title = url.split("/")[-2].replace("-", " ").title()
                    all_streams.append((title, m))

    if not all_streams:
        print("❌ No streams captured")
        return

    write_playlists(all_streams)
    print("🎉 Playlists generated")


if __name__ == "__main__":
    asyncio.run(main())
