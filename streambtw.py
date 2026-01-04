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

        matches = await page.locator(".schedule .match").all()
        for m in matches:
            try:
                title_el = await m.locator(".match-title").first
                a_el = await m.locator("a.watch-btn").first
                if not title_el or not a_el:
                    continue
                title = (await title_el.inner_text()).strip()
                href = await a_el.get_attribute("href")
                href = await normalize_href(href)
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

async def extract_stream_from_embed(context, url: str):
    """Visit embed page, extract m3u8 or .pnp streams"""
    streams = set()
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        try:
            page = await context.new_page()
        except Exception:
            return []

        async def on_response(resp):
            rurl = resp.url
            if is_m3u8_url(rurl):
                streams.add(rurl)

        page.on("response", on_response)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
        except Exception:
            pass

        await asyncio.sleep(1.5)

        # try clicking common play buttons
        click_selectors = [
            "#player", "video", ".play-button", ".big-play-button", ".big-play", ".play",
            ".jw-icon-play", ".vjs-big-play-button", ".plyr__control--play", ".clappr-container"
        ]
        for sel in click_selectors:
            try:
                el = page.locator(sel)
                if await el.count() > 0:
                    try:
                        await el.first.click(timeout=1500)
                    except Exception:
                        await page.evaluate("""(s)=>{const e=document.querySelector(s); if(e){ e.click(); } }""", sel)
                    await asyncio.sleep(0.6)
            except Exception:
                continue

        await asyncio.sleep(CLICK_WAIT)

        # try extract from HTML base64
        html = await page.content()
        candidates = await extract_encoded_from_html(html)
        for c in candidates:
            streams.add(c)

        # resolve any .pnp links
        final_streams = set()
        for s in streams:
            resolved = await resolve_pnp_to_m3u8(s, session)
            if resolved:
                final_streams.add(resolved)

        try:
            await page.close()
        except Exception:
            pass

    return list(final_streams)

async def main():
    events = await fetch_event_links()
    if not events:
        print("❌ No event links found.")
        return
    print(f"📌 Found {len(events)} events")

    all_streams = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(user_agent=USER_AGENT)
        for idx, e in enumerate(events, start=1):
            print(f"🔎 [{idx}/{len(events)}] Checking: {e['title']} -> {e['url']}")
            try:
                streams = await extract_stream_from_embed(context, e["url"])
                if streams:
                    for s in streams:
                        all_streams.append((e["title"], s))
                        print(f"  ✅ Found stream: {s}")
                else:
                    print(f"  ⚠️ No streams found for {e['title']}")
            except Exception as ex:
                print(f"  ⚠️ Error: {ex}")
        await browser.close()

    if not all_streams:
        print("❌ No streams captured.")
        return

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
