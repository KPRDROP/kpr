import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

START_URL = "https://nflwebcast.com/sbl/"   # direct path to bypass redirect
OUTPUT_FILE = "NFLWebcast.m3u8.m3u8"

MAX_RETRIES = 4
NAV_TIMEOUT = 15000  # 15 seconds per attempt


async def safe_goto(page, url):
    """A triple-fallback navigation method that NEVER hangs."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"🌐 Navigation attempt {attempt}/{MAX_RETRIES}: {url}")

            # Try standard navigation first
            await page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            return True

        except PlaywrightTimeout:
            print(f"⚠️ Timeout on attempt {attempt}. Retrying...")

            # Try again with more lenient wait
            try:
                await page.goto(url, timeout=NAV_TIMEOUT, wait_until="networkidle")
                return True
            except:
                pass

            # Final fallback: load without waiting
            try:
                await page.goto(url, timeout=NAV_TIMEOUT, wait_until="commit")
                return True
            except:
                pass

            await asyncio.sleep(2)

    print("❌ Navigation FAILED after retries")
    return False


async def extract_links(page):
    """Return all M3U URL candidates."""
    links = await page.eval_on_selector_all(
        "a",
        "els => els.map(e => e.href).filter(h => h && h.includes('m3u'))"
    )
    return list(set(links))


async def deep_scan(page):
    """Scan all sub-pages for stream links."""
    found = set()
    anchors = await page.query_selector_all("a")

    for a in anchors:
        href = await a.get_attribute("href")
        if not href:
            continue

        if href.startswith("/"):
            href = "https://nflwebcast.com" + href

        if not href.startswith("http"):
            continue

        # Visit each link with safety
        print(f"🔎 Deep-scan visiting: {href}")
        new_tab = await page.context.new_page()
        ok = await safe_goto(new_tab, href)

        if ok:
            sub_links = await extract_links(new_tab)
            for s in sub_links:
                found.add(s)

        await new_tab.close()

    return list(found)


async def run_scraper():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # navigate with protection
        success = await safe_goto(page, START_URL)
        if not success:
            raise Exception("Unable to reach NFLWebcast main page")

        # extract inline links
        print("🔍 Extracting direct links...")
        links = set(await extract_links(page))

        # deep scan
        print("🔎 Running deep scan...")
        deep_links = await deep_scan(page)
        for l in deep_links:
            links.add(l)

        print(f"✅ Total links found: {len(links)}")

        # write file
        if links:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for link in links:
                    f.write(f"#EXTINF:-1,\n{link}\n")

            print(f"💾 Playlist saved: {OUTPUT_FILE}")
        else:
            print("⚠️ No links found — playlist NOT generated")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_scraper())
