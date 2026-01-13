#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.async_api import (
    async_playwright,
    TimeoutError,
    Error as PlaywrightError,
)

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
        await page.goto(
            HOMEPAGE,
            timeout=TIMEOUT,
            wait_until="domcontentloaded"
        )

        # Poll for injected content
        found = False
        for _ in range(10):
            if await page.locator("tr.singele_match_date").count() > 0:
                found = True
                break
            await page.wait_for_timeout(1000)

        if not found:
            print("⚠️ No match rows yet, continuing with fallback scan")

        # Primary detection
        rows = await page.locator("tr.singele_match_date").all()
        for row in rows:
            try:
                title = None
                for sel in ("td.teamvs a", "td.teamvs", "a"):
                    el = row.locator(sel)
                    if await el.count() > 0:
                        title = (await el.first.inner_text()).strip()
                        break

                link = row.locator("a[href*='live']")
                href = await link.first.get_attribute("href") if await link.count() else None

                if title and href:
                    events.append({
                        "title": title,
                        "url": urljoin(HOMEPAGE, href)
                    })
            except Exception:
                pass

        # Fallback
        if not events:
            print("🔁 Using fallback link scan")
            links = await page.locator("a[href*='live']").all()
            seen = set()

            for a in links:
                try:
                    href = await a.get_attribute("href")
                    if not href or href in seen:
                        continue
                    seen.add(href)

                    text = (await a.inner_text()).strip()
                    events.append({
                        "title": text or "NFL Game",
                        "url": urljoin(HOMEPAGE, href)
                    })
                except Exception:
                    pass

        await browser.close()

    return events

# -------------------------------------------------
async def extract_streams(page, url: str) -> list[str]:
    streams = set()
    captured = None

    # 🔥 NETWORK RESPONSE SNIFFER (KEY FIX)
    def on_response(resp):
        nonlocal captured
        try:
            if ".m3u8" in resp.url and "webcastserver" in resp.url:
                if not captured:
                    captured = resp.url
                    streams.add(resp.url)
        except Exception:
            pass

    page.on("response", on_response)

    try:
        # Load event page
        await page.goto(
            url,
            timeout=TIMEOUT,
            wait_until="domcontentloaded"
        )

        # Allow iframe + JS bootstrap
        await page.wait_for_timeout(6000)

        # Soft interaction fallback
        try:
            await page.mouse.click(400, 300)
            await asyncio.sleep(1)
            await page.mouse.click(400, 300)
        except Exception:
            pass

        waited = 0.0
        while waited < 15.0 and not captured:
            await asyncio.sleep(0.6)
            waited += 0.6

        if not captured:
            html = await page.content()
            streams |= extract_m3u8(html)

    except (TimeoutError, PlaywrightError):
        pass
    finally:
        try:
            page.off("response", on_response)
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
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ],
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

    # VLC playlist
    vlc = ["#EXTM3U"]
    for t, u in collected:
        vlc.append(f"#EXTINF:-1,{t}")
        vlc.append(u)
    Path(OUTPUT_VLC).write_text("\n".join(vlc), encoding="utf-8")

    # TiviMate playlist
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
