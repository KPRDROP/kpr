#!/usr/bin/env python3

import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

HOMEPAGE = "https://streambtw.com/"
OUTPUT_VLC = "Streambtw_VLC.m3u8"
OUTPUT_TIVIMATE = "Streambtw_TiviMate.m3u8"
TIMEOUT = 25000  # ms for navigations
CLICK_WAIT = 2.0  # seconds after clicks to allow requests

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

def is_m3u8_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return ".m3u8" in u or "playlist" in u and u.endswith("m3u8") or u.endswith(".m3u8")

async def extract_encoded_from_html(html: str):
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

    for m in re.finditer(r'["\']([A-Za-z0-9+/=]{32,200})["\']', html):
        raw = m.group(1)
        try:
            dec = base64.b64decode(raw).decode(errors="ignore")
            if is_m3u8_url(dec):
                candidates.add(dec)
        except Exception:
            pass

    return list(candidates)

async def normalize_href(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    href = href.replace("streambtw.com", "streambtw.live")
    if href.startswith("/"):
        return "https://streambtw.live" + href
    return href

async def fetch_iframe_pages():
    pages = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()
        print("🔍 Fetching StreamBTW homepage...")
        try:
            await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=TIMEOUT)
        except PlaywrightTimeoutError:
            print("⚠️ Homepage load timeout; continuing with available content.")
        except Exception as e:
            print("❌ Failed to open homepage:", e)
            await browser.close()
            return pages

        anchors = []
        try:
            anchors += [await a.get_attribute("href") for a in await page.locator('a.btn, a.btn-primary, .card a.btn-primary, .card .btn').all() if await a.get_attribute("href")]
        except Exception:
            pass

        try:
            anchors += [await a.get_attribute("href") for a in await page.locator('a').all() if await a.get_attribute("href") and "iframe" in (await a.get_attribute("href"))]
        except Exception:
            pass

        seen = set()
        for h in anchors:
            if not h:
                continue
            norm = await normalize_href(h)
            if norm and norm not in seen:
                seen.add(norm)
                pages.append(norm)

        await browser.close()
    return pages

async def attempt_extract_from_iframe(context, url: str):
    found = set()
    try:
        page = await context.new_page()
    except Exception:
        return []

    stream_urls = set()

    async def on_response(response):
        try:
            rurl = response.url
            if is_m3u8_url(rurl):
                stream_urls.add(rurl)
        except Exception:
            pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    except PlaywrightTimeoutError:
        try:
            await page.goto(url, wait_until="load", timeout=TIMEOUT)
        except Exception:
            pass
    except Exception:
        pass

    await asyncio.sleep(1.2)

    try:
        await page.add_style_tag(content="""
            #adblock-overlay, .overlay, .ads, .ad, .modal, .popup { display:none !important; pointer-events:none !important; }
            .play-button, .big-play-button { pointer-events: auto !important; }
        """)
    except Exception:
        pass

    click_selectors = [
        "#player", "video", ".play-button", ".big-play-button", ".big-play", ".play", ".jw-icon-play",
        ".vjs-big-play-button", ".plyr__control--play", ".clappr-container", ".playback", "body"
    ]
    for sel in click_selectors:
        try:
            el = page.locator(sel)
            if await el.count() > 0:
                try:
                    await el.first.click(timeout=1500)
                except Exception:
                    try:
                        await page.evaluate("""(s)=>{const e=document.querySelector(s); if(e){ e.click(); return true } return false }""", sel)
                    except Exception:
                        pass
                await asyncio.sleep(0.8)
        except Exception:
            pass

    await asyncio.sleep(CLICK_WAIT)

    if not stream_urls:
        try:
            html = await page.content()
            candidates = await extract_encoded_from_html(html)
            for cand in candidates:
                stream_urls.add(cand)
        except Exception:
            pass

    if not stream_urls:
        try:
            iframes = await page.locator("iframe").all()
            for i in iframes:
                try:
                    src = await i.get_attribute("src")
                    if not src:
                        continue
                    src = src.strip()
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://streambtw.live" + src
                    sub = await context.new_page()
                    sub.on("response", on_response)
                    try:
                        await sub.goto(src, wait_until="domcontentloaded", timeout=TIMEOUT)
                    except Exception:
                        pass
                    try:
                        await sub.locator("body").click(timeout=700, force=True)
                    except Exception:
                        pass
                    try:
                        nested_html = await sub.content()
                        cands = await extract_encoded_from_html(nested_html)
                        for c in cands:
                            stream_urls.add(c)
                    except Exception:
                        pass
                    try:
                        await sub.close()
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass

    try:
        await page.close()
    except Exception:
        pass

    return list(stream_urls)

