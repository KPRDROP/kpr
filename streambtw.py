#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path
from urllib.parse import quote, urljoin
from playwright.async_api import async_playwright

HOMEPAGES = [
    "https://hiteasport.info/",
    "https://streambtw.com/",
]

BASE = "https://streambtw.com"

OUTPUT_VLC = "Streambtw_VLC.m3u8"
OUTPUT_TIVIMATE = "Streambtw_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

TIMEOUT = 60000
M3U8_RE = re.compile(r"\.m3u8(\?|$)")

# -------------------------------------------------
async def goto_first_available(page):
    for url in HOMEPAGES:
        try:
            print(f"🌐 Trying homepage: {url}")
            await page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            print(f"✅ Connected: {url}")
            return True
        except Exception:
            pass
    return False

# -------------------------------------------------
async def fetch_events():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        if not await goto_first_available(page):
            await browser.close()
            return []

        for match in await page.locator(".match").all():
            try:
                title = (await match.locator(".match-title").inner_text()).strip()
                href = await match.locator("a.watch-btn").get_attribute("href")
                if title and href:
                    events.append({
                        "title": title,
                        "url": urljoin(BASE, href)
                    })
            except Exception:
                pass

        await browser.close()
    return events

# -------------------------------------------------
async def extract_streams(page, context, url):
    streams = set()

    def on_request(req):
        if M3U8_RE.search(req.url):
            streams.add(req.url)

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    except Exception:
        page.remove_listener("request", on_request)
        return []

    await page.wait_for_timeout(3000)

    # find iframe
    iframe = None
    for f in page.frames:
        if f.url and ("embed" in f.url or "hiteasport" in f.url):
            iframe = f
            break

    # momentum click
    pages_before = context.pages.copy()

    try:
        target = iframe if iframe else page
        await target.mouse.click(300, 300)
        await asyncio.sleep(1)

        # close ad tab
        for _ in range(10):
            if len(context.pages) > len(pages_before):
                ad = [p for p in context.pages if p not in pages_before][0]
                await ad.close()
                break
            await asyncio.sleep(0.3)

        # second click starts player
        await target.mouse.click(300, 300)

    except Exception:
        pass

    # wait for HLS
    for _ in range(30):
        if streams:
            break
        await asyncio.sleep(0.5)

    page.remove_listener("request", on_request)
    return list(streams)

# -------------------------------------------------
async def main():
    events = await fetch_events()
    print(f"📌 Found {len(events)} events")

    if not events:
        return

    collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"]
        )
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        for i, ev in enumerate(events, 1):
            print(f"🔎 [{i}/{len(events)}] {ev['title']}")
            streams = await extract_streams(page, ctx, ev["url"])

            if streams:
                for s in streams:
                    print(f"  ✅ STREAM FOUND: {s}")
                    collected.append((ev["title"], s))
            else:
                print("  ⚠️ No streams found")

        await browser.close()

    if not collected:
        print("❌ No streams captured.")
        return

    # VLC
    vlc = ["#EXTM3U"]
    for t, u in collected:
        vlc.append(f"#EXTINF:-1,{t}")
        vlc.append(u)
    Path(OUTPUT_VLC).write_text("\n".join(vlc), encoding="utf-8")

    # TiviMate
    ua = quote(USER_AGENT)
    tm = ["#EXTM3U"]
    for t, u in collected:
        tm.append(f"#EXTINF:-1,{t}")
        tm.append(f"{u}|referer={BASE}/|origin={BASE}|user-agent={ua}")
    Path(OUTPUT_TIVIMATE).write_text("\n".join(tm), encoding="utf-8")

    print("✅ Playlists saved")

# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
