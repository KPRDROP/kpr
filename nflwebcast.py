#!/usr/bin/env python3
"""
nflwebcast.py
Robust scraper for https://nflwebcast.com that:
 - finds event pages (including /sbl/ listing),
 - visits event pages, sniffs network requests for playable .m3u8/.ts,
 - validates m3u8 URLs with aiohttp,
 - writes a TiviMate-style playlist with pipe headers:
     url|referer=https://nflwebcast.com/|origin=https://nflwebcast.com|user-agent=<encoded UA>
"""

import asyncio
import sys
import time
import traceback
import aiohttp
from urllib.parse import urljoin, quote, urlparse
from typing import Set, Optional, List
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

START_URL = "https://nflwebcast.com/"
OUTPUT_FILE = "NFLWebcast.m3u8"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Tunables
GLOBAL_TIMEOUT = 240               # overall script timeout (s)
NAV_TIMEOUT_MS = 20_000            # per navigation timeout (ms)
NAV_RETRIES = 3
PAGE_CAPTURE_SECONDS = 10          # seconds to capture network requests after load
DEEP_SCAN_LIMIT = 10               # max event pages to visit
VALIDATE_TIMEOUT = 10              # aiohttp validation timeout (s)
CLICK_WAIT = 0.8                   # wait after simulated clicks (s)

# Playable heuristics
PLAYABLE_MARK = (".m3u8", ".ts", "/hls/", "/live/")

def log(*parts):
    print(" ".join(str(p) for p in parts), flush=True)

def is_playable_url(u: str) -> bool:
    if not u:
        return False
    low = u.lower()
    return any(mark in low for mark in PLAYABLE_MARK)

async def validate_m3u8(url: str, referer: str, origin: str, session: aiohttp.ClientSession) -> bool:
    """Check that URL returns 200 and looks like a playable m3u8 or media segment."""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Origin": origin,
    }
    try:
        async with session.get(url, headers=headers, timeout=VALIDATE_TIMEOUT, allow_redirects=True) as resp:
            if resp.status != 200:
                log(f"    ✖ validation failed status={resp.status} for {url}")
                return False
            ctype = (resp.headers.get("Content-Type") or "").lower()
            text_preview = ""
            # read small portion to detect .m3u8 file contents
            try:
                chunk = await resp.content.read(1024)
                text_preview = chunk.decode(errors="ignore").lower()
            except Exception:
                pass
            if ".m3u8" in url or "application/vnd.apple.mpegurl" in ctype or "#extm3u" in text_preview or "#extinf" in text_preview:
                log(f"    ✔ validated (200 + m3u8-like) {url}")
                return True
            # ts segment or other media: acceptable if content-type video or octet-stream
            if "video" in ctype or "octet-stream" in ctype:
                log(f"    ✔ validated (media) {url}")
                return True
            # fallback: accept 200 for known hosts with /hls/ etc
            if "/hls/" in url or "/live/" in url:
                log(f"    ✔ validated (heuristic) {url}")
                return True
            log(f"    ✖ validation ambiguous for {url} (ctype={ctype})")
            return False
    except Exception as e:
        log(f"    ✖ validation exception for {url}: {e}")
        return False

async def robust_goto(page, url: str, attempts: int = NAV_RETRIES) -> bool:
    """Navigate with retries and small waits; returns True when page looks loaded."""
    for i in range(1, attempts + 1):
        try:
            log(f"  → goto {url} (attempt {i}/{attempts})")
            await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            # short wait for JS to run a bit
            await asyncio.sleep(1.0)
            # heuristics: if Cloudflare challenge text appears, treat as not-ready
            content = (await page.content()).lower()
            if any(x in content for x in ("cf-browser-verification", "checking your browser", "just a moment")):
                log("    ⏳ Cloudflare/challenge detected; retrying after short sleep")
                await asyncio.sleep(2 + i)
                continue
            return True
        except PlaywrightTimeout:
            log("    ⚠ playwright navigation timeout")
        except Exception as e:
            log("    ⚠ goto error:", e)
        await asyncio.sleep(1 + i)
    return False

async def sniff_requests_on_page(context, page_url: str, session: aiohttp.ClientSession, capture_seconds: int = PAGE_CAPTURE_SECONDS) -> Set[str]:
    """Open page, attach page.on('request') and collect playable URLs during capture window."""
    page = await context.new_page()
    captured: Set[str] = set()
    handler_added = False

    def on_request(req):
        try:
            u = req.url
            if is_playable_url(u):
                if u not in captured:
                    captured.add(u)
                    log("    ▶ sniffed candidate:", u)
        except Exception:
            pass

    try:
        page.on("request", on_request)
        handler_added = True

        ok = await robust_goto(page, page_url)
        if not ok:
            log("    ✖ failed to load:", page_url)
            return captured

        # try some clicks to trigger players
        for sel in ["button.play", ".vjs-big-play-button", ".jw-icon-display", "button[class*=play]", "div[class*=play]"]:
            try:
                el = await page.query_selector(sel)
                if el:
                    try:
                        await el.click(timeout=1000)
                        log("    👆 clicked", sel)
                        await asyncio.sleep(CLICK_WAIT)
                    except Exception:
                        pass
            except Exception:
                pass

        # Wait for network activity; break early if we captured and network quiet
        start = time.time()
        last_add = time.time()
        while time.time() - start < capture_seconds:
            await asyncio.sleep(0.5)
            # small optimization: if captured and no requests for 2s, break
            if captured and (time.time() - last_add) > 2:
                break

        # Additionally inspect iframes (visit iframe srcs bounded)
        try:
            frames = page.frames
            for f in frames:
                try:
                    fu = f.url
                    if fu and fu != page_url and fu not in captured:
                        # try a quick fetch on iframe url (it might be embed page)
                        if is_playable_url(fu):
                            captured.add(fu)
                            log("    ▶ iframe direct candidate:", fu)
                except Exception:
                    pass
        except Exception:
            pass

    finally:
        try:
            if handler_added:
                # remove listener gracefully; Playwright may throw if already removed
                try:
                    page.remove_listener("request", on_request)
                except Exception:
                    # fallback: ignore
                    pass
            await page.close()
        except Exception:
            pass

    return captured

