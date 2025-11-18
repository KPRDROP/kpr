#!/usr/bin/env python3
"""
nflwebcast.py
Robust, bounded scraper for https://nflwebcast.com that:
 - avoids hanging indefinitely
 - captures .m3u8 / .ts network requests (works even behind Cloudflare)
 - retries navigation sensibly
 - writes an M3U playlist file (even if empty)
"""

import asyncio
import sys
import time
import traceback
from urllib.parse import urljoin, urlparse
from typing import Optional, Set

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

START_URL = "https://nflwebcast.com/"
OUTPUT_FILE = "NFLWebcast.m3u8"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Timeouts and limits (tweakable)
GLOBAL_TIMEOUT = 180              # overall script timeout (seconds)
NAV_TIMEOUT = 15_000              # per navigation timeout (ms)
NETWORK_IDLE_WAIT = 3             # seconds to wait after last request
NAV_RETRIES = 3                   # retries for top-level pages
DEEP_SCAN_LIMIT = 10              # max number of deep links to visit
PAGE_CAPTURE_WAIT = 6             # seconds to wait after load to capture requests
CLICK_WAIT = 1.0                  # seconds between simulated clicks
MAX_REQUEST_CAPTURE_SECONDS = 12  # seconds to keep listening for requests after load

# Patterns we consider playable
PLAYABLE_SUFFIXES = (".m3u8", ".ts")
PLAYABLE_INCLUDES = (".m3u8", "/hls/", "/live/")

# Logging helpers
def log(*parts):
    print(" ".join(str(p) for p in parts), flush=True)

# Utility
def is_playable_url(url: str) -> bool:
    url = (url or "").lower()
    if any(s in url for s in PLAYABLE_INCLUDES):
        if any(url.endswith(s) or s in url for s in PLAYABLE_SUFFIXES):
            return True
        # m3u8 with params too
        if ".m3u8" in url:
            return True
    return False

# Wait until page content appears to look like real site (not Cloudflare intermediate)
async def looks_like_real_site(page) -> bool:
    try:
        html = await page.content()
        lower = (html or "").lower()
        if "cf-browser-verification" in lower or "just a moment" in lower or "checking your browser" in lower:
            return False
        # site-specific heuristic: main menu or 'sbl' path present
        if "sbl" in (await page.url):
            return True
        if "<article" in lower or "live stream" in lower or "hd" in lower:
            return True
        # default to True (don't block too aggressively)
        return True
    except Exception:
        return False

# robust navigation with retries
async def robust_goto(page, url: str, attempts: int = NAV_RETRIES) -> bool:
    for i in range(1, attempts + 1):
        try:
            log(f"➡️ Navigating to {url} (attempt {i}/{attempts})")
            await page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
            # quick check for Cloudflare or redirect loops
            ok = await looks_like_real_site(page)
            if ok:
                return True
            log("⏳ Page looks like challenge or not ready; will wait briefly then retry.")
            await asyncio.sleep(2)
        except PlaywrightTimeout:
            log(f"⚠️ Navigation timeout for {url} (attempt {i})")
        except Exception as e:
            log(f"⚠️ Navigation error for {url} (attempt {i}): {e}")
        await asyncio.sleep(1 + i)
    return False

