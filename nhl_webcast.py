#!/usr/bin/env python3

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, quote_plus
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"
)

BASE = "https://slapstreams.com/"
OUTPUT_VLC = "NHLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NHLWebcast_TiviMate.m3u8"
HEADERS = {
    "referer": BASE,
    "origin": BASE,
    "user-agent": USER_AGENT
}

VLC_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/026/046/225/small/hockey-character-mascot-logo-design-vector.jpg"

# ---- PATCHED FUNCTION ----
def clean_event_title(title: str) -> str:
    """Clean only the event title: replace '@' with 'vs' and remove commas."""
    if not title:
        return "NHL Game"

    t = title.strip()

    # Replace '@' → 'vs'
    t = t.replace("@", "vs")

    # Remove all commas
    t = t.replace(",", "")

    # Clean double spaces
    t = re.sub(r"\s{2,}", " ", t).strip()

    return t
# --------------------------

# ------ Helpers ------

def log(*a, **kw):
    print(*a, **kw)
    sys.stdout.flush()


def clean_title(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    parts = [p.strip() for p in raw.split("|")]
    if parts:
        return parts[0]
    return raw


def find_event_links_from_homepage(html: str, base: str = BASE) -> list:
    soup = BeautifulSoup(html, "lxml")
    links = []

    for a in soup.select(".card .card-body a, .card a.btn, .card a"):
        href = a.get("href")
        if not href:
            continue
        href = urljoin(base, href)
        text = a.text.strip() or ""
        parent = a.find_parent(class_="card-body")
        if parent:
            p = parent.find("p", class_="card-text")
            if p and p.text.strip():
                text = p.text.strip()
        links.append((href, text))

    if not links:
        for a in soup.find_all("a", href=True):
            href = urljoin(base, a["href"])
            if href.startswith(base):
                text = (a.text or "").strip()
                links.append((href, text))

    if not links:
        for m in re.finditer(r'https?://slapstreams\.com/[-\w/]+', html):
            href = m.group(0)
            links.append((href, ""))

    # Deduplicate while preserving order
    seen = set()
    out = []
    for href, text in links:
        if href in seen:
            if not [t for (h, t) in out if h == href][0] and text:
                out = [(h, text if h == href else t) for (h, t) in out]
            continue
        seen.add(href)
        out.append((href, text))
    return out


def guess_title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return clean_title(og["content"])
    t = soup.find("title")
    if t and t.text:
        return clean_title(t.text)
    h1 = soup.find("h1")
    if h1 and h1.text:
        return clean_title(h1.text)
    return ""


# ------ Playwright capture logic ------

async def capture_m3u8_from_page(playwright, url, timeout_ms=25000):
    browser = await playwright.firefox.launch(headless=True, args=["--no-sandbox"])
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()
    captured = None
    page_title_html = None

    def resp_handler(resp):
        nonlocal captured
        try:
            rurl = resp.url
            if rurl and ".m3u8" in rurl:
                if rurl.endswith(".m3u8") or "/playlist/" in rurl or "playlist" in rurl:
                    if not captured:
                        captured = rurl
        except Exception:
            pass

    try:
        page.on("response", resp_handler)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            log(f"⚠️ Timeout loading {url} -- continuing to capture network events")
        except Exception as e:
            log(f"⚠️ Error navigating {url}: {e}")

        content = await page.content()
        page_title_html = content

        b64_candidates = set(re.findall(r'["\']([A-Za-z0-9+/=]{40,200})["\']', content))
        for c in b64_candidates:
            try:
                import base64
                dec = base64.b64decode(c).decode(errors="ignore")
                if ".m3u8" in dec and not captured:
                    captured = dec.strip()
                    log("🔎 Found candidate from base64 in page content")
                    break
            except Exception:
                continue

        try:
            for sel in ["#player", ".player", ".play-button", ".play", "video", "body"]:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0:
                        await el.first.click(timeout=1200, force=True)
                        await asyncio.sleep(1.0)
                except Exception:
                    pass
        except Exception:
            pass

        total_wait = 0.0
        max_wait = 8.0
        while total_wait < max_wait and not captured:
            await asyncio.sleep(0.6)
            total_wait += 0.6

        if not captured:
            m = re.search(r'https?://[^\s"\'<>]+\.(?:m3u8)(?:\?[^\s"\'<>]*)?', content)
            if m:
                captured = m.group(0)

        if not captured:
            rev_pattern = re.search(r'encoded\s*=\s*["\']([A-Za-z0-9+/=]+)["\']', content)
            if rev_pattern:
                try:
                    import base64
                    candidate = rev_pattern.group(1)
                    try_dec = base64.b64decode(candidate).decode(errors="ignore")
                    if ".m3u8" in try_dec:
                        captured = try_dec
                except Exception:
                    pass

    finally:
        try:
            await page.close()
        except Exception:
            pass
        try:
            await context.close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass

    return captured, page_title_html


# ------ Main orchestration ------

def write_playlists(entries):
    """
    entries: list of tuples (title, url)
    Writes two files:
     - NHLWebcast_VLC.m3u8
     - NHLWebcast_TiviMate.m3u8
    """
    # VLC
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="NHL.Hockey.Dummy.us" '
                f'tvg-name="NHL" tvg-logo="{VLC_LOGO}" '
                f'group-title="NHL GAME",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={HEADERS['referer']}\n")
            f.write(f"#EXTVLCOPT:http-origin={HEADERS['origin']}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{url}\n\n")

    # TiviMate
    ua_enc = quote_plus(USER_AGENT)
    referer = HEADERS["referer"]
    origin = HEADERS["origin"]
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            t = title or ""
            f.write(f'#EXTINF:-1,{t}\n')
            f.write(f"{url}|referer={referer}|origin={origin}|user-agent={ua_enc}\n")
    log(f"✅ TiviMate playlist generated: {OUTPUT_TIVI}")


async def main():
    log("🚀 Starting NHL Webcast scraper (rebuilt)...")

    try:
        resp = requests.get(BASE, headers={"User-Agent": USER_AGENT}, timeout=15)
        resp.raise_for_status()
        homepage_html = resp.text
    except Exception as e:
        log(f"❌ Failed to fetch homepage {BASE}: {e}")
        homepage_html = ""

    event_links = find_event_links_from_homepage(homepage_html, base=BASE)
    log(f"🔍 Found {len(event_links)} event page(s) from homepage.")

    if not event_links:
        fallback = set(re.findall(r'https?://slapstreams\.com/[-\w/]+', homepage_html))
        if fallback:
            event_links = [(u, "") for u in fallback]
            log(f"ℹ️ Found {len(event_links)} fallback links via regex.")
    if not event_links:
        log("❌ No streams captured.")
        return

    found_entries = []
    async with async_playwright() as p:
        for idx, (url, text_hint) in enumerate(event_links, start=1):
            log(f"🔎 Processing event {idx}/{len(event_links)}: {text_hint or '—'} -> {url}")
            try:
                m3u8, page_html = await capture_m3u8_from_page(p, url, timeout_ms=20000)
            except Exception as e:
                log(f"⚠️ Error during capture for {url}: {e}")
                m3u8 = None
                page_html = None

            if m3u8:
                title = text_hint.strip() if text_hint else ""
                if not title and page_html:
                    title = guess_title_from_html(page_html)
                title = clean_event_title(title)  # <-- PATCHED FUNCTION USED HERE
                if not m3u8.lower().startswith("http"):
                    m3u8 = urljoin(url, m3u8)
                log(f"✅ Captured m3u8 for {url}: {m3u8}")
                found_entries.append((title, m3u8))
            else:
                log(f"⚠️ No m3u8 found for {url}")

    if not found_entries:
        log("❌ No streams captured.")
        return

    write_playlists(found_entries)
    log("✅ Done — playlists written.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Interrupted by user")