async def extract_event_links(context) -> List[str]:
    """Fetch START_URL and extract event links (works with sbl listing and dracula classes)."""
    page = await context.new_page()
    found: Set[str] = set()
    try:
        ok = await robust_goto(page, START_URL)
        if not ok:
            # try /sbl/ explicitly
            sbl = urljoin(START_URL, "sbl/")
            log("  trying listing path", sbl)
            ok2 = await robust_goto(page, sbl)
            if not ok2:
                log("  ✖ couldn't load homepage or /sbl/ listing")
                await page.close()
                return []

        # find anchors that match event patterns
        anchors = await page.query_selector_all("a[href]")
        for a in anchors:
            try:
                href = await a.get_attribute("href") or ""
                if not href:
                    continue
                # normalize
                parsed = urlparse(href)
                if not parsed.netloc:
                    href = urljoin(START_URL, href)
                # specific heuristics from the site: pages with long slugs and 'live-stream' / 'live-stream-online' or team links
                if any(x in href for x in ("/live-stream", "live-stream-online", "/watch-", "/sbl/", "/houston-texans") ) or href.startswith(START_URL) and href.count("/") > 3:
                    found.add(href)
                # also match dracula-style link anchors indicated in your paste
                text = (await a.inner_text() or "").lower()
                if "@" in text and "november" in text or "december" in text or "live stream" in text:
                    found.add(href)
            except Exception:
                pass

        # fallback: add /sbl/ explicitly
        found.add(urljoin(START_URL, "sbl/"))

    finally:
        await page.close()

    # return deduped list
    return list(found)

async def main():
    start = time.time()
    log("🚀 Starting NFLWebcast scraper (Playwright sniff + aiohttp validation)")

    found_valid: Set[str] = set()

    try:
        async with async_playwright() as pw, aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = await browser.new_context(user_agent=USER_AGENT)

            try:
                # 1) extract candidate event URLs
                event_urls = await extract_event_links(context)
                log("  ℹ candidate event links:", len(event_urls))
                if event_urls:
                    # cap deep-scan
                    to_visit = event_urls[:DEEP_SCAN_LIMIT]
                else:
                    to_visit = [urljoin(START_URL, "sbl/")]

                # 2) sniff homepage quickly (some sites load inline players)
                homepage_candidates = await sniff_requests_on_page(context, START_URL, session, capture_seconds=6)
                log("  ℹ homepage sniffed candidates:", len(homepage_candidates))

                # 3) deep-scan event pages
                total_visited = 0
                for ev in to_visit:
                    if total_visited >= DEEP_SCAN_LIMIT:
                        break
                    total_visited += 1
                    log(f"  ↳ deep visiting ({total_visited}/{len(to_visit)}): {ev}")
                    cands = await sniff_requests_on_page(context, ev, session, capture_seconds=PAGE_CAPTURE_SECONDS)
                    log("    → sniffed:", len(cands))
                    for c in cands:
                        if c not in found_valid:
                            # validate candidate
                            if await validate_m3u8(c, referer=START_URL, origin=START_URL, session=session):
                                found_valid.add(c)
                            else:
                                log("      ✖ candidate failed validation:", c)

                # also validate homepage candidates
                for c in homepage_candidates:
                    if c not in found_valid:
                        if await validate_m3u8(c, referer=START_URL, origin=START_URL, session=session):
                            found_valid.add(c)

            finally:
                await context.close()
                await browser.close()

    except Exception as e:
        log("❌ Fatal error during scraping:", e)
        traceback.print_exc()
        # create a minimal playlist to avoid missing-file downstream
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n# error during scraping\n")
        except Exception:
            pass
        sys.exit(1)

    # Write playlist (TiviMate pipe-style headers)
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
            fh.write("#EXTM3U\n")
            if not found_valid:
                fh.write("# no streams validated\n")
            for url in sorted(found_valid):
                # build tivimate header
                ua_enc = quote(USER_AGENT, safe="")
                headers = f"referer={START_URL}|origin={START_URL}|user-agent={ua_enc}"
                fh.write(f'#EXTINF:-1,Live\n')
                fh.write(f'{url}|{headers}\n')
        log("✅ Playlist written:", OUTPUT_FILE, "| streams:", len(found_valid), "| time:", f"{time.time()-start:.1f}s")
    except Exception as e:
        log("❌ Failed to write playlist:", e)
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=GLOBAL_TIMEOUT))
    except asyncio.TimeoutError:
        log("❌ Global timeout reached; writing empty playlist and exiting.")
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n# timeout\n")
        except Exception:
            pass
        sys.exit(2)
