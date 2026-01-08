#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

HOMEPAGE = "https://streambtw.com/"
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
# m3u8 extraction (robust)
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
# Fetch events (THIS PART WAS ALREADY CORRECT)
# -------------------------------------------------
async def fetch_events():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        await page.goto(HOMEPAGE, timeout=TIMEOUT)
        await page.wait_for_timeout(2000)

        for match in await page.locator(".match").all():
            try:
                title = (await match.locator(".match-title").inner_text()).strip()
                href = await match.locator("a.watch-btn").get_attribute("href")
                if title and href:
                    events.append({"title": title, "url": href})
            except Exception:
                pass

        await browser.close()

    return events


# -------------------------------------------------
# STREAM EXTRACTION (REAL FIX)
# -------------------------------------------------
async def extract_streams_from_event(page, url: str) -> list[str]:
    streams = set()

    async def attach_listener(frame):
        async def on_response(res):
            try:
                if res.request.resource_type in ("xhr", "fetch", "document", "script"):
                    body = await res.text()
                    streams.update(extract_m3u8(body))
            except Exception:
                pass

        frame.on("response", on_response)

    # Load event page
    await page.goto(url, timeout=TIMEOUT)
    await page.wait_for_timeout(4000)

    # Attach listeners to all frames
    for frame in page.frames:
        await attach_listener(frame)

    # CLICK PLAY BUTTONS INSIDE IFRAMES
    for _ in range(4):
        for frame in page.frames:
            try:
                for sel in (
                    "button",
                    ".play",
                    ".jw-icon-play",
                    ".vjs-big-play-button",
                    "[onclick]"
                ):
                    for el in await frame.locator(sel).all():
                        try:
                            await el.click(force=True, timeout=1000)
                            await page.wait_for_timeout(2000)
                        except Exception:
                            pass
            except Exception:
                pass

    # WAIT FOR STREAM TO LOAD (IMPORTANT)
    await page.wait_for_timeout(12000)

    return list(streams)


# -------------------------------------------------
# MAIN
# -------------------------------------------------
async def main():
    events = await fetch_events()
    print(f"📌 Found {len(events)} events")

    collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        for i, ev in enumerate(events, 1):
            print(f"🔎 [{i}/{len(events)}] {ev['title']}")
            streams = await extract_streams_from_event(page, ev["url"])

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


if __name__ == "__main__":
    asyncio.run(main())
