#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote, urljoin
from playwright.async_api import async_playwright, TimeoutError

HOMEPAGE = "https://nflwebcast.com"
BASE = "https://live.nflwebcast.com"

OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVIMATE = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

TIMEOUT = 60000

# -------------------------------------------------
def extract_m3u8(text: str) -> set[str]:
    found = set()
    for m in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', text):
        found.add(m)
    for b64 in re.findall(r'atob\(["\']([^"\']+)["\']\)', text):
        try:
            decoded = base64.b64decode(b64).decode("utf-8", "ignore")
            found |= extract_m3u8(decoded)
        except Exception:
            pass
    return found

# -------------------------------------------------
async def fetch_events():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        print("🌐 Loading homepage…")
        await page.goto(HOMEPAGE, timeout=TIMEOUT, wait_until="networkidle")

        # Give JS time to render cards
        await page.wait_for_timeout(3000)

        # 🔎 Robust selector: any watch link
        links = await page.locator('a[href*="/watch"]').all()

        for a in links:
            try:
                href = await a.get_attribute("href")
                title = (await a.inner_text() or "").strip()

                if not href:
                    continue

                url = urljoin(HOMEPAGE, href)

                if not title:
                    title = url.split("/")[-1].replace("-", " ").title()

                events.append({
                    "title": title,
                    "url": url,
                })
            except Exception:
                pass

        await browser.close()

    return events

# -------------------------------------------------
async def extract_streams(page, context, url: str) -> list[str]:
    streams = set()

    def on_request_finished(req):
        try:
            if ".m3u8" in req.url:
                streams.add(req.url)
        except Exception:
            pass

    context.on("requestfinished", on_request_finished)

    try:
        await page.goto(url, timeout=TIMEOUT)
        await page.wait_for_timeout(4000)

        # iframe detection
        iframe = None
        for f in page.frames:
            if f.url and ("hiteasport" in f.url or "stream" in f.url):
                iframe = f
                break

        box = None
        if iframe:
            try:
                el = await iframe.query_selector("video, iframe, body")
                if el:
                    box = await el.bounding_box()
            except Exception:
                pass

        x = int(box["x"] + box["width"] / 2) if box else 300
        y = int(box["y"] + box["height"] / 2) if box else 300

        pages_before = context.pages

        # Momentum click
        await page.mouse.click(x, y)
        await asyncio.sleep(1)

        # Close ad tab
        for _ in range(10):
            pages_now = context.pages
            if len(pages_now) > len(pages_before):
                ad = [p for p in pages_now if p not in pages_before][0]
                await ad.close()
                break
            await asyncio.sleep(0.3)

        # Second click
        await page.mouse.click(x, y)

        await page.wait_for_timeout(15000)

    except TimeoutError:
        pass
    finally:
        context.remove_listener("requestfinished", on_request_finished)

    return list(streams)

# -------------------------------------------------
async def main():
    events = await fetch_events()
    print(f"📌 Found {len(events)} events")

    if not events:
        print("❌ No events detected (site layout may have changed)")
        return

    collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ],
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
