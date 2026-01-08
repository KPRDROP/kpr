#!/usr/bin/env python3
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import quote
import requests
from playwright.async_api import async_playwright

API_EVENTS = "https://api.sporthub.tv/event"
BASE = "https://sporthub.tv"

OUTPUT_VLC = "SportHub_VLC.m3u8"
OUTPUT_TIVI = "SportHub_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

TIMEOUT = 60000


# ---------------- API ----------------

def fetch_events():
    r = requests.get(API_EVENTS, timeout=20)
    r.raise_for_status()
    data = r.json()

    events = []
    for ev in data:
        title = ev.get("title") or ev.get("name") or "SportHub Event"
        streams = ev.get("streams") or []
        for s in streams:
            embed = s.get("embed")
            if embed:
                events.append({
                    "title": title,
                    "url": embed
                })
    return events


# ---------------- PLAYWRIGHT ----------------

async def momentum_click(page):
    # intento directo
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
            el = await page.query_selector(sel)
            if el:
                await el.click(timeout=500)
                break
        except:
            pass

    # secuencia humana
    pages_before = list(page.context.pages)
    await page.mouse.click(200, 200)
    await asyncio.sleep(0.8)

    for _ in range(12):
        pages_now = page.context.pages
        if len(pages_now) > len(pages_before):
            popup = [p for p in pages_now if p not in pages_before][0]
            try:
                await popup.close()
            except:
                pass
            break
        await asyncio.sleep(0.25)

    await asyncio.sleep(0.8)
    await page.mouse.click(200, 200)


async def capture_m3u8(page, url):
    found = set()

    async def on_response(res):
        try:
            if res.request.resource_type in ("xhr", "fetch", "media"):
                if ".m3u8" in res.url:
                    found.add(res.url)
        except:
            pass

    page.on("response", on_response)

    await page.goto(url, timeout=TIMEOUT)
    await page.wait_for_timeout(2000)

    await momentum_click(page)

    # esperar tráfico
    for _ in range(15):
        if found:
            break
        await page.wait_for_timeout(700)

    page.remove_listener("response", on_response)
    return list(found)


# ---------------- MAIN ----------------

async def main():
    events = fetch_events()
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
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        for i, ev in enumerate(events, 1):
            print(f"🔎 [{i}/{len(events)}] {ev['title']}")
            streams = await capture_m3u8(page, ev["url"])

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
        tm.append(f"{u}|referer={BASE}|origin={BASE}|user-agent={ua}")
    Path(OUTPUT_TIVI).write_text("\n".join(tm), encoding="utf-8")

    print("✅ Playlists saved")


if __name__ == "__main__":
    asyncio.run(main())
