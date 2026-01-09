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

        # Allow JS to inject rows
        await page.wait_for_timeout(3000)

        rows = await page.locator("tr.singele_match_date").all()

        # Fallback if class changes
        if not rows:
            rows = await page.locator("tr[class*='match']").all()

        for row in rows:
            try:
                title = None
                for sel in ("td.teamvs a", "td.teamvs", "a"):
                    el = row.locator(sel)
                    if await el.count() > 0:
                        title = (await el.first.inner_text()).strip()
                        break

                href = None
                link = row.locator("a[href*='live']")
                if await link.count() > 0:
                    href = await link.first.get_attribute("href")

                if title and href:
                    events.append({
                        "title": title,
                        "url": urljoin(HOMEPAGE, href)
                    })
            except Exception:
                continue

        # Final fallback: scan links
        if not events:
            links = await page.locator("a[href*='live']").all()
            for a in links:
                try:
                    href = await a.get_attribute("href")
                    text = (await a.inner_text()).strip()
                    if href and "nfl" in href.lower():
                        events.append({
                            "title": text or "NFL Game",
                            "url": urljoin(HOMEPAGE, href)
                        })
                except Exception:
                    pass

        await browser.close()

    return events

# -------------------------------------------------
async def extract_streams(page, context, url: str) -> list[str]:
    streams = set()
    captured = None

    # --- RESPONSE SNIFFER (MOST IMPORTANT) ---
    def on_response(resp):
        nonlocal captured
        try:
            rurl = resp.url
            if ".m3u8" in rurl:
                streams.add(rurl)
                if not captured:
                    captured = rurl
        except Exception:
            pass

    page.on("response", on_response)

    try:
        await page.goto(
            url,
            timeout=TIMEOUT,
            wait_until="domcontentloaded"
        )

        # Let JS + ads initialize
        await page.wait_for_timeout(5000)

        # --- CLICK PLAYER (momentum click) ---
        try:
            for frame in page.frames:
                if frame.url and ("stream" in frame.url or "player" in frame.url):
                    try:
                        el = await frame.query_selector("video, iframe, body")
                        if el:
                            box = await el.bounding_box()
                            if box:
                                x = int(box["x"] + box["width"] / 2)
                                y = int(box["y"] + box["height"] / 2)
                                await page.mouse.click(x, y)
                                await asyncio.sleep(1)
                                await page.mouse.click(x, y)
                                break
                    except Exception:
                        continue
        except Exception:
            pass

        # --- WAIT FOR NETWORK ---
        total = 0
        while total < 12 and not streams:
            await asyncio.sleep(0.6)
            total += 0.6

        # --- FALLBACK: HTML + BASE64 SCAN ---
        if not streams:
            html = await page.content()

            for m in re.findall(
                r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
                html
            ):
                streams.add(m)

            for b64 in re.findall(r'atob\(["\']([^"\']+)["\']\)', html):
                try:
                    decoded = base64.b64decode(b64).decode("utf-8", "ignore")
                    for m in re.findall(
                        r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*',
                        decoded
                    ):
                        streams.add(m)
                except Exception:
                    pass

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
