#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote, urljoin
import aiohttp
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

HOMEPAGE = "https://streambtw.com/"
OUTPUT_VLC = "Streambtw_VLC.m3u8"
OUTPUT_TIVIMATE = "Streambtw_TiviMate.m3u8"
TIMEOUT = 25000
CLICK_WAIT = 2.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

def is_m3u8_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return ".m3u8" in u or ".pnp" in u or "playlist" in u

async def extract_encoded_from_html(html: str):
    """Decode base64-encoded m3u8 streams if present"""
    candidates = set()
    for m in re.finditer(r'var\s+encoded\s*=\s*"([^"]+)"', html, re.IGNORECASE):
        raw = m.group(1)
        for attempt in (raw, raw[::-1]):
            try:
                dec = base64.b64decode(attempt).decode(errors="ignore")
                if is_m3u8_url(dec):
                    candidates.add(dec)
                r2 = dec[::-1]
                if is_m3u8_url(r2):
                    candidates.add(r2)
            except Exception:
                pass
    for m in re.finditer(r'atob\(\s*"([^"]+)"\s*\)', html, re.IGNORECASE):
        raw = m.group(1)
        try:
            dec = base64.b64decode(raw).decode(errors="ignore")
            if is_m3u8_url(dec):
                candidates.add(dec)
        except Exception:
            pass
    return list(candidates)

async def normalize_href(href: str) -> str:
    """Ensure absolute URL for embed links"""
    if not href:
        return ""
    href = href.strip().replace("streambtw.com", "streambtw.live")
    if href.startswith("/"):
        return "https://streambtw.live" + href
    if not href.startswith("http"):
        return "https://streambtw.live/" + href
    return href

async def fetch_event_links():
    """Fetch all match links + titles from homepage"""
    events = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        try:
            await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=TIMEOUT)
        except PlaywrightTimeoutError:
            print("⚠️ Homepage load timeout")
        except Exception as e:
            print("❌ Failed to open homepage:", e)
            await browser.close()
            return events

        # --- FIX: iterate over each match div ---
        match_divs = await page.locator(".schedule .match").all()
        for div in match_divs:
            try:
                title_el = div.locator(".match-title")
                href_el = div.locator("a.watch-btn")
                if await title_el.count() == 0 or await href_el.count() == 0:
                    continue
                title = (await title_el.inner_text()).strip()
                href = await href_el.get_attribute("href")
                href = await normalize_href(href)
                if title and href:
                    events.append({"title": title, "url": href})
            except Exception:
                continue

        await browser.close()
    return events
    
async def resolve_pnp_to_m3u8(url: str, session: aiohttp.ClientSession) -> str | None:
    """If .pnp link, try to resolve final m3u8"""
    if url.endswith(".m3u8"):
        return url
    if url.endswith(".pnp"):
        try:
            async with session.get(url, headers={"User-Agent": USER_AGENT}, timeout=20) as r:
                text = await r.text()
                m = re.search(r'https?://[^\s"\'<>]+\.m3u8', text)
                if m:
                    return m.group(0)
        except Exception:
            return None
    return None

async def extract_m3u8_from_event(page, url):
    streams = set()

    async def on_request(request):
        req_url = request.url
        if ".m3u8" in req_url:
            streams.add(req_url)

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        return []

    # give JS time to build iframe
    await page.wait_for_timeout(3000)

    # --- HANDLE IFRAMES ---
    frames = page.frames
    for frame in frames:
        try:
            # try clicking common play buttons
            for selector in [
                "button",
                ".play",
                ".vjs-big-play-button",
                "#play",
                "[aria-label='Play']",
                "div"
            ]:
                btns = await frame.locator(selector).all()
                for btn in btns[:2]:
                    try:
                        await btn.click(force=True, timeout=2000)
                        await page.wait_for_timeout(5000)
                    except Exception:
                        pass
        except Exception:
            continue

    # final wait for stream
    await page.wait_for_timeout(6000)

    return list(streams)

async def main():
    events = await fetch_event_links()
    if not events:
        print("❌ No event links found.")
        return
    print(f"📌 Found {len(events)} events")

    all_streams = []
    async with async_playwright() as p:
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled"
        ]
    )
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 720}
    )
    page = await context.new_page()

    for idx, event in enumerate(events, 1):
        print(f"🔎 [{idx}/{len(events)}] Checking: {event['title']} -> {event['url']}")

        streams = await extract_m3u8_from_event(page, event["url"])

        if streams:
            for s in streams:
                print(f"  ✅ STREAM FOUND: {s}")
                playlist.append({
                    "title": event["title"],
                    "url": s
                })
        else:
            print(f"  ⚠️ No streams found for {event['title']}")

    await browser.close()

    # ---- WRITE VLC OUTPUT ----
    lines_vlc = ["#EXTM3U"]
    for title, m3u in all_streams:
        lines_vlc.append(f"#EXTINF:-1,{title}")
        lines_vlc.append(m3u)
    Path(OUTPUT_VLC).write_text("\n".join(lines_vlc), encoding="utf-8")
    print(f"✅ Saved VLC playlist: {OUTPUT_VLC}")

    # ---- WRITE TIVIMATE OUTPUT ----
    ua_encoded = quote(USER_AGENT)
    lines_tm = ["#EXTM3U"]
    for title, m3u in all_streams:
        url_tm = f"{m3u}|referer=https://streambtw.live/|origin=https://streambtw.live|user-agent={ua_encoded}"
        lines_tm.append(f"#EXTINF:-1,{title}")
        lines_tm.append(url_tm)
    Path(OUTPUT_TIVIMATE).write_text("\n".join(lines_tm), encoding="utf-8")
    print(f"✅ Saved TiviMate playlist: {OUTPUT_TIVIMATE}")

if __name__ == "__main__":
    asyncio.run(main())
