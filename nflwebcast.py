#!/usr/bin/env python3
import asyncio
import sys
import re
import time
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright
import aiohttp

START_URL = "https://nflwebcast.com/"
LISTING_URL = "https://nflwebcast.com/sbl/"
OUTPUT_FILE = "NFLWebcast.m3u8"

MAX_NAV_RETRIES = 4
NAV_TIMEOUT_MS = 30000
DEEP_SCAN = True
REQUEST_TIMEOUT = 8
VALIDATION_TIMEOUT = 10


# ----------------------------------------------------------
# Utility: Clean URL
# ----------------------------------------------------------
def clean_url(url: str) -> str:
    if not url:
        return ""
    return url.strip().split("#")[0].strip()


# ----------------------------------------------------------
# Utility: detect if page is Cloudflare challenge
# ----------------------------------------------------------
async def looks_like_challenge(page):
    html = await page.content()
    if "cf-browser-verification" in html.lower():
        return True
    if "Just a moment" in html:
        return True
    if "cf-challenge" in html:
        return True
    if "cloudflare" in html.lower() and "challenge" in html.lower():
        return True
    return False


# ----------------------------------------------------------
# Chrome Stealth: patch JS environment
# ----------------------------------------------------------
STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', {
  get: () => undefined
});

// Fake plugins
Object.defineProperty(navigator, 'plugins', {
  get: () => [1,2,3]
});

// Fake languages
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en']
});

// Fake permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters)
);

// Navigator hardware spoof
Object.defineProperty(navigator, 'hardwareConcurrency', {
  get: () => 8
});
"""


# ----------------------------------------------------------
# Navigation with retries
# ----------------------------------------------------------
async def safe_goto(page, url: str) -> bool:
    for attempt in range(1, MAX_NAV_RETRIES + 1):
        print(f"→ goto {url} attempt {attempt}/{MAX_NAV_RETRIES}")
        try:
            resp = await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)

            if await looks_like_challenge(page):
                print("   ⏳ Cloudflare challenge, waiting…")
                await asyncio.sleep(5 + attempt)
                continue

            if resp and resp.ok:
                return True

        except Exception as e:
            print(f"   ⚠ navigation error: {e}")
            await asyncio.sleep(2)

    print(f"   ✖ failed navigation: {url}")
    return False


# ----------------------------------------------------------
# Extract all <a href> links
# ----------------------------------------------------------
async def extract_links(page, base_url: str):
    anchors = await page.eval_on_selector_all(
        "a[href]", "els => els.map(e => e.getAttribute('href'))"
    )

    links = []
    if not anchors:
        return []

    for a in anchors:
        if not a:
            continue
        if a.startswith("javascript:"):
            continue

        url = urljoin(base_url, a)
        if START_URL not in url:
            continue

        links.append(clean_url(url))

    return list(sorted(set(links)))


# ----------------------------------------------------------
# Extract M3U8 candidates from HTML sources
# ----------------------------------------------------------
async def extract_m3u8_from_html(page):
    html = await page.content()
    urls = re.findall(r"https?://[^\"']+?\.m3u8[^\"']*", html, flags=re.IGNORECASE)
    return [clean_url(u) for u in urls]


# ----------------------------------------------------------
# aiohttp validation of .m3u8 (with TiviMate-compatible headers)
# ----------------------------------------------------------
async def validate_m3u8(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
        "Referer": START_URL,
        "Origin": START_URL,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=VALIDATION_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return True
    except:
        return False
    return False


# ----------------------------------------------------------
# Scrape individual event page for streams
# ----------------------------------------------------------
async def scan_event_page(context, url):
    page = await context.new_page()
    page.add_init_script(STEALTH_JS)

    print(f"   → scanning event: {url}")
    if not await safe_goto(page, url):
        await page.close()
        return []

    # Find m3u8
    m3u8s = await extract_m3u8_from_html(page)
    await page.close()
    return m3u8s


# ----------------------------------------------------------
# Main
# ----------------------------------------------------------
async def main():
    print("🚀 Starting NFLWebcast scraper (Chrome Stealth Mode)")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome Safari"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        page.add_init_script(STEALTH_JS)

        print("🌐 Loading homepage…")
        ok = await safe_goto(page, START_URL)

        print("🌐 Loading listing /sbl/…")
        ok2 = await safe_goto(page, LISTING_URL)

        # Collect candidate event <a> hrefs
        candidate_links = []
        if ok2:
            candidate_links.extend(await extract_links(page, LISTING_URL))

        print(f"🔍 Event links discovered: {len(candidate_links)}")

        # Optionally deep scan homepage <a>
        if DEEP_SCAN:
            print("🔎 Deep scan of homepage <a>…")
            if ok:
                hl = await extract_links(page, START_URL)
                for x in hl:
                    if x not in candidate_links:
                        candidate_links.append(x)

        await page.close()

        # Scan found pages for M3U8
        results = []
        for ev in candidate_links:
            m3u8s = await scan_event_page(context, ev)
            for u in m3u8s:
                results.append(u)

        print(f"🎯 Found potential streams: {len(results)}")

        # Validate streams
        validated = []
        print("🔎 Validating m3u8 URLs…")
        for url in results:
            okv = await validate_m3u8(url)
            if okv:
                validated.append(url)

        print(f"✅ Valid streams: {len(validated)}")

        # Write playlist
        print(f"📄 Writing playlist: {OUTPUT_FILE}")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for idx, u in enumerate(validated, 1):
                f.write(
                    f'#EXTINF:-1 group-title="NFLWebcast" tvg-name="Stream {idx}", Stream {idx}\n'
                )
                f.write(
                    f"{u}|referer={START_URL}|origin={START_URL}|user-agent=Chrome\n"
                )

        print(
            f"🎉 Playlist written: {OUTPUT_FILE} | streams: {len(validated)}"
        )

        await context.close()
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
