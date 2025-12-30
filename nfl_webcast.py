#!/usr/bin/env python3
import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus
from playwright.async_api import async_playwright

BASE = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

HEADERS = {
    "referer": BASE,
    "origin": BASE,
}

LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"


def log(*args):
    print(*args)
    sys.stdout.flush()


def clean_title(title: str) -> str:
    if not title:
        return "NFL Game"
    title = title.replace("@", "vs")
    title = re.sub(r"\s+", " ", title)
    return title.strip()


async def capture_m3u8(page):
    found = None

    def on_response(resp):
        nonlocal found
        url = resp.url
        if ".m3u8" in url and not found:
            found = url

    page.on("response", on_response)

    # Try clicking common play elements
    for sel in ["button", ".play", ".player", "video", "iframe"]:
        try:
            loc = page.locator(sel)
            if await loc.count() > 0:
                await loc.first.click(force=True, timeout=1000)
        except Exception:
            pass

    await asyncio.sleep(6)
    return found


async def scrape_nfl():
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        log("🌐 Loading NFLWebcast homepage...")
        await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)

        # Collect candidate event links
        links = set()
        for a in await page.locator("a").all():
            href = await a.get_attribute("href")
            if href and href.startswith("/"):
                full = urljoin(BASE, href)
                if "nfl" in full.lower():
                    links.add(full)

        log(f"🔍 Found {len(links)} candidate event pages")

        for idx, url in enumerate(sorted(links), 1):
            log(f"➡️ [{idx}/{len(links)}] Visiting {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                title = clean_title(await page.title())
                m3u8 = await capture_m3u8(page)

                if m3u8:
                    log(f"✅ Found stream: {m3u8}")
                    results.append((title, m3u8))
                else:
                    log("⚠️ No stream found")

            except Exception as e:
                log(f"❌ Error: {e}")

        await browser.close()

    return results


def write_playlists(entries):
    if not entries:
        log("❌ No streams captured.")
        return

    # VLC
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="NFL.Dummy.us" '
                f'tvg-logo="{LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={HEADERS['referer']}\n")
            f.write(f"#EXTVLCOPT:http-origin={HEADERS['origin']}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(url + "\n")

    # TiviMate
    ua = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{url}|referer={BASE}|origin={BASE}|user-agent={ua}\n")

    log(f"✅ Playlists written: {OUTPUT_VLC}, {OUTPUT_TIVI}")


async def main():
    log("🚀 Starting NFL Webcast scraper (fixed)...")
    streams = await scrape_nfl()
    write_playlists(streams)
    log("🎉 Done.")


if __name__ == "__main__":
    asyncio.run(main())