# capture playable urls via network sniffing
async def capture_from_page(context, url: str, max_capture_seconds: int = MAX_REQUEST_CAPTURE_SECONDS) -> Set[str]:
    page = await context.new_page()
    captured = set()
    last_req_time = time.time()

    def on_request(req):
        nonlocal last_req_time
        u = req.url or ""
        last_req_time = time.time()
        if is_playable_url(u):
            captured.add(u)
            log("  🎯 Captured candidate:", u)

    context.on("request", on_request)
    try:
        ok = await robust_goto(page, url)
        if not ok:
            log("  ⚠️ Could not load", url)
            await asyncio.sleep(0.5)
            await page.close()
            context.remove_listener("request", on_request)
            return captured

        # attempt to trigger common play buttons and clicks
        try:
            await page.bring_to_front()
            # try a few common selectors
            for sel in ["button.play", "button[class*=play]", ".vjs-big-play-button", ".jw-icon-display", "div.play"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click(timeout=1000)
                        log("  👆 Clicked", sel)
                        await asyncio.sleep(CLICK_WAIT)
                except Exception:
                    pass
        except Exception:
            pass

        # wait and capture requests for a limited time
        start = time.time()
        while time.time() - start < max_capture_seconds:
            await asyncio.sleep(0.5)
            # short-circuit if we recently captured something
            if captured and (time.time() - last_req_time) > NETWORK_IDLE_WAIT:
                break

        # deep-scan: inspect iframes and follow a few links (bounded)
        try:
            frames = page.frames
            for f in frames:
                try:
                    frame_url = f.url or ""
                    if frame_url and frame_url != url and len(captured) < 3:
                        log("  ↳ Inspecting iframe:", frame_url)
                        # clicking inside iframe might be blocked; attempt simple capture
                        await asyncio.sleep(0.25)
                except Exception:
                    pass
        except Exception:
            pass

    finally:
        await page.close()
        context.remove_listener("request", on_request)

    return captured

# shallow link extraction (main page)
async def extract_event_links_from_homepage(context) -> Set[str]:
    page = await context.new_page()
    urls = set()
    try:
        ok = await robust_goto(page, START_URL)
        if not ok:
            await page.close()
            return urls

        # try to find anchors that look like event pages
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
                if "/live-stream" in href or "/live-stream-online" in href or "/live-stream-online-free" in href or "/watch-" in href or "/sbl/" in href:
                    urls.add(href)
                # also include event-like slugs under site domain
                if href.startswith(START_URL) and href.count("/") > 3 and "channel" not in href:
                    urls.add(href)
            except Exception:
                pass

        # some sites redirect to /sbl/ path; ensure that
        # also include the /sbl/ root
        urls.add(urljoin(START_URL, "sbl/"))

    except Exception as e:
        log("⚠️ Error extracting links:", e)
    finally:
        await page.close()
    return urls

# bounded deep-scan visiting extracted event pages
async def deep_scan_for_streams(context, event_links, max_visit=DEEP_SCAN_LIMIT):
    found = set()
    count = 0
    for link in event_links:
        if count >= max_visit:
            break
        try:
            log(f"🔎 Deep-scan visiting: {link}")
            cands = await capture_from_page(context, link)
            for u in cands:
                found.add(u)
            count += 1
        except Exception as e:
            log("  ⚠️ scan error:", e)
    return found

# top-level orchestrator with overall timeout guard
async def main():
    start_ts = time.time()
    log("🚀 Starting NFLWebcast scraper (bounded, network-sniff mode)")

    # overall timeout guard
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = await browser.new_context(user_agent=USER_AGENT)
            try:
                # 1) Extract candidate event links from homepage
                log("🔍 Extracting event links from homepage...")
                event_links = await extract_event_links_from_homepage(context)
                log("📌 Found", len(event_links), "candidate links")

                # 2) First attempt capture directly on homepage (some streams load inline)
                homepage_captures = await capture_from_page(context, START_URL, max_capture_seconds=PAGE_CAPTURE_WAIT + 6)
                log("📥 Homepage captures:", len(homepage_captures))

                # 3) Deep-scan event links (bounded)
                deep_found = await deep_scan_for_streams(context, list(event_links), max_visit=DEEP_SCAN_LIMIT)
                log("📥 Deep-scan found:", len(deep_found))

                # Combine and validate (dedupe)
                all_candidates = set(homepage_captures) | set(deep_found)
                filtered = set(u for u in all_candidates if is_playable_url(u))
                log("🧾 Total playable candidates:", len(filtered))

                # Write playlist (pipe-style TiviMate entries are easily supported by adding headers)
                with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
                    fh.write("#EXTM3U\n")
                    if not filtered:
                        fh.write("# no streams found\n")
                    for u in sorted(filtered):
                        fh.write('#EXTINF:-1,Live\n')
                        # add basic headers as comments; user can modify to pipe headers if needed
                        fh.write(u + "\n")

                elapsed = time.time() - start_ts
                log("✅ Done — wrote", OUTPUT_FILE, "| streams:", len(filtered), "| time:", f"{elapsed:.1f}s")

            finally:
                await context.close()
                await browser.close()

    except Exception as exc:
        log("❌ Fatal error:", exc)
        traceback.print_exc()
        # ensure we create an empty playlist so CI doesn't break
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n# fatal error occurred\n")
        except Exception:
            pass
        # exit non-zero so CI can signal failure
        sys.exit(1)

if __name__ == "__main__":
    # enforce overall global timeout to prevent indefinite hanging
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=GLOBAL_TIMEOUT))
    except asyncio.TimeoutError:
        log("❌ Global timeout reached — aborting.")
        # ensure a playlist file exists
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n# timeout\n")
        except Exception:
            pass
        sys.exit(2)
