#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import urljoin, quote
from playwright.async_api import async_playwright

BASE = "https://streamfree.to"
STREAMS_PAGE = f"{BASE}/streams"

FIXTURES = [
    "/fixtures/soccer",
    "/fixtures/basketball",
    "/fixtures/football",
    "/fixtures/hockey",
    "/fixtures/baseball",
    "/fixtures/combat",
    "/fixtures/racing",
    "/fixtures/tennis",
]

OUTPUT_VLC = "Strmfree_VLC.m3u8"
OUTPUT_TIVIMATE = "Strmfree_TiviMate.m3u8"

TIMEOUT = 60000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------
def extract_m3u8(text: str) -> set[str]:
    found = set()

    # direct
    for m in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', text):
        found.add(m)

    # base64
    for b64 in re.findall(r'atob\(["\']([^"\']+)["\']\)', text):
        try:
            decoded = base64.b64decode(b64).decode("utf-8", "ignore")
            found |= extract_m3u8(decoded)
        except Exception:
            pass

    return found

# ---------------------------------------------------------
async def fetch_events():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        for path in FIXTURES:
            url = BASE + path
            print(f"🌐 Scanning {url}")
            try:
                await page.goto(url, timeout=TIMEOUT)
                await page.wait_for_timeout(2500)

                for a in await page.locator("a[href*='/stream/']").all():
                    try:
                        href = await a.get_attribute("href")
                        title = (await a.inner_text()).strip()

                        if href and title:
                            full = urljoin(BASE, href)
                            events.append({
                                "title": title,
                                "url": full
                            })
                    except Exception:
                        pass
            except Exception:
                pass

        await browser.close()

    # remove duplicates
    seen = {}
    for e in events:
        seen[e["url"]] = e
    return list(seen.values())

# ---------------------------------------------------------
async def extract_streams(page, context, url: str) -> list[str]:
    streams = set()

    # Network sniffing
    async def on_response(res):
        try:
            if res.request.resource_type in ("xhr", "fetch", "document", "script"):
                body = await res.text()
                streams |= extract_m3u8(body)
        except Exception:
            pass

    def on_request_finished(req):
        try:
            if ".m3u8" in req.url:
                streams.add(req.url)
        except Exception:
            pass

    page.on("response", on_response)
    context.on("requestfinished", on_request_finished)

    await page.goto(url, timeout=TIMEOUT)
    await page.wait_for_timeout(4000)

    # Click ALL possible play buttons in all frames
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

    await page.wait_for_timeout(12000)

    page.remove_listener("response", on_response)
    context.remove_listener("requestfinished", on_request_finished)

    return list(streams)

# ---------------------------------------------------------
async def main():
    events = await fetch_events()
    print(f"📌 Found {len(events)} events")

    if not events:
        print("❌ No events found")
        return

    collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
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
        print("❌ No streams captured")
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

# ---------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
