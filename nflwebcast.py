#!/usr/bin/env python3
import asyncio
import sys
import re
import time
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright
import aiohttp

START_URL = "https://nflwebcast.com/"
LISTING_URL = "https://nflwebcast.com/sbl/"
OUTPUT_FILE = "NFLWebcast.m3u8"

MAX_NAV_RETRIES = 4
NAV_TIMEOUT_MS = 30000
DEEP_SCAN = True
VALIDATION_TIMEOUT = 10

# stealth JS to reduce automation fingerprints
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
const _origPerm = navigator.permissions && navigator.permissions.query;
if (_origPerm) {
  navigator.permissions.query = (p) => (p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : _origPerm(p));
}
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
"""

M3U8_RE = re.compile(r"https?://[^\s\"']+?\.m3u8(?:\?[^\s\"']*)?", re.I)
EVENT_HREF_PATTERNS = [
    re.compile(r"/[a-z0-9-]+-live-stream", re.I),                # typical slug pattern
    re.compile(r"/[a-z0-9-]+live-stream", re.I),
    re.compile(r"houston-texans-live-stream", re.I),             # example explicit
]


def clean_url(u: str) -> str:
    if not u:
        return ""
    return u.split("#", 1)[0].strip()


async def looks_like_challenge(page):
    html = await page.content()
    low = html.lower()
    if "cf-browser-verification" in low or "just a moment" in html or "cf-challenge" in low:
        return True
    return False


async def safe_goto(page, url: str) -> bool:
    for attempt in range(1, MAX_NAV_RETRIES + 1):
        try:
            print(f"→ goto {url} (attempt {attempt}/{MAX_NAV_RETRIES})")
            resp = await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            # small pause so client-side scripts can run
            await asyncio.sleep(1.25)
            if await looks_like_challenge(page):
                print("   ⏳ Cloudflare challenge detected; sleeping and retrying...")
                await asyncio.sleep(3 + attempt * 2)
                continue
            # wait a bit for dynamic links to appear
            try:
                await page.wait_for_load_state("networkidle", timeout=2500)
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"   ⚠ navigation error: {e}; retrying shortly")
            await asyncio.sleep(1 + attempt)
    print(f"   ✖ failed navigation: {url}")
    return False


async def extract_event_links_from_page(page, base_url: str):
    """Primary extraction: wait for known selectors then gather hrefs."""
    found = set()

    # first try: wait for the specific dracula link selector (the site uses this)
    selectors_to_try = [
        "a.dracula-style-link",
        "a.dracula-style-txt-border",
        "a.team",                     # team link
        "a[href*='live-stream']"      # direct href containing live-stream
    ]

    for sel in selectors_to_try:
        try:
            # try waiting a short time for this selector to appear
            await page.wait_for_selector(sel, timeout=2500)
            anchors = page.locator(sel)
            count = await anchors.count()
            for i in range(count):
                href = await anchors.nth(i).get_attribute("href") or ""
                href = clean_url(urljoin(base_url, href))
                if href and href.startswith("http"):
                    found.add(href)
            if found:
                print(f"   ✔ collected {len(found)} links from selector '{sel}'")
                return sorted(found)
        except Exception:
            # not found quickly — continue to next selector
            continue

    # fallback #1: gather all anchor hrefs and filter by pattern (safer)
    try:
        anchors = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        for a in anchors:
            if not a:
                continue
            a_clean = clean_url(urljoin(base_url, a))
            if not a_clean.startswith("http"):
                continue
            # keep only links that belong to the same host (nflwebcast) or match pattern
            if START_URL in a_clean or any(p.search(a_clean) for p in EVENT_HREF_PATTERNS):
                found.add(a_clean)
    except Exception:
        pass

    # fallback #2: scan HTML for common slug patterns directly
    if not found:
        try:
            html = await page.content()
            for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
                href = clean_url(m.group(1))
                if not href:
                    continue
                full = urljoin(base_url, href)
                if START_URL in full or any(p.search(full) for p in EVENT_HREF_PATTERNS):
                    found.add(full)
        except Exception:
            pass

    return sorted(found)


async def extract_m3u8_candidates_from_html(page):
    html = await page.content()
    matches = M3U8_RE.findall(html)
    return [clean_url(m) for m in matches]


async def validate_m3u8(url: str) -> bool:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
        "Referer": START_URL,
        "Origin": START_URL,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=VALIDATION_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.get(url, allow_redirects=True) as r:
                ok = r.status == 200
                print(f"   🔎 validate {url} -> {r.status}")
                return ok
    except Exception as e:
        print(f"   ⚠ validation error for {url}: {e}")
        return False


async def scan_event_page_for_m3u8(context, url: str):
    page = await context.new_page()
    page.add_init_script(STEALTH_JS)
    try:
        ok = await safe_goto(page, url)
        if not ok:
            return []

        # try clicking a likely play button if present (best-effort)
        try:
            # common play button selectors
            for ps in ["button.play", ".vjs-big-play-button", ".jw-icon-display", "button"]:
                try:
                    el = await page.query_selector(ps)
                    if el:
                        await el.click(timeout=1200)
                        await asyncio.sleep(1.0)
                except Exception:
                    pass
        except Exception:
            pass

        # collect m3u8 from HTML
        m3u8s = await extract_m3u8_candidates_from_html(page)
        return list(dict.fromkeys(m3u8s))  # unique preserving order
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def write_playlist(validated_urls):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, u in enumerate(validated_urls, 1):
            f.write(f'#EXTINF:-1 tvg-id="NFL.{i}" tvg-name="Stream {i}" tvg-logo="" group-title="NFL",Stream {i}\n')
            # TiviMate pipe headers
            headers = f"referer={START_URL}|origin={START_URL}|user-agent=Mozilla%2F5.0"
            f.write(f"{u}|{headers}\n")
    print(f"✅ Playlist written: {OUTPUT_FILE} | streams: {len(validated_urls)}")


async def main():
    print("🚀 Starting NFLWebcast scraper — improved event-link detection")
    async with async_playwright() as pw:
        # use a "real" chrome if available (channel=chrome), headful to help CF
        browser = await pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"),
            viewport={"width": 1280, "height": 900},
        )
        # add stealth before pages are created
        context.add_init_script(STEALTH_JS)

        # Try homepage + listing page to collect candidate event links
        candidate_links = []
        page = await context.new_page()
        page.add_init_script(STEALTH_JS)

        # prefer listing page, but try homepage too
        for target in (LISTING_URL, START_URL):
            ok = await safe_goto(page, target)
            if not ok:
                print(f" ✖ couldn't load {target} reliably (CF?)")
                continue
            try:
                # try to extract dracula/event links
                links = await extract_event_links_from_page(page, target)
                if links:
                    print(f" 🔍 Found {len(links)} event links on {target}")
                    for l in links:
                        if l not in candidate_links:
                            candidate_links.append(l)
                else:
                    print(f" ℹ no event links found quickly on {target}")
            except Exception as e:
                print(f" ⚠ extraction error on {target}: {e}")

        # optionally deep-scan homepage anchors
        if DEEP_SCAN and page:
            try:
                deep_links = await extract_event_links_from_page(page, START_URL)
                for l in deep_links:
                    if l not in candidate_links:
                        candidate_links.append(l)
            except Exception:
                pass

        await page.close()

        # Last-resort: if no candidates, try to perform a targeted HTML search on listing URL via fetch (no-js)
        if not candidate_links:
            try:
                print(" 🔎 Fallback: fetch listing HTML and search for event slugs (no-JS)")
                import requests
                r = requests.get(LISTING_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                if r.status_code == 200:
                    for m in re.finditer(r'href=["\']([^"\']+)["\']', r.text, re.I):
                        href = clean_url(m.group(1))
                        full = urljoin(LISTING_URL, href)
                        if START_URL in full or any(p.search(full) for p in EVENT_HREF_PATTERNS):
                            if full not in candidate_links:
                                candidate_links.append(full)
                    print(f"   fallback found {len(candidate_links)} candidates")
            except Exception as e:
                print(f"   fallback fetch failed: {e}")

        # dedupe preserve order
        seen = set()
        candidate_links = [x for x in candidate_links if not (x in seen or seen.add(x))]

        print(f"🔍 Total candidate event links: {len(candidate_links)}")

        # scan each event page for m3u8 candidates
        m3u8_candidates = []
        for ev in candidate_links:
            try:
                cands = await scan_event_page_for_m3u8(context, ev)
                for c in cands:
                    if c not in m3u8_candidates:
                        m3u8_candidates.append(c)
            except Exception as e:
                print(f" ⚠ error scanning {ev}: {e}")

        print(f"🎯 Raw m3u8 candidates found: {len(m3u8_candidates)}")

        # Validate candidates with aiohttp
        validated = []
        for u in m3u8_candidates:
            try:
                ok = await validate_m3u8(u)
                if ok:
                    validated.append(u)
            except Exception as e:
                print(f"   ⚠ validation exception for {u}: {e}")

        await context.close()
        await browser.close()

    # write playlist (even if empty)
    await write_playlist(validated)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted.")
        sys.exit(0)
