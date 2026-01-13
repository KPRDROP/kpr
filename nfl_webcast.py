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

        # 🔁 Poll for content (XHR injected)
        found = False
        for _ in range(10):
            rows = await page.locator("tr.singele_match_date").count()
            links = await page.locator("a[href*='live']").count()
            if rows > 0 or links > 0:
                found = True
                break
            await page.wait_for_timeout(1000)

        if not found:
            print("⚠️ No match rows yet, continuing with fallback scan")

        # --- Primary: table rows ---
        rows = await page.locator("tr.singele_match_date").all()
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
                pass

        # --- Fallback: scan all live links ---
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
                    if "nfl" in href.lower():
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
    player_urls = set()

    # --- Capture ALL m3u8 at context level ---
    def on_request_finished(req):
        try:
            if ".m3u8" in req.url:
                streams.add(req.url)
        except Exception:
            pass

    context.on("requestfinished", on_request_finished)

    try:
        # --------------------------------------------------
        # 1️⃣ Load event page
        # --------------------------------------------------
        await page.goto(url, timeout=TIMEOUT, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        html = await page.content()

        # --------------------------------------------------
        # 2️⃣ Extract embedded player iframe URLs
        # --------------------------------------------------
        iframe_urls = re.findall(
            r'<iframe[^>]+src=["\']([^"\']+)["\']',
            html,
            re.I
        )

        for src in iframe_urls:
            if any(x in src for x in ("webcast", "player", "embed", "stream")):
                player_urls.add(src)

        # Absolute URLs
        player_urls = {
            urljoin(url, p) for p in player_urls
        }

        # --------------------------------------------------
        # 3️⃣ Visit each player iframe directly
        # --------------------------------------------------
        for purl in player_urls:
            if streams:
                break

            try:
                await page.goto(
                    purl,
                    timeout=TIMEOUT,
                    wait_until="domcontentloaded"
                )

                # Let network requests fire
                waited = 0.0
                while waited < 12.0 and not streams:
                    await asyncio.sleep(0.6)
                    waited += 0.6

                # --------------------------------------------------
                # 4️⃣ Fallback click inside player
                # --------------------------------------------------
                if not streams:
                    try:
                        await page.mouse.click(400, 300)
                        await asyncio.sleep(1)
                        await page.mouse.click(400, 300)
                    except Exception:
                        pass

                    waited = 0.0
                    while waited < 8.0 and not streams:
                        await asyncio.sleep(0.6)
                        waited += 0.6

                # --------------------------------------------------
                # 5️⃣ Final HTML scan fallback
                # --------------------------------------------------
                if not streams:
                    html = await page.content()
                    streams |= extract_m3u8(html)

            except Exception:
                continue

    except (TimeoutError, PlaywrightError):
        pass
    finally:
        try:
            context.remove_listener("requestfinished", on_request_finished)
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
