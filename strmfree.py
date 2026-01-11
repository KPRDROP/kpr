#!/usr/bin/env python3
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote
from playwright.async_api import async_playwright

BASE = "https://streamfree.to"
STREAMS_URL = f"{BASE}/streams"

OUTPUT_VLC = "Strmfree_VLC.m3u8"
OUTPUT_TIVIMATE = "Strmfree_TiviMate.m3u8"

TIMEOUT = 60000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------
def log(*a):
    print(*a, flush=True)

# ---------------------------------------------------------
async def fetch_events():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        print("🌐 Loading streams page…")
        await page.goto(STREAMS_PAGE, wait_until="domcontentloaded", timeout=TIMEOUT)
        await page.wait_for_timeout(4000)

        html = await page.content()
        await browser.close()

    # -------------------------------------------------
    # 🔥 Extract Nuxt embedded JSON (robust)
    # -------------------------------------------------
    nuxt_match = re.search(
        r'(?:window\.)?__NUXT__\s*=\s*({.*?})\s*;',
        html,
        re.DOTALL
    )

    if not nuxt_match:
        print("❌ __NUXT__ state not found in page")
        return []

    try:
        nuxt = json.loads(nuxt_match.group(1))
    except Exception as e:
        print("❌ Failed to parse Nuxt JSON:", e)
        return []

    # -------------------------------------------------
    # 🔍 Locate streams inside Nuxt state
    # -------------------------------------------------
    state = nuxt.get("state", {})
    streams = None

    # try all known layouts (StreamFree changes often)
    for path in (
        lambda s: s.get("streams"),
        lambda s: s.get("events"),
        lambda s: s.get("data", {}).get("streams"),
        lambda s: s.get("streamfree", {}).get("streams"),
    ):
        try:
            streams = path(state)
            if streams:
                break
        except:
            pass

    if not streams:
        print("❌ Streams array not found in Nuxt state")
        return []

    # -------------------------------------------------
    # ✅ Build events
    # -------------------------------------------------
    for ev in streams:
        try:
            name = ev.get("name")
            category = ev.get("category")
            logo = ev.get("thumbnail_url")

            if not name or not category:
                continue

            events.append({
                "title": name.replace("-", " ").title(),
                "category": category,
                "logo": logo,
                "url": f"{BASE}/player/{category}/{name}"
            })
        except:
            pass

    # dedupe
    uniq = {e["url"]: e for e in events}
    return list(uniq.values())

# ---------------------------------------------------------
async def extract_m3u8(page, context, url):
    streams = set()

    def on_request_finished(req):
        try:
            if ".m3u8" in req.url:
                streams.add(req.url)
        except Exception:
            pass

    context.on("requestfinished", on_request_finished)

    await page.goto(url, timeout=TIMEOUT)
    await page.wait_for_timeout(3000)

    # Click play buttons aggressively
    for _ in range(3):
        for frame in page.frames:
            try:
                for sel in (
                    "button",
                    ".play",
                    ".jw-icon-play",
                    ".vjs-big-play-button",
                    "[onclick]",
                    "video",
                    "div"
                ):
                    for el in await frame.locator(sel).all():
                        try:
                            await el.click(force=True, timeout=1200)
                            await page.wait_for_timeout(1500)
                        except Exception:
                            pass
            except Exception:
                pass

    await page.wait_for_timeout(12000)

    context.remove_listener("requestfinished", on_request_finished)
    return list(streams)

# ---------------------------------------------------------
async def main():
    log("🚀 Starting StreamFree scraper (API → Player → m3u8)")

    events = await fetch_events()
    log(f"📌 Found {len(events)} events")

    if not events:
        log("❌ No events found")
        return

    collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(
            user_agent=USER_AGENT,
            java_script_enabled=True,
            referer=BASE + "/"
        )
        page = await ctx.new_page()

        for i, ev in enumerate(events, 1):
            log(f"🔎 [{i}/{len(events)}] {ev['title']} ({ev['category']})")
            streams = await extract_m3u8(page, ctx, ev["url"])

            for s in streams:
                if s.endswith(".m3u8"):
                    log(f"  ✅ STREAM FOUND: {s}")
                    collected.append((ev, s))

        await browser.close()

    if not collected:
        log("❌ No streams captured")
        return

    # VLC playlist
    vlc = ["#EXTM3U"]
    for ev, u in collected:
        vlc.append(
            f'#EXTINF:-1 group-title="{ev["category"].upper()}",{ev["title"]}'
        )
        vlc.append(f"#EXTVLCOPT:http-referrer={BASE}/")
        vlc.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc.append(u)

    Path(OUTPUT_VLC).write_text("\n".join(vlc), encoding="utf-8")

    # TiviMate playlist
    ua = quote(USER_AGENT)
    tm = ["#EXTM3U"]
    for ev, u in collected:
        tm.append(
            f'#EXTINF:-1 group-title="{ev["category"].upper()}",{ev["title"]}'
        )
        tm.append(f"{u}|referer={BASE}/|origin={BASE}|user-agent={ua}")

    Path(OUTPUT_TIVIMATE).write_text("\n".join(tm), encoding="utf-8")

    log("✅ Playlists saved")

# ---------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
