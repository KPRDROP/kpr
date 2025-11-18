import asyncio
from playwright.async_api import async_playwright, TimeoutError

START_URL = "https://nflwebcast.com/sbl/"
OUTPUT_FILE = "NFLWebcast.m3u8.m3u8"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

MAX_NAV_RETRIES = 5
NAV_TIMEOUT = 20000


async def wait_real_page(page):
    """Detect when page is real content and NOT Cloudflare."""
    for _ in range(50):
        html = await page.content()

        if "cf-browser-verification" in html.lower():
            print("⏳ Cloudflare challenge detected... waiting...")
            await asyncio.sleep(2)
            continue

        if "Just a moment" in html:
            print("⏳ Cloudflare says 'Just a moment'...")
            await asyncio.sleep(2)
            continue

        # Not a CF page → content loaded
        return True

    return False


async def robust_goto(page, url):
    """A robust navigation method that survives CF challenge + retries."""
    for attempt in range(1, MAX_NAV_RETRIES + 1):
        print(f"🌐 Navigation attempt {attempt}/{MAX_NAV_RETRIES}: {url}")
        try:
            await page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            ok = await wait_real_page(page)
            if ok:
                return True
        except TimeoutError:
            print("⚠️ Navigation timeout, retrying...")

        await asyncio.sleep(2)

    print("❌ Could not load the page after retries.")
    return False


async def run_scraper():
    print("🚀 Starting NFLWebcast Cloudflare-Bypass Scraper...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins",
                "--disable-site-isolation-trials",
            ]
        )

        context = await browser.new_context(
            user_agent=UA,
            extra_http_headers={
                "Referer": "https://nflwebcast.com/",
                "Origin": "https://nflwebcast.com",
            }
        )

        page = await context.new_page()

        # Capture streams BEFORE loading page
        streams = set()

        def on_request(req):
            url = req.url.lower()
            if ".m3u8" in url:
                print(f"🎯 STREAM FOUND: {url}")
                streams.add(url)

        context.on("request", on_request)

        # Navigate
        ok = await robust_goto(page, START_URL)
        if not ok:
            print("❌ Unable to pass Cloudflare")
            await browser.close()
            return

        print("🔎 Waiting for all scripts requests (10 seconds)...")
        await asyncio.sleep(10)

        # Save result
        if streams:
            print(f"✅ Found {len(streams)} stream(s). Writing playlist...")
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for m3u in streams:
                    f.write("#EXTINF:-1,\n")
                    f.write(m3u + "\n")
        else:
            print("⚠️ No streams found.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_scraper())
