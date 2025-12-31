#!/usr/bin/env python3
import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

UA_FIREFOX = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)

UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

VLC_LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"

# --------------------------------------------------

def log(*a):
    print(*a)
    sys.stdout.flush()

def clean_event_title(title: str) -> str:
    if not title:
        return "NFL Game"
    t = title.replace("@", "vs").replace(",", "")
    return re.sub(r"\s{2,}", " ", t).strip()

def guess_title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for sel in [
        ("meta", {"property": "og:title"}),
        ("title", {}),
        ("h1", {}),
    ]:
        el = soup.find(*sel)
        if el:
            return clean_event_title(el.get("content") if el.name == "meta" else el.text)
    return "NFL Game"

# --------------------------------------------------

async def launch_browser(p):
    """Try Firefox first, fallback to Chromium"""
    try:
        log("🦊 Launching Firefox…")
        return await p.firefox.launch(headless=True), UA_FIREFOX
    except Exception as e:
        log(f"⚠️ Firefox failed: {e}")
        log("🌐 Falling back to Chromium…")
        return await p.chromium.launch(headless=True), UA_CHROME

# --------------------------------------------------

async def capture_m3u8(playwright, url):
    browser, ua = await launch_browser(playwright)
    context = await browser.new_context(user_agent=ua)
    page = await context.new_page()

    found = None

    page.on(
        "response",
        lambda r: (
            r.url.endswith(".m3u8") and not found and globals().update(found=r.url)
        ),
    )

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(8000)
        html = await page.content()

        if not found:
            m = re.search(r'https?://[^"\']+\.m3u8', html)
            if m:
                found = m.group(0)

    except PlaywrightTimeoutError:
        pass
    finally:
        await context.close()
        await browser.close()

    return found

# --------------------------------------------------

def write_playlists(entries):
    # VLC
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="NFL.Dummy.us" '
                f'tvg-logo="{VLC_LOGO}" group-title="NFL",{title}\n'
            )
            f.write(url + "\n\n")

    # TiviMate
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(
                f"{url}|referer={BASE}|origin={BASE}|user-agent={quote_plus(UA_CHROME)}\n"
            )

    log("✅ Playlists generated")

# --------------------------------------------------

async def main():
    log("🚀 Starting NFL Webcast scraper (repaired)…")

    html = ""
    try:
        html = requests.get(BASE, headers={"User-Agent": UA_CHROME}, timeout=15).text
    except Exception:
        pass

    if not html:
        async with async_playwright() as p:
            browser, ua = await launch_browser(p)
            ctx = await browser.new_context(user_agent=ua)
            pg = await ctx.new_page()
            await pg.goto(BASE, wait_until="domcontentloaded")
            html = await pg.content()
            await browser.close()

    soup = BeautifulSoup(html, "lxml")
    links = {
        urljoin(BASE, a["href"])
        for a in soup.find_all("a", href=True)
        if "live-stream" in a["href"]
    }

    log(f"🔍 Found {len(links)} event pages")

    if not links:
        log("❌ No event pages found")
        return

    results = []
    async with async_playwright() as p:
        for url in links:
            log(f"🔎 Processing {url}")
            m3u8 = await capture_m3u8(p, url)
            if m3u8:
                title = clean_event_title(url.split("/")[-2].replace("-", " "))
                results.append((title, m3u8))
                log(f"✅ {title}")
            else:
                log("⚠️ No stream")

    if results:
        write_playlists(results)
    else:
        log("❌ No streams captured")

# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
