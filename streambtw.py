import asyncio
import re
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

HOMEPAGE = "https://streambtw.com/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

M3U8_REGEX = re.compile(r"https?://[^\s\"']+\.m3u8[^\s\"']*")

async def extract_m3u8(playwright, iframe_url):
    """Loads the iframe in headless Chromium and captures m3u8 URLs."""
    try:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(iframe_url, timeout=30000)

        m3u8_urls = set()

        async def capture_request(route):
            url = route.request.url
            if ".m3u8" in url:
                m3u8_urls.add(url)
            await route.continue_()

        await page.route("**/*", capture_request)

        await page.wait_for_timeout(8000)

        await browser.close()
        return list(m3u8_urls)

    except Exception as e:
        print(f"⚠️ Error scraping iframe {iframe_url}: {e}")
        return []


async def main():
    print("🔍 Fetching StreamBTW homepage...")

    r = requests.get(HOMEPAGE, headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")

    iframe_links = []

    for a in soup.select("a"):
        href = a.get("href")
        if href and "iframe/" in href:
            full = urljoin(HOMEPAGE, href)
            iframe_links.append(full)

    print(f"📌 Found {len(iframe_links)} iframe pages")

    results = {}

    async with async_playwright() as p:
        for idx, link in enumerate(iframe_links, start=1):
            print(f"🔎 [{idx}/{len(iframe_links)}] Checking iframe: {link}")

            streams = await extract_m3u8(p, link)

            if streams:
                print(f"✅ Found stream: {streams[0]}")
                results[link] = streams[0]
            else:
                print(f"⚠️ No m3u8 found for {link}")

    if not results:
        print("❌ No streams captured from any iframe pages.")
        return

    print("📺 Generating M3U playlists...")

    with open("Streambtw_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, stream_url in results.items():
            f.write(f'#EXTINF:-1,{title}\n{stream_url}\n')

    with open("Streambtw_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, stream_url in results.items():
            f.write(f'#EXTINF:-1 tvg-name="{title}" group-title="StreamBTW",StreamBTW\n{stream_url}\n')

    print("🎉 DONE — Playlists generated:")
    print("➡ Streambtw_VLC.m3u8")
    print("➡ Streambtw_TiviMate.m3u8")


if __name__ == "__main__":
    asyncio.run(main())
