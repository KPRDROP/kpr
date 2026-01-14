#!/usr/bin/env python3

import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# -------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)

HOMEPAGE = "https://nflwebcast.com/"

OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

DEFAULT_LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"

TVG_ID = "NFL.Dummy.us"
GROUP_TITLE = "NFL GAME"

# -------------------------------------------------
def log(*a):
    print(*a)
    sys.stdout.flush()

# -------------------------------------------------
def normalize_vs(text: str) -> str:
    text = re.sub(r"\s*@\s*", " vs ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# -------------------------------------------------
async def fetch_events_via_playwright(playwright):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    log("🌐 Loading homepage…")

    try:
        await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=30000)

        # ⏳ allow JS to inject content
        for _ in range(6):
            await page.wait_for_timeout(1000)
            anchors = await page.locator("a[href]").count()
            if anchors > 20:
                break

        html = await page.content()

    finally:
        await page.close()
        await context.close()
        await browser.close()

    soup = BeautifulSoup(html, "lxml")
    events = []

    # -------------------------------------------------
    # PRIMARY SELECTORS (robust)
    selectors = [
        "a[href*='nflwebcast.com'][href*='live']",
        "a[href*='-live-']",
        "a.dracula-style-link",
        "a[href*='online-free']",
    ]

    anchors = []
    for sel in selectors:
        anchors.extend(soup.select(sel))

    # -------------------------------------------------
    # FALLBACK: regex scan (last resort)
    if not anchors:
        for m in re.finditer(r'https://live\.nflwebcast\.com/[^"\']+', html):
            anchors.append({"href": m.group(0)})

    seen = set()

    for a in anchors:
        href = a.get("href") if hasattr(a, "get") else a["href"]
        if not href:
            continue

        url = urljoin(HOMEPAGE, href)
        if url in seen:
            continue
        seen.add(url)

        # ---- TITLE ----
        title_attr = a.get("title") if hasattr(a, "get") else None
        raw_text = ""
        if hasattr(a, "get_text"):
            raw_text = a.get_text(" ", strip=True)

        event_name = title_attr.strip() if title_attr else normalize_vs(raw_text)
        if not event_name:
            event_name = "NFL Game"

        # ---- LOGO ----
        logo = DEFAULT_LOGO
        if hasattr(a, "find"):
            img = a.find("img")
            if img and img.get("src"):
                logo = img["src"]

        events.append({
            "url": url,
            "event": event_name,
            "logo": logo
        })

    return events

# -------------------------------------------------
async def capture_m3u8_from_page(playwright, url, timeout_ms=25000):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    captured = None

    def resp_handler(resp):
        nonlocal captured
        if ".m3u8" in resp.url and not captured:
            captured = resp.url

    try:
        page.on("response", resp_handler)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass

        # click to trigger player
        for _ in range(2):
            try:
                await page.mouse.click(400, 300)
                await asyncio.sleep(1)
            except Exception:
                pass

        waited = 0.0
        while waited < 15 and not captured:
            await asyncio.sleep(0.6)
            waited += 0.6

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
        for e in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="{TVG_ID}" '
                f'tvg-name="{e["event"]}" '
                f'tvg-logo="{e["logo"]}" '
                f'group-title="{GROUP_TITLE}",{e["event"]}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={HOMEPAGE}\n")
            f.write(f"#EXTVLCOPT:http-origin={HOMEPAGE}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{e['m3u8']}\n\n")

    ua = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for e in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="{TVG_ID}" '
                f'tvg-name="{e["event"]}" '
                f'tvg-logo="{e["logo"]}",{e["event"]}\n'
            )
            f.write(
                f"{e['m3u8']}|referer={HOMEPAGE}|origin={HOMEPAGE}|user-agent={ua}\n"
            )

    log("✅ Playlists saved")

# -------------------------------------------------
async def main():
    log("🏈 Starting NFL Webcast Scraper...")

    async with async_playwright() as p:
        events = await fetch_events_via_playwright(p)
        log(f"📌 Found {len(events)} events")

        if not events:
            log("❌ No events detected")
            return

        collected = []

        for i, ev in enumerate(events, 1):
            log(f"🔎 [{i}/{len(events)}] {ev['event']}")
            m3u8 = await capture_m3u8_from_page(p, ev["url"])

            if m3u8:
                log(f"  ✅ STREAM FOUND: {m3u8}")
                ev["m3u8"] = m3u8
                collected.append(ev)
            else:
                log("  ⚠️ No streams found")

    if not collected:
        log("❌ No streams captured.")
        return

    write_playlists(collected)

# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
