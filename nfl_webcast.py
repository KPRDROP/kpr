import asyncio
from playwright.async_api import async_playwright
from urllib.parse import urlparse

BASE_URL = "https://nflwebcast.com/"
OUTPUT_FILE = "NFLWebcast_VLC.m3u8"

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
        await page.goto(BASE_URL, wait_until="load", timeout=60000)

        # IMPORTANT: let Cloudflare + JS finish
        await page.wait_for_selector("body", timeout=30000)
        await page.wait_for_timeout(5000)

        # Trigger lazy-loaded content
        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(4000)

        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll("a[href]"))
                .map(a => a.href)
        """)

        print(f"🔍 Found {len(links)} total links")

        for href in links:
            try:
                parsed = urlparse(href)
            except Exception:
                continue

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
            name = url.rstrip("/").split("/")[-1].replace("-", " ").title()
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
