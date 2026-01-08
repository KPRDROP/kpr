#!/usr/bin/env python3
import asyncio
import re
import base64
import warnings
from pathlib import Path
from urllib.parse import quote_plus

import requests
from urllib3.exceptions import InsecureRequestWarning
from playwright.async_api import async_playwright

# ---------------- CONFIG ----------------

API_EVENTS = "https://api.sporthub.tv/event"
BASE = "https://sporthub.tv/"

OUTPUT_VLC = "SportHub_VLC.m3u8"
OUTPUT_TIVI = "SportHub_TiviMate.m3u8"

TIMEOUT = 60000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

# ----------------------------------------

def log(*a):
    print(*a, flush=True)

# ----------------------------------------
# API FETCH (SSL BYPASS)
# ----------------------------------------

def fetch_events():
    warnings.simplefilter("ignore", InsecureRequestWarning)

    r = requests.get(
        API_EVENTS,
        timeout=20,
        verify=False,  # 🔥 SSL BYPASS
        headers={"User-Agent": USER_AGENT}
    )
    r.raise_for_status()
    data = r.json()

    events = []
    for ev in data:
        title = ev.get("title") or ev.get("name") or "SportHub Event"
        for s in ev.get("streams", []):
            embed = s.get("embed")
            if embed:
                events.append({
                    "title": title.strip(),
                    "url": embed
                })

    return events

# ----------------------------------------
# M3U8 EXTRACTION
# ----------------------------------------

def extract_m3u8(text: str) -> set[str]:
    found = set()

    for m in re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', text):
        found.add(m)

    for b64 in re.findall(r'atob\(["\']([^"\']+)["\']\)', text):
        try:
            dec = base64.b64decode(b64).decode("utf-8", "ignore")
            found |= extract_m3u8(dec)
        except Exception:
            pass

    return found

# ----------------------------------------
# PLAYWRIGHT CAPTURE
# ----------------------------------------

async def capture_stream(page, url: str) -> set[str]:
    streams = set()

    async def on_response(res):
        try:
            if res.request.resource_type in ("xhr", "fetch", "script", "document"):
                body = await res.text()
                streams.update(extract_m3u8(body))
        except Exception:
            pass

    page.on("response", on_response)

    await page.goto(url, timeout=TIMEOUT)
    await page.wait_for_timeout(3000)

    # 🔥 Momentum click (ads → player)
    try:
        pages_before = page.context.pages
        await page.mouse.click(300, 300)
        await asyncio.sleep(1)

        for _ in range(10):
            if len(page.context.pages) > len(pages_before):
                ad = [p for p in page.context.pages if p not in pages_before][0]
                await ad.close()
                break
            await asyncio.sleep(0.3)

        await asyncio.sleep(1)
        await page.mouse.click(300, 300)
    except Exception:
        pass

    await page.wait_for_timeout(10000)
    page.remove_listener("response", on_response)

    return streams

# ----------------------------------------
# MAIN
# ----------------------------------------

async def main():
    log("🚀 Starting SportHub Scraper (SSL BYPASS)")

    events = fetch_events()
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
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        for i, ev in enumerate(events, 1):
            log(f"🔎 [{i}/{len(events)}] {ev['title']}")
            streams = await capture_stream(page, ev["url"])

            if streams:
                for s in streams:
                    log(f"  ✅ {s}")
                    collected.append((ev["title"], s))
            else:
                log("  ⚠️ No streams found")

        await browser.close()

    if not collected:
        log("❌ No streams captured")
        return

    # VLC
    vlc = ["#EXTM3U"]
    for t, u in collected:
        vlc.append(f"#EXTINF:-1,{t}")
        vlc.append(u)
    Path(OUTPUT_VLC).write_text("\n".join(vlc), encoding="utf-8")

    # TiviMate
    ua = quote_plus(USER_AGENT)
    tm = ["#EXTM3U"]
    for t, u in collected:
        tm.append(f"#EXTINF:-1,{t}")
        tm.append(f"{u}|referer={BASE}|origin={BASE}|user-agent={ua}")
    Path(OUTPUT_TIVI).write_text("\n".join(tm), encoding="utf-8")

    log("✅ Playlists generated")

# ----------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
