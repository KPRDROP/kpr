#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path
from urllib.parse import quote, urljoin
from playwright.async_api import async_playwright

# --------------------------------------------------
HOMEPAGES = [
    "https://hiteasport.info",
]

BASE = "https://hiteasport.info/"

OUTPUT_VLC = "Streambtw_VLC.m3u8"
OUTPUT_TIVIMATE = "Streambtw_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

TIMEOUT = 60000
M3U8_RE = re.compile(r"https?://[^\"']+\.m3u8[^\"']*")

# --------------------------------------------------
async def goto_first_available(page):
    for url in HOMEPAGES:
        try:
            print(f"🌐 Trying homepage: {url}")
            await page.goto(url, timeout=TIMEOUT, wait_until="networkidle")
            print(f"✅ Connected: {url}")
            return True
        except Exception:
            pass
    return False

# --------------------------------------------------
async def fetch_events():
    events = []
    seen = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        if not await goto_first_available(page):
            await browser.close()
            return []

        # 🔥 wait for dynamic JS
        await page.wait_for_timeout(4000)

        # 1️⃣ try network JSON
        try:
            perf = await page.evaluate("""
                performance.getEntries()
                  .map(e => e.name)
                  .filter(n => n.includes("match") || n.includes("event"))
            """)
        except Exception:
            perf = []

        # 2️⃣ DOM fallback (AFTER JS)
        for card in await page.locator("a[href*='watch']").all():
            try:
                title = (await card.inner_text()).strip()
                href = await card.get_attribute("href")
                if not title or not href:
                    continue

                url = urljoin(BASE, href)
                if url in seen:
                    continue

                seen.add(url)
                events.append({"title": title, "url": url})
            except Exception:
                pass

        await browser.close()

    return events

# --------------------------------------------------
async def extract_streams(context, url, idx):
    streams = set()

    def on_request(req):
        if M3U8_RE.search(req.url):
            streams.add(req.url)

    context.on("requestfinished", on_request)

    page = await context.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
        await page.wait_for_timeout(3000)

        # click center (momentum click)
        box = await page.evaluate("""
            (() => {
                const el = document.querySelector("video, iframe, body");
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {x: r.x + r.width/2, y: r.y + r.height/2};
            })()
        """)

        if box:
            await page.mouse.click(int(box["x"]), int(box["y"]))
            await asyncio.sleep(1)
            await page.mouse.click(int(box["x"]), int(box["y"]))

        # wait for hls
        for _ in range(40):
            if streams:
                return list(streams)
            await asyncio.sleep(0.5)

    except Exception:
        pass

    finally:
        context.remove_listener("requestfinished", on_request)
        await page.close()

    return []

# --------------------------------------------------
async def main():
    events = await fetch_events()
    print(f"📌 Found {len(events)} events")

    if not events:
        print("❌ No events detected")
        return

    collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"]
        )
        ctx = await browser.new_context(user_agent=USER_AGENT)

        for i, ev in enumerate(events, 1):
            print(f"🔎 [{i}/{len(events)}] {ev['title']}")
            streams = await extract_streams(ctx, ev["url"], i)

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

# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
