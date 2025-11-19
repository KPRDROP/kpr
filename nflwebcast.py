#!/usr/bin/env python3
import asyncio
import sys
import re
from urllib.parse import urljoin
import aiohttp
from playwright.async_api import async_playwright

START_URL = "https://nflwebcast.com/"
LISTING_URL = "https://nflwebcast.com/sbl/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.6099.200 Safari/537.36")

NAV_TIMEOUT = 30000
CF_WAIT = 4
M3U_OUT = "NFLWebcast.m3u8"

EVENT_PATTERNS = [
    r"live-stream-online",
    r"live-stream",
    r"live\-stream",
]

M3U_PAT = re.compile(r"https?://[^\s\"']+\.m3u8")


# ---------------------------------------------------------------
#  Utility: real browser anti-bot patches
# ---------------------------------------------------------------
async def apply_stealth(page):
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
    """)


# ---------------------------------------------------------------
#  Navigation with Cloudflare handling
# ---------------------------------------------------------------
async def cf_safe_goto(page, url, retries=4):
    for attempt in range(1, retries + 1):
        print(f"→ goto {url} attempt {attempt}/{retries}")
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await asyncio.sleep(CF_WAIT)

            text = (await page.content()).lower()

            if "cloudflare" in text or "checking your browser" in text:
                print("   ⏳ Cloudflare challenge, waiting…")
                await asyncio.sleep(CF_WAIT + 2)
                continue

            return resp

        except Exception as e:
            print("   ⚠️ navigation error:", e)

    print("   ✖ failed navigation:", url)
    return None


# ---------------------------------------------------------------
#  Extract event page <a href=""> links
# ---------------------------------------------------------------
def extract_event_links(html: str):
    links = re.findall(r'href="([^"]+)"', html)
    out = []

    for link in links:
        full = link.strip()
        if not full.startswith("http"):
            continue

        for pat in EVENT_PATTERNS:
            if re.search(pat, full):
                out.append(full)

    return list(set(out))


# ---------------------------------------------------------------
#  Extract .m3u8 URLs from event page HTML
# ---------------------------------------------------------------
def extract_m3u8(html: str):
    return list(set(M3U_PAT.findall(html)))


# ---------------------------------------------------------------
#  Validate m3u8 via aiohttp
# ---------------------------------------------------------------
async def validate_m3u8(url):
    headers = {
        "User-Agent": UA,
        "Referer": START_URL,
        "Origin": START_URL,
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.head(url, timeout=10, allow_redirects=True) as resp:
                return resp.status == 200
    except:
        return False


# ---------------------------------------------------------------
#  Main scraping logic
# ---------------------------------------------------------------
async def main():
    print("🚀 Starting NFLWebcast scraper (full rewrite)")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()
        await apply_stealth(page)

        # 1) Try homepage
        print("🌐 Loading homepage…")
        r1 = await cf_safe_goto(page, START_URL)
        html1 = await page.content()

        # 2) Try /sbl/
        print("🌐 Loading listing /sbl/…")
        r2 = await cf_safe_goto(page, LISTING_URL)
        html2 = await page.content()

        # Extract event links
        event_links = extract_event_links(html1) + extract_event_links(html2)
        event_links = list(set(event_links))

        print(f"🔍 Event links discovered: {len(event_links)}")
        for ev in event_links:
            print("  •", ev)

        streams = []

        # Visit each event page
        for ev in event_links:
            print(f"\n🎯 Visiting event page:\n   {ev}")
            rr = await cf_safe_goto(page, ev)
            if rr is None:
                print("   ✖ could not load event")
                continue

            html = await page.content()
            found = extract_m3u8(html)

            if not found:
                print("   ⚠️ no .m3u8 found in HTML — scanning scripts…")
                # scan JS requests via network logs later if needed
                continue

            for url in found:
                print("   → candidate:", url)
                ok = await validate_m3u8(url)
                if ok:
                    print("     ✔ valid")
                    streams.append(url)
                else:
                    print("     ✖ invalid")

        # Write playlist
        print(f"\n📄 Writing playlist: {M3U_OUT}")
        with open(M3U_OUT, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for idx, s in enumerate(streams, 1):
                pipe = (
                    f"{s}|referer={START_URL}|origin={START_URL}|"
                    f"user-agent={UA}"
                )
                f.write(f"#EXTINF:-1,NFLWebcast Stream {idx}\n{pipe}\n")

        print(f"✅ Playlist written: {M3U_OUT} | streams: {len(streams)}")

        await browser.close()


# ---------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
