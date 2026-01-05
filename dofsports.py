#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote
from playwright.async_api import async_playwright

HOMEPAGE = "http://dofusports.xyz/sport/nhl/"
BASE = "https://stellarthread.com/"

OUTPUT_VLC = "Streambtw_VLC.m3u8"
OUTPUT_TIVIMATE = "Streambtw_TiviMate.m3u8"

TIMEOUT = 60000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

# -------------------------------------------------

def extract_m3u8(text: str) -> set[str]:
    found = set()

    # direct urls
    for m in re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', text):
        found.add(m)

    # base64 encoded urls
    for b64 in re.findall(r'atob\(["\']([^"\']+)["\']\)', text):
        try:
            decoded = base64.b64decode(b64).decode("utf-8", "ignore")
            for m in extract_m3u8(decoded):
                found.add(m)
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

        await page.goto(HOMEPAGE, timeout=TIMEOUT)
        await page.wait_for_timeout(3000)

        for card in await page.locator(".schedule .match").all():
            try:
                title = (await card.locator(".match-title").inner_text()).strip()
                href = await card.locator("a.watch-btn").get_attribute("href")
                if href:
                    if href.startswith("/"):
                        href = BASE + href
                    events.append({"title": title, "url": href})
            except Exception:
                pass

        await browser.close()

    return events

# -------------------------------------------------

async def extract_streams(page, url: str) -> list[str]:
    streams = set()

    async def on_response(res):
        try:
            if res.request.resource_type in ("xhr", "fetch", "document", "script"):
                body = await res.text()
                for s in extract_m3u8(body):
                    streams.add(s)
        except Exception:
            pass

    page.on("response", on_response)

    await page.goto(url, timeout=TIMEOUT)
    await page.wait_for_timeout(4000)

    # CLICK ALL SERVER / PLAY BUTTONS
    for _ in range(3):
        for frame in page.frames:
            try:
                for sel in (
                    "button",
                    ".play",
                    ".jw-icon-play",
                    ".vjs-big-play-button",
                    "[onclick]",
                    "div"
                ):
                    for el in await frame.locator(sel).all():
                        try:
                            await el.click(force=True, timeout=1500)
                            await page.wait_for_timeout(2000)
                        except Exception:
                            pass
            except Exception:
                pass

    # WAIT FOR PLAYER JS TO EXECUTE
    await page.wait_for_timeout(10000)

    page.remove_listener("response", on_response)
    return list(streams)

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
            streams = await extract_streams(page, ev["url"])

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
