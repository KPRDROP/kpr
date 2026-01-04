#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

HOMEPAGE = "https://streambtw.com/"
BASE = "https://streambtw.live"
OUTPUT_VLC = "Streambtw_VLC.m3u8"
OUTPUT_TIVIMATE = "Streambtw_TiviMate.m3u8"

TIMEOUT = 45000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

# -------------------------------------------------------------

def is_m3u8(url: str) -> bool:
    return url and ".m3u8" in url.lower()

def extract_m3u8_from_text(text: str) -> set[str]:
    found = set()
    for m in re.finditer(r'https?://[^\s"\']+\.m3u8[^\s"\']*', text):
        found.add(m.group(0))
    return found

def normalize_url(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("/"):
        return BASE + href
    if not href.startswith("http"):
        return BASE + "/" + href
    return href.replace("streambtw.com", "streambtw.live")

# -------------------------------------------------------------

async def fetch_events():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(user_agent=USER_AGENT)
        page = await ctx.new_page()

        try:
            await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=TIMEOUT)
        except PlaywrightTimeoutError:
            pass

        matches = await page.locator(".schedule .match").all()
        for m in matches:
            try:
                title = (await m.locator(".match-title").inner_text()).strip()
                href = await m.locator("a.watch-btn").get_attribute("href")
                href = normalize_url(href)
                if title and href:
                    events.append({"title": title, "url": href})
            except Exception:
                pass

        await browser.close()

    return events

# -------------------------------------------------------------

async def extract_streams(page, url: str) -> list[str]:
    streams = set()

    async def on_request(req):
        if is_m3u8(req.url):
            streams.add(req.url)

    async def on_response(res):
        try:
            ct = res.headers.get("content-type", "")
            if any(x in ct for x in ("json", "javascript", "text", "octet-stream")):
                body = await res.text()
                for m in extract_m3u8_from_text(body):
                    streams.add(m)
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    except Exception:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
        return []

    # allow JS + iframe boot
    await page.wait_for_timeout(4000)

    # CLICK EVERYTHING THAT CAN PLAY
    for frame in page.frames:
        try:
            for sel in (
                "video",
                "button",
                ".play",
                ".jw-icon-play",
                ".vjs-big-play-button",
                "[aria-label='Play']",
                "div"
            ):
                els = await frame.locator(sel).all()
                for el in els[:3]:
                    try:
                        await el.click(force=True, timeout=1500)
                        await page.wait_for_timeout(2500)
                    except Exception:
                        pass
        except Exception:
            pass

    # final capture window
    await page.wait_for_timeout(8000)

    page.remove_listener("request", on_request)
    page.remove_listener("response", on_response)

    return list(streams)

# -------------------------------------------------------------

async def main():
    events = await fetch_events()
    if not events:
        print("❌ No event links found.")
        return

    print(f"📌 Found {len(events)} events")

    collected = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720}
        )
        page = await ctx.new_page()

        for idx, ev in enumerate(events, 1):
            print(f"🔎 [{idx}/{len(events)}] {ev['title']}")
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

    # ---------------- VLC ----------------
    vlc = ["#EXTM3U"]
    for title, url in collected:
        vlc.append(f"#EXTINF:-1,{title}")
        vlc.append(url)

    Path(OUTPUT_VLC).write_text("\n".join(vlc), encoding="utf-8")

    # ---------------- TIVIMATE ----------------
    ua = quote(USER_AGENT)
    tm = ["#EXTM3U"]
    for title, url in collected:
        tm.append(f"#EXTINF:-1,{title}")
        tm.append(
            f"{url}|referer={BASE}/|origin={BASE}|user-agent={ua}"
        )

    Path(OUTPUT_TIVIMATE).write_text("\n".join(tm), encoding="utf-8")

    print(f"✅ Saved VLC playlist: {OUTPUT_VLC}")
    print(f"✅ Saved TiviMate playlist: {OUTPUT_TIVIMATE}")

# -------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
