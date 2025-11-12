#!/usr/bin/env python3
"""
update_buff.py

Deep Playwright-based BuffStreams scraper (root-only).

- Visits https://buffstreams.plus/
- Captures network requests (including iframe requests)
- Tries clicking play controls inside frames and iframe pages
- Extracts .m3u8 and playlist/... URLs
- Writes two playlists:
    - BuffStreams_VLC.m3u8
    - BuffStreams_TiviMate.m3u8
"""

import asyncio
import re
import urllib.parse
from datetime import datetime
from typing import Set, List, Tuple
from playwright.async_api import async_playwright, Page, Frame, BrowserContext

BASE_URL = "https://buffstreams.plus/"
REFERER = BASE_URL
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
ENCODED_UA = urllib.parse.quote(USER_AGENT, safe="")

# Regexes to capture the different stream patterns we've seen
STREAM_REGEX = re.compile(
    r"(https?://[^\s\"'<>`]+\.(?:m3u8)(?:\?[^\"'\s<>]*)?|https?://[^\s\"'<>`]+/(?:playlist|load-playlist)[^\s\"'<>`]*)",
    re.IGNORECASE,
)

# Some pages embed stream wrappers like showPlayer('clappr', 'https:/...m3u8')
EMBEDDED_JS_RE = re.compile(r"(['\"])(https?:\/\/[^'\"]+?\.m3u8[^'\"]*)\1", re.IGNORECASE)

# Selectors that commonly start playback controls (try many)
PLAY_SELECTORS = [
    "button.jw-play", ".jw-icon-playback", ".jw-icon-display", ".jw-play", ".jw-button",
    ".vjs-big-play-button", "button.play", "div.play-button", ".play-btn", ".playBtn",
    ".plyr__control--play", ".plyr__play", "button[aria-label*='Play']", "button[title*='Play']",
    "video"
]

# Output files
VLC_OUTPUT = "BuffStreams_VLC.m3u8"
TIVIMATE_OUTPUT = "BuffStreams_TiviMate.m3u8"

# Default TV metadata (kept simple)
TVG_ID = "Sports.Dummy.us"
TVG_LOGO = "https://i.postimg.cc/qMm0rc3L/247.png"
GROUP_NAME = "BuffStreams"

# Timeouts and retries
NAV_TIMEOUT = 60000
EXTRA_WAIT = 6  # seconds after networkidle
CLICK_RETRIES = 3
FRAME_CLICK_DELAY = 0.6


async def try_click_in_frame(frame: Frame) -> bool:
    """Attempt to click common play selectors inside a frame. Return True if any click executed."""
    for sel in PLAY_SELECTORS:
        try:
            # try using frame.locator().first.click() if exists
            locator = frame.locator(sel)
            count = await locator.count()
            if count > 0:
                # click first visible control
                for i in range(count):
                    try:
                        el = locator.nth(i)
                        await el.click(timeout=3000)
                        await asyncio.sleep(FRAME_CLICK_DELAY)
                        return True
                    except Exception:
                        continue
            # try evaluate to click via DOM if selector matched but not clickable via API
            has = await frame.eval_on_selector_all(sel, "els => els.length").catch(lambda e: 0) if hasattr(frame, "eval_on_selector_all") else 0
        except Exception:
            # ignore selector errors
            pass
    # fallback: try clicking center of frame's viewport (works for canvas players)
    try:
        box = await frame.evaluate(
            """() => {
                const r = document.body.getBoundingClientRect();
                return {w: r.width||window.innerWidth, h: r.height||window.innerHeight};
            }"""
        )
        if box and isinstance(box, dict):
            w = box.get("w", 800)
            h = box.get("h", 600)
            try:
                await frame.mouse.click(int(w/2), int(h/2))
                await asyncio.sleep(FRAME_CLICK_DELAY)
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False


