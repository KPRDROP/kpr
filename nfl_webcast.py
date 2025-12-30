import asyncio
from playwright.async_api import async_playwright
from urllib.parse import urlparse

BASE_URL = "https://nflwebcast.com/"
OUTPUT_FILE = "NFLWebcast_Subpages.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

# -------------------------------------------------

async def scrape_nfl_subpages():
    subpages = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        print("🌐 Loading NFLWebcast homepage...")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)

        links = await page.query_selector_all("a[href]")
        print(f"🔍 Found {len(links)} total links")

        for a in links:
            href = await a.get_attribute("href")
            if not href:
                continue

            if href.startswith("/"):
                href = BASE_URL.rstrip("/") + href

            parsed = urlparse(href)

            if (
                parsed.scheme in ("http", "https")
                and parsed.netloc == "nflwebcast.com"
                and "live-stream" in parsed.path
            ):
                subpages.add(href)

        await browser.close()

    return sorted(subpages)

# -------------------------------------------------

def write_playlist(urls):
    if not urls:
        print("❌ No subpages found.")
        return

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for url in urls:
            name = url.split("/")[-2].replace("-", " ").title()
            f.write(f"#EXTINF:-1,{name}\n")
            f.write(url + "\n")

    print(f"✅ Saved {len(urls)} NFL subpages to {OUTPUT_FILE}")

# -------------------------------------------------

async def main():
    print("🚀 Starting NFLWebcast subpage scraper...")
    urls = await scrape_nfl_subpages()
    write_playlist(urls)
    print("🎉 Done.")

if __name__ == "__main__":
    asyncio.run(main())
