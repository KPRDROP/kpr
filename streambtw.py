#!/usr/bin/env python3

import asyncio
import re
import base64
import sys
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

HOMEPAGE = "https://streambtw.com/"
OUTPUT_VLC = "Streambtw_VLC.m3u8"
TIMEOUT = 25000  # ms for navigations
CLICK_WAIT = 2.0  # seconds after clicks to allow requests

# Helper: collect m3u8s from a Playwright page by listening to responses and scanning HTML
def is_m3u8_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return ".m3u8" in u or "playlist" in u and u.endswith("m3u8") or u.endswith(".m3u8")

async def extract_encoded_from_html(html: str):
    """
    Try to find base64-like encoded strings used to build the m3u8 URL in obfuscated scripts.
    Looks for patterns like:
      var encoded = "aGVsbG8=";
    or reversed strings etc.
    Returns a list of candidate decoded URLs.
    """
    candidates = set()
    # Pattern: var encoded = "...."
    for m in re.finditer(r'var\s+encoded\s*=\s*"([^"]+)"', html, re.IGNORECASE):
        raw = m.group(1)
        # Try various decode attempts:
        for attempt in (raw, raw[::-1]):
            try:
                dec = base64.b64decode(attempt).decode(errors="ignore")
                # if still looks reversed in JS they might reverse again before atob, try reverse result
                if is_m3u8_url(dec):
                    candidates.add(dec)
                # maybe the JS reversed then atob on reversed(string) -> atob(reversed(raw)),
                # so reverse twice: attempt[::-1] etc
                r2 = dec[::-1]
                if is_m3u8_url(r2):
                    candidates.add(r2)
            except Exception:
                # ignore decode failures
                pass

    # Another pattern: atob("...") or atob(r.split("").reverse().join(""))
    for m in re.finditer(r'atob\(\s*"([^"]+)"\s*\)', html, re.IGNORECASE):
        raw = m.group(1)
        try:
            dec = base64.b64decode(raw).decode(errors="ignore")
            if is_m3u8_url(dec):
                candidates.add(dec)
        except Exception:
            pass

    # look for obvious base64 strings (long, only base64 chars) and try decode
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
    # The site lists .com links but the working host is .live
    href = href.strip()
    href = href.replace("streambtw.com", "streambtw.live")
    # accept trailing relative links
    if href.startswith("/"):
        return "https://streambtw.live" + href
    return href


async def fetch_iframe_pages():
    """Open homepage with Playwright and collect iframe links (the Watch Stream buttons)."""
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

        # gather anchors for Watch Stream buttons - common selectors
        anchors = []
        try:
            # try button links
            anchors += [await a.get_attribute("href") for a in await page.locator('a.btn, a.btn-primary, .card a.btn-primary, .card .btn').all() if await a.get_attribute("href")]
        except Exception:
            pass

        # fallback: any card link href
        try:
            anchors += [await a.get_attribute("href") for a in await page.locator('a').all() if await a.get_attribute("href") and "iframe" in (await a.get_attribute("href"))]
        except Exception:
            pass

        # dedupe and normalize
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
    """
    Open an iframe page, try to remove overlays, click play, intercept network responses, and
    extract m3u8 URL(s). Returns list of found m3u8s.
    """
    found = set()
    try:
        page = await context.new_page()
    except Exception:
        return []

    stream_urls = set()

    # response handler
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
        # try waiting for load slowly (some pages block DOMContent)
        try:
            await page.goto(url, wait_until="load", timeout=TIMEOUT)
        except Exception:
            pass
    except Exception:
        pass

    # small delay to allow initial requests
    await asyncio.sleep(1.2)

    # Try to remove common overlays/adblock overlays by hiding elements
    try:
        await page.add_style_tag(content="""
            #adblock-overlay, .overlay, .ads, .ad, .modal, .popup { display:none !important; pointer-events:none !important; }
            .play-button, .big-play-button { pointer-events: auto !important; }
        """)
    except Exception:
        pass

    # Try clicking typical play targets: '#player', '.play', 'video', '.big-play', etc.
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
                    # fallback: evaluate click via JS
                    try:
                        await page.evaluate("""(s)=>{const e=document.querySelector(s); if(e){ e.click(); return true } return false }""", sel)
                    except Exception:
                        pass
                # wait some time to allow network requests triggered by play
                await asyncio.sleep(0.8)
        except Exception:
            pass

    # After clicking, wait a bit to capture network responses
    await asyncio.sleep(CLICK_WAIT)

    # If no m3u8 in network, try to scan inline HTML/JS for encoded strings
    if not stream_urls:
        try:
            html = await page.content()
            candidates = await extract_encoded_from_html(html)
            for cand in candidates:
                stream_urls.add(cand)
        except Exception:
            pass

    # Some pages embed a nested iframe pointing to real player - try to find iframe src and visit it
    if not stream_urls:
        try:
            iframes = await page.locator("iframe").all()
            for i in iframes:
                try:
                    src = await i.get_attribute("src")
                    if not src:
                        continue
                    src = src.strip()
                    # normalize relative -> absolute
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://streambtw.live" + src
                    # visit nested iframe and attempt to capture m3u8
                    sub = await context.new_page()
                    sub.on("response", on_response)
                    try:
                        await sub.goto(src, wait_until="domcontentloaded", timeout=TIMEOUT)
                    except Exception:
                        pass
                    # try clicking inside nested
                    try:
                        await sub.locator("body").click(timeout=700, force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(CLICK_WAIT)
                    # inspect html of nested
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

        # ---- NEW PATCH ----
    # Fetch event names from homepage, build mapping {iframe_url: event_name}
    print("📌 Extracting event names from homepage for metadata...")
    event_titles = {}  # url → event title

    try:
        async with async_playwright() as p2:
            browser2 = await p2.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx2 = await browser2.new_context()
            pg2 = await ctx2.new_page()
            await pg2.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=25000)

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

    # ---- WRITE OUTPUT ----
    lines = ["#EXTM3U"]
    for src_page, m3u in all_streams:
        # Default fallback title (original behavior)
        fallback_title = src_page.rsplit("/", 1)[-1]

        # Try to find real event title
        real_title = event_titles.get(src_page, fallback_title)

        lines.append(f"#EXTINF:-1,{real_title}")
        lines.append(m3u)


    Path(OUTPUT_VLC).write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Captured {len(all_streams)} streams — saved to {OUTPUT_VLC}")


if __name__ == "__main__":
    asyncio.run(main())