async def open_iframe_src_and_click(context: BrowserContext, iframe_src: str, found: Set[str]) -> None:
    """Open iframe src in a new page and attempt clicks / sniffing there."""
    try:
        page = await context.new_page()
        await page.goto(iframe_src, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
        # small wait for dynamic injection
        await asyncio.sleep(EXTRA_WAIT)
        # try clicking on page
        for _ in range(CLICK_RETRIES):
            clicked = False
            for sel in PLAY_SELECTORS:
                try:
                    loc = page.locator(sel)
                    if await loc.count() > 0:
                        try:
                            await loc.first.click(timeout=3000)
                            clicked = True
                            await asyncio.sleep(FRAME_CLICK_DELAY)
                        except Exception:
                            continue
                except Exception:
                    continue
            if clicked:
                await asyncio.sleep(1.0)
            else:
                # click center of viewport
                try:
                    width = await page.evaluate("() => window.innerWidth")
                    height = await page.evaluate("() => window.innerHeight")
                    await page.mouse.click(int(width/2), int(height/2))
                    await asyncio.sleep(FRAME_CLICK_DELAY)
                except Exception:
                    pass
        # check page content for matches
        html = await page.content()
        for m in STREAM_REGEX.findall(html):
            found.add(m if isinstance(m, str) else m[0])
        # also inspect requests already captured by context (requests are captured at context level)
    except Exception:
        pass
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def collect_streams() -> List[Tuple[str, str]]:
    """Main routine: open main page, sniff network, click players in frames & iframe pages."""
    found: Set[str] = set()

    async with async_playwright() as p:
        # Use chromium; add no-sandbox args for CI
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        ctx = await browser.new_context(user_agent=USER_AGENT, extra_http_headers={"Referer": REFERER})
        page = await ctx.new_page()

        # capture requests globally
        def on_request(req):
            try:
                url = req.url
                # check regex
                for m in STREAM_REGEX.findall(url):
                    if isinstance(m, tuple):
                        url_candidate = m[0]
                    else:
                        url_candidate = m
                    found.add(url_candidate)
            except Exception:
                pass

        ctx.on("request", on_request)

        # navigate main page
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
        except Exception:
            # try a reload if initial load failed
            try:
                await page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                await page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
            except Exception:
                pass

        # allow JS to inject players
        await asyncio.sleep(EXTRA_WAIT)

        # quick scan page HTML for stream links
        html = await page.content()
        for m in STREAM_REGEX.findall(html):
            found.add(m if isinstance(m, str) else m[0])
        for m in EMBEDDED_JS_RE.findall(html):
            # EMBEDDED_JS_RE returns tuples because of capture groups; second item is url
            if isinstance(m, tuple):
                url = m[1] if len(m) > 1 else m[0]
            else:
                url = m
            found.add(url)

        # iterate frames and try clicking play inside them
        frames = page.frames
        iframe_srcs = set()
        for frame in frames:
            try:
                # frame.content() may fail for cross-origin; we still try clicks via frame
                try:
                    frame_html = await frame.content()
                    # scan frame html for streams
                    for m in STREAM_REGEX.findall(frame_html):
                        found.add(m if isinstance(m, str) else m[0])
                    for m in EMBEDDED_JS_RE.findall(frame_html):
                        if isinstance(m, tuple):
                            url = m[1] if len(m) > 1 else m[0]
                        else:
                            url = m
                        found.add(url)
                except Exception:
                    frame_html = ""
                # if frame has src, collect it for fallback open
                try:
                    src = frame.url
                    if src and src != "about:blank":
                        iframe_srcs.add(src)
                except Exception:
                    pass

                # attempt click sequence in the frame
                clicked = False
                for _ in range(CLICK_RETRIES):
                    result = await try_click_in_frame(frame)
                    if result:
                        clicked = True
                        # give it time to spawn requests
                        await asyncio.sleep(1.0)
                    else:
                        break

                # after clicking, give time and re-scan requests / frame content
                await asyncio.sleep(1.0)
                try:
                    frame_html2 = await frame.content()
                    for m in STREAM_REGEX.findall(frame_html2):
                        found.add(m if isinstance(m, str) else m[0])
                except Exception:
                    pass

            except Exception:
                continue

        # Fallback: open iframe src pages directly and try clicking & scanning
        for src in list(iframe_srcs):
            # skip same-origin main page
            if not src or src.startswith("data:"):
                continue
            # attempt to open and sniff
            await open_iframe_src_and_click(ctx, src, found)

        # final attempt: open a new page to the main page and click center, then wait
        try:
            pg2 = await ctx.new_page()
            await pg2.goto(BASE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await pg2.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT)
            for _ in range(2):
                try:
                    w = await pg2.evaluate("() => window.innerWidth")
                    h = await pg2.evaluate("() => window.innerHeight")
                    await pg2.mouse.click(int(w/2), int(h/2))
                    await asyncio.sleep(1.0)
                except Exception:
                    break
            html2 = await pg2.content()
            for m in STREAM_REGEX.findall(html2):
                found.add(m if isinstance(m, str) else m[0])
            await pg2.close()
        except Exception:
            pass

        await browser.close()

    # normalize found set: keep only full http(s) urls and dedupe
    candidates = []
    for u in found:
        if not u:
            continue
        u = u.strip()
        # sometimes regex capture returns tuples — handle that
        if isinstance(u, tuple):
            u = u[0]
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("http"):
            # ensure no trailing JS junk like "');" or quotes
            u = re.sub(r"['\"\)\(;\s]+$", "", u)
            candidates.append(u)
    # dedupe preserving order
    seen = set()
    final = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        final.append(c)

    # Return as list of tuples (title, url) - title is filename piece
    results = []
    for url in final:
        title = url.split("/")[-1]
        title = re.sub(r"\?.*$", "", title)
        title = title.replace("-", " ").replace("_", " ").title()
        results.append((title, url))
    return results


def write_playlists(items: List[Tuple[str, str]]):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {ts}\n\n'

    # VLC (no pipe headers)
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for title, url in items:
            f.write(f'#EXTINF:-1 tvg-logo="{TVG_LOGO}" tvg-id="{TVG_ID}" group-title="{GROUP_NAME}",{title}\n')
            f.write(f'{url}\n\n')

    # TiviMate (pipe headers appended to the URL)
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(header)
        for title, url in items:
            f.write(f'#EXTINF:-1 tvg-logo="{TVG_LOGO}" tvg-id="{TVG_ID}" group-title="{GROUP_NAME}",{title}\n')
            f.write(f'{url}|referer={REFERER}|user-agent={ENCODED_UA}\n\n')


async def main():
    print("▶️ Starting BuffStreams playlist generation...")
    items = await collect_streams()
    if not items:
        print("⚠️ Found 0 potential streams.")
    else:
        print(f"✅ Found {len(items)} potential streams:")
        for t, u in items:
            print(f"  • {t} -> {u}")

    write_playlists(items)
    print(f"\n✅ Finished. Playlists written:\n - {VLC_OUTPUT}\n - {TIVIMATE_OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
