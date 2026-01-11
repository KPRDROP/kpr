#!/usr/bin/env python3
import asyncio
import re
import json
import base64
from pathlib import Path
from urllib.parse import quote
from playwright.async_api import async_playwright

BASE = "https://streamfree.to"
STREAMS_PAGE = f"{BASE}/streams"

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

    for m in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', text):
        found.add(m)

    for b64 in re.findall(r'atob\(["\']([^"\']+)["\']\)', text):
        try:
            decoded = base64.b64decode(b64).decode("utf-8", "ignore")
            found |= extract_m3u8(decoded)
        except:
            pass

    return found

# ---------------------------------------------------------
async def fetch_events():
    print("🌐 Loading streams page…")
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        await page.goto(STREAMS_PAGE, wait_until="domcontentloaded", timeout=TIMEOUT)
        await page.wait_for_timeout(4000)

        html = await page.content()
        await browser.close()

    # 🔥 Extract Nuxt embedded JSON (robust)
    nuxt_match = re.search(
        r'(?:window\.)?__NUXT__\s*=\s*({.*?})\s*;',
        html,
        re.DOTALL
    )

    if not nuxt_match:
        print("❌ __NUXT__ state not found")
        return []

    try:
        nuxt = json.loads(nuxt_match.group(1))
    except Exception as e:
        print("❌ Failed parsing Nuxt JSON:", e)
        return []

    state = nuxt.get("state", {})
    streams = None

    # StreamFree moves this often — try all known paths
    for getter in (
        lambda s: s.get("streams"),
        lambda s: s.get("events"),
        lambda s: s.get("data", {}).get("streams"),
        lambda s: s.get("streamfree", {}).get("streams"),
    ):
        try:
            streams = getter(state)
            if streams:
                break
        except:
            pass

    if not streams:
        print("❌ Streams not found in Nuxt state")
        return []

    for ev in streams:
        try:
            name = ev.get("name")
            category = ev.get("category")
            logo = ev.get("thumbnail_url")

            if not name or not category:
                continue

            events.append({
                "title": name.replace("-", " ").title(),
                "url": f"{BASE}/player/{category}/{name}",
                "logo": logo
            })
        except:
            pass

    uniq = {e["url"]: e for e in events}
    return list(uniq.values())

# ---------------------------------------------------------
async def extract_streams(page, context, url: str) -> list[str]:
    streams = set()

    async def on_response(res):
        try:
            if res.request.resource_type in ("xhr", "fetch", "script", "document"):
                body = await res.text()
                streams |= extract_m3u8(body)
        except:
            pass

    def on_request_finished(req):
        try:
            if ".m3u8" in req.url:
                streams.add(req.url)
        except:
            pass

    page.on("response", on_response)
    context.on("requestfinished", on_request_finished)

    await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    await page.wait_for_timeout(3000)

    # 👆 Click play buttons in all frames
    for _ in range(3):
        for frame in page.frames:
            for sel in (
                "button",
                ".play",
                ".jw-icon-play",
                ".vjs-big-play-button",
                "[onclick]",
                "video",
                "div"
            ):
                try:
                    for el in await frame.locator(sel).all():
                        try:
                            await el.click(force=True, timeout=1200)
                            await page.wait_for_timeout(2000)
                        except:
                            pass
                except:
                    pass

    await page.wait_for_timeout(12000)

    page.remove_listener("response", on_response)
    context.remove_listener("requestfinished", on_request_finished)

    return list(streams)

# ---------------------------------------------------------
async def main():
    print("🚀 Starting StreamFree scraper (API → Player → m3u8)")

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

# ---------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
