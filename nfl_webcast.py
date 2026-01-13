#!/usr/bin/env python3

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# -------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)

HOMEPAGE = "https://nflwebcast.com/"
BASE = "https://live.nflwebcast.com/"

OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

HEADERS = {
    "user-agent": USER_AGENT,
    "referer": HOMEPAGE,
    "origin": HOMEPAGE,
}

NFL_LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"

# -------------------------------------------------
def log(*a):
    print(*a)
    sys.stdout.flush()

# -------------------------------------------------
def clean_event_title(title: str) -> str:
    if not title:
        return "NFL Game"
    title = title.replace("@", "vs")
    title = title.replace(",", "")
    title = re.sub(r"\s{2,}", " ", title).strip()
    return title

# -------------------------------------------------
def find_events_from_homepage(html: str) -> list:
    soup = BeautifulSoup(html, "lxml")
    events = []

    # Primary: Watch buttons
    for a in soup.select("a[href*='live-stream']"):
        href = a.get("href")
        if not href:
            continue
        url = urljoin(HOMEPAGE, href)
        title = a.text.strip()
        events.append((url, title))

    # Fallback: any live.nflwebcast.com links
    if not events:
        for a in soup.find_all("a", href=True):
            if "live.nflwebcast.com" in a["href"]:
                url = urljoin(HOMEPAGE, a["href"])
                title = a.text.strip()
                events.append((url, title))

    # Deduplicate
    seen = set()
    out = []
    for url, title in events:
        if url not in seen:
            seen.add(url)
            out.append((url, title))

    return out

# -------------------------------------------------
async def capture_m3u8_from_page(playwright, url, timeout_ms=25000):
    browser = await playwright.firefox.launch(
        headless=True,
        args=["--no-sandbox"]
    )
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    captured = None

    def resp_handler(resp):
        nonlocal captured
        try:
            if ".m3u8" in resp.url and not captured:
                captured = resp.url
        except Exception:
            pass

    try:
        page.on("response", resp_handler)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            log(f"⚠️ Timeout loading {url}")

        # Trigger player
        for _ in range(2):
            try:
                await page.mouse.click(400, 300)
                await asyncio.sleep(1)
            except Exception:
                pass

        waited = 0.0
        while waited < 12.0 and not captured:
            await asyncio.sleep(0.6)
            waited += 0.6

        # HTML fallback
        if not captured:
            html = await page.content()
            m = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)
            if m:
                captured = m.group(0)

    finally:
        try:
            await page.close()
            await context.close()
            await browser.close()
        except Exception:
            pass

    return captured

# -------------------------------------------------
def write_playlists(entries):
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="NFL.Dummy.us" '
                f'tvg-name="NFL" tvg-logo="{NFL_LOGO}" '
                f'group-title="NFL GAME",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={HOMEPAGE}\n")
            f.write(f"#EXTVLCOPT:http-origin={HOMEPAGE}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{url}\n\n")

    ua = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(
                f"{url}|referer={HOMEPAGE}|origin={HOMEPAGE}|user-agent={ua}\n"
            )

    log("✅ Playlists saved")

# -------------------------------------------------
async def main():
    log("🏈 Starting NFL Webcast Scraper...")

    try:
        r = requests.get(HOMEPAGE, headers=HEADERS, timeout=15)
        r.raise_for_status()
        homepage_html = r.text
    except Exception as e:
        log(f"❌ Failed to load homepage: {e}")
        return

    events = find_events_from_homepage(homepage_html)
    log(f"📌 Found {len(events)} events")

    if not events:
        log("❌ No events detected")
        return

    collected = []

    async with async_playwright() as p:
        for i, (url, title_hint) in enumerate(events, 1):
            log(f"🔎 [{i}/{len(events)}] {title_hint or 'NFL Game'}")
            m3u8 = await capture_m3u8_from_page(p, url)

            if m3u8:
                title = clean_event_title(title_hint)
                log(f"  ✅ STREAM FOUND: {m3u8}")
                collected.append((title, m3u8))
            else:
                log("  ⚠️ No streams found")

    if not collected:
        log("❌ No streams captured.")
        return

    write_playlists(collected)

# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
