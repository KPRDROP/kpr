#!/usr/bin/env python3
import asyncio
import aiohttp
import sys
import time
from urllib.parse import urljoin, quote
from playwright.async_api import async_playwright

START_URL = "https://nflwebcast.com/"
OUTPUT_FILE = "NFLWebcast.m3u8"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

NAV_TIMEOUT_MS = 25_000
NAV_RETRIES = 3
GLOBAL_TIMEOUT = 180
VALIDATE_TIMEOUT = 10

PLAYABLE_MARK = (".m3u8", ".ts")

def log(*args):
    print(*args, flush=True)

def is_playable(url: str):
    return url and any(p in url.lower() for p in PLAYABLE_MARK)

async def validate_url(url, session):
    headers = {"User-Agent": USER_AGENT, "Referer": START_URL, "Origin": START_URL}
    try:
        async with session.get(url, headers=headers, timeout=VALIDATE_TIMEOUT) as r:
            return r.status == 200
    except:
        return False

async def robust_goto(page, url, attempts=NAV_RETRIES):
    for i in range(1, attempts + 1):
        try:
            log(f"→ goto {url} attempt {i}/{attempts}")
            await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            await page.wait_for_selector("body", timeout=5000)
            content = (await page.content()).lower()
            if any(x in content for x in ("cf-browser-verification","checking your browser","just a moment")):
                log("⏳ Cloudflare/challenge detected, retrying...")
                await asyncio.sleep(2 + i)
                continue
            return True
        except:
            await asyncio.sleep(1)
    return False

async def extract_event_links(page):
    anchors = await page.query_selector_all("a[href]")
    links = set()
    for a in anchors:
        try:
            href = await a.get_attribute("href")
            if href and any(x in href.lower() for x in ("/live-stream","live-stream-online")):
                links.add(urljoin(START_URL, href))
        except:
            continue
    return list(links)

async def main():
    start = time.time()
    log("🚀 Starting NFLWebcast scraper (headful + direct <a> scan)")
    found_urls = set()
    try:
        async with async_playwright() as pw, aiohttp.ClientSession() as session:
            browser = await pw.chromium.launch(headless=False, args=["--no-sandbox"])
            context = await browser.new_context(user_agent=USER_AGENT, viewport={"width":1280,"height":720})
            page = await context.new_page()

            # Try main page first
            ok = await robust_goto(page, START_URL)
            if not ok:
                # try /sbl/ listing
                sbl = urljoin(START_URL, "sbl/")
                ok = await robust_goto(page, sbl)
            if ok:
                links = await extract_event_links(page)
                found_urls.update(links)
            await page.close()
            await context.close()
            await browser.close()

            log(f"ℹ candidate links found: {len(found_urls)}")
            validated = set()
            for u in found_urls:
                if is_playable(u):
                    if await validate_url(u, session):
                        validated.add(u)

            # write playlist
            with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n")
                if not validated:
                    fh.write("# no streams validated\n")
                for u in validated:
                    ua_enc = quote(USER_AGENT, safe="")
                    fh.write(f"#EXTINF:-1,Live\n{u}|referer={START_URL}|origin={START_URL}|user-agent={ua_enc}\n")
            log(f"✅ Playlist written: {OUTPUT_FILE} | streams: {len(validated)} | time: {time.time()-start:.1f}s")

    except Exception as e:
        log("❌ Fatal error:", e)
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=GLOBAL_TIMEOUT))
    except asyncio.TimeoutError:
        log("❌ Global timeout reached")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n# timeout\n")
        sys.exit(2)
