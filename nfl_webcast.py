#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.async_api import async_playwright, TimeoutError, Error as PlaywrightError

# -------------------------------------------------
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
async def fetch_events():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        print("🌐 Loading homepage…")
        await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=TIMEOUT)

        # wait for JS injected links
        found = False
        for _ in range(15):
            cnt = await page.locator(
                "a[href*='live-stream'], a[href*='live-stream-online']"
            ).count()
            if cnt > 0:
                found = True
                break
            await page.wait_for_timeout(1000)

        if found:
            print("✅ Events detected via JS render")
        else:
            print("⚠️ No events injected by JS")

        links = await page.locator(
            "a[href*='live-stream'], a[href*='live-stream-online']"
        ).all()

        seen = set()
        for a in links:
            try:
                href = await a.get_attribute("href")
                if not href or href in seen:
                    continue
                seen.add(href)

                title = (await a.inner_text()).strip()
                events.append({
                    "title": title or "NFL Game",
                    "url": urljoin(HOMEPAGE, href)
                })
            except Exception:
                pass

        await browser.close()

    return events

# -------------------------------------------------
async def extract_streams(page, url: str):
    streams = set()

    def on_response(resp):
        try:
            if ".m3u8" in resp.url and "webcastserver" in resp.url:
                streams.add(resp.url)
        except Exception:
            pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
        await page.wait_for_timeout(6000)

        # trigger player
        try:
            await page.mouse.click(400, 300)
            await asyncio.sleep(1)
            await page.mouse.click(400, 300)
        except Exception:
            pass

        waited = 0.0
        while waited < 15.0 and not streams:
            await asyncio.sleep(0.6)
            waited += 0.6

        if not streams:
            html = await page.content()
            for m in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html):
                streams.add(m)

    except (TimeoutError, PlaywrightError):
        pass
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

    return list(streams)

# -------------------------------------------------
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
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(user_agent=USER_AGENT)

        for i, ev in enumerate(events, 1):
            print(f"🔎 [{i}/{len(events)}] {ev['title']}")
            page = await ctx.new_page()

            streams = await extract_streams(page, ev["url"])
            if streams:
                for s in streams:
                    print(f"  ✅ STREAM FOUND: {s}")
                    collected.append((ev["title"], s))
            else:
                print("  ⚠️ No streams found")

            await page.close()

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