async def main():
    iframe_pages = await fetch_iframe_pages()
    if not iframe_pages:
        print("📌 Found 0 iframe pages")
        print("❌ No streams captured.")
        return

    print(f"📌 Found {len(iframe_pages)} iframe pages")
    seen = set()
    iframe_pages = [p for p in iframe_pages if not (p in seen or seen.add(p))]

    found_map = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = await browser.new_context()

        for idx, page_url in enumerate(iframe_pages, start=1):
            print(f"🔎 [{idx}/{len(iframe_pages)}] Checking iframe: {page_url}")
            try:
                streams = await attempt_extract_from_iframe(context, page_url)
                if streams:
                    print(f"✅ Found {len(streams)} m3u8(s) for {page_url}")
                    for s in streams:
                        print("  →", s)
                    found_map[page_url] = streams
                else:
                    print(f"⚠️ No m3u8 found for {page_url}")
            except Exception as e:
                print(f"⚠️ Error while processing {page_url}: {e}")

        try:
            await browser.close()
        except Exception:
            pass

    all_streams = []
    for k, v in found_map.items():
        for s in v:
            all_streams.append((k, s))

    if not all_streams:
        print("❌ No streams captured from any iframe pages.")
        return

    # ---- Extract event titles for metadata ----
    print("📌 Extracting event names from homepage for metadata...")
    event_titles = {}
    try:
        async with async_playwright() as p2:
            browser2 = await p2.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx2 = await browser2.new_context()
            pg2 = await ctx2.new_page()
            await pg2.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=TIMEOUT)

            cards = pg2.locator(".card")
            count = await cards.count()
            for i in range(count):
                card = cards.nth(i)
                try:
                    href = await card.locator("a").first.get_attribute("href")
                    title = await card.locator(".card-text").inner_text()
                    if href and title:
                        href = href.replace("streambtw.com", "streambtw.live").strip()
                        event_titles[href] = title.strip()
                except:
                    pass

            await browser2.close()
    except:
        print("⚠️ Metadata extraction failed — continuing without titles")

    # ---- WRITE VLC OUTPUT ----
    lines_vlc = ["#EXTM3U"]
    for src_page, m3u in all_streams:
        fallback_title = src_page.rsplit("/", 1)[-1]
        real_title = event_titles.get(src_page, fallback_title)
        lines_vlc.append(f"#EXTINF:-1,{real_title}")
        lines_vlc.append(m3u)

    Path(OUTPUT_VLC).write_text("\n".join(lines_vlc), encoding="utf-8")
    print(f"✅ Captured {len(all_streams)} streams — saved to {OUTPUT_VLC}")

    # ---- WRITE TIVIMATE OUTPUT ----
    ua_encoded = quote(USER_AGENT)
    lines_tivimate = ["#EXTM3U"]
    for src_page, m3u in all_streams:
        fallback_title = src_page.rsplit("/", 1)[-1]
        real_title = event_titles.get(src_page, fallback_title)
        url_tivimate = f"{m3u}|referer=https://streambtw.live/|origin=https://streambtw.live|user-agent={ua_encoded}"
        lines_tivimate.append(f"#EXTINF:-1,{real_title}")
        lines_tivimate.append(url_tivimate)

    Path(OUTPUT_TIVIMATE).write_text("\n".join(lines_tivimate), encoding="utf-8")
    print(f"✅ Captured {len(all_streams)} streams — saved to {OUTPUT_TIVIMATE}")

if __name__ == "__main__":
    asyncio.run(main())
