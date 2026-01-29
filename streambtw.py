#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote, urljoin
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# 🔁 MULTIPLE ENTRY POINTS
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
async def goto_first_available(page):
    for url in HOMEPAGES:
        try:
            print(f"🌐 Trying homepage: {url}")
            await page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            print(f"✅ Connected: {url}")
            return True
        except Exception as e:
            print(f"❌ Failed: {url} ({e})")
    return False

# -------------------------------------------------
async def fetch_events():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        ok = await goto_first_available(page)
        if not ok:
            print("❌ All homepages unreachable.")
            await browser.close()
            return []

        for match in await page.locator(".match").all():
            try:
                title = (await match.locator(".match-title").inner_text()).strip()
                href = await match.locator("a.watch-btn").get_attribute("href")

                if title and href:
                    full_url = urljoin(BASE, href)
                    events.append({
                        "title": title,
                        "url": full_url
                    })
            except Exception:
                pass

        await browser.close()

    return events

# -------------------------------------------------
async def extract_streams(page, context, url: str) -> list[str]:
    streams = set()

    if not url.startswith("http"):
        url = urljoin(BASE, url)

    def on_request_finished(req):
        try:
            if ".m3u8" in req.url:
                streams.add(req.url)
        except Exception:
            pass

    context.on("requestfinished", on_request_finished)

    try:
        await page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
    except (PlaywrightTimeout, Exception):
        context.remove_listener("requestfinished", on_request_finished)
        return []

    await page.wait_for_timeout(4000)

    iframe = None
    for f in page.frames:
        if f.url and ("hiteasport" in f.url or "embed" in f.url):
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

    x = int(box["x"] + box["width"] / 2) if box else 200
    y = int(box["y"] + box["height"] / 2) if box else 200

    pages_before = context.pages.copy()

    try:
        await page.mouse.click(x, y)
        await asyncio.sleep(1)

        for _ in range(10):
            pages_now = context.pages
            if len(pages_now) > len(pages_before):
                ad = [p for p in pages_now if p not in pages_before][0]
                await ad.close()
                break
            await asyncio.sleep(0.3)

        await page.mouse.click(x, y)
    except Exception:
        pass

    await page.wait_for_timeout(15000)

    context.remove_listener("requestfinished", on_request_finished)
    return list(streams)

# -------------------------------------------------
async def main():
    events = await fetch_events()
    print(f"📌 Found {len(events)} events")

    if not events:
        print("❌ No events found.")
        return

    collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required"
            ]
        )
        ctx = await browser.new_context(
            user_agent=USER_AGENT,
            java_script_enabled=True
        )
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

    vlc = ["#EXTM3U"]
    for t, u in collected:
        vlc.append(f"#EXTINF:-1,{t}")
        vlc.append(u)
    Path(OUTPUT_VLC).write_text("\n".join(vlc), encoding="utf-8")

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
