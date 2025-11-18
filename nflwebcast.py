import asyncio
import re
import time
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://nflwebcast.com/"
HEADERS = {
    "Referer": BASE_URL,
    "Origin": BASE_URL.rstrip("/")
}

# -------- Retry wrapper -------- #
async def safe_goto(page, url, retries=5, wait=3000):
    for attempt in range(1, retries + 1):
        try:
            print(f"➡️ Navigating to {url} (attempt {attempt})")
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(wait)
            return True
        except Exception as e:
            print(f"⚠️ Navigation failed: {e}")
            if attempt < retries:
                await page.wait_for_timeout(2000)
            else:
                print("❌ Gave up navigating.")
                return False


# -------- Extract m3u8 anywhere in HTML -------- #
def extract_m3u8(html):
    found = re.findall(r'https?://[^\s"\'<>]+\.m3u8', html)
    return list(set(found))


# -------- Main scraping -------- #
async def scrape_nfl():
    print("🚀 Starting NFL Webcast Scraper...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(extra_http_headers=HEADERS)
        page = await context.new_page()

        # 1. Load homepage
        ok = await safe_goto(page, BASE_URL)
        if not ok:
            return []

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # 2. Find event links
        links = []
        for a in soup.select("a"):
            href = a.get("href", "")
            if "/live-stream" in href or "online-free" in href:
                if href.startswith("/"):
                    href = BASE_URL.rstrip("/") + href
                if href.startswith(BASE_URL):
                    links.append(href)

        links = list(set(links))
        print(f"📌 Found {len(links)} event links")

        all_streams = []

        # 3. Visit each event page
        for url in links:
            print(f"\n📺 Checking event page: {url}")
            ok = await safe_goto(page, url)
            if not ok:
                continue

            event_html = await page.content()

            # Find any <iframe> (streams usually inside)
            soup2 = BeautifulSoup(event_html, "html.parser")
            iframes = soup2.find_all("iframe")

            for iframe in iframes:
                src = iframe.get("src", "")
                if not src:
                    continue

                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = BASE_URL.rstrip("/") + src

                print(f"   ➡️ iframe: {src}")

                # Visit iframe URL
                ok = await safe_goto(page, src)
                if not ok:
                    continue

                frame_html = await page.content()
                m3u8s = extract_m3u8(frame_html)

                if m3u8s:
                    for m in m3u8s:
                        print(f"   🎯 FOUND STREAM: {m}")
                        all_streams.append(m)

        await browser.close()
        return list(set(all_streams))


# -------- Write playlist -------- #
def write_playlist(streams):
    filename = "NFLWebcast.m3u8.m3u8"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for idx, stream in enumerate(streams, 1):
            f.write(f"#EXTINF:-1,NFL Stream {idx}\n")
            f.write(stream + "\n")
    print(f"✅ Playlist saved: {filename}")


# -------- Main -------- #
async def main():
    streams = await scrape_nfl()
    if streams:
        print(f"🔥 Total streams found: {len(streams)}")
        write_playlist(streams)
    else:
        print("❌ No streams found.")


if __name__ == "__main__":
    asyncio.run(main())
