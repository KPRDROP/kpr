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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

BASE = "https://mlswebcast.com/"
OUTPUT_VLC = "MLSWebcast_VLC.m3u8"
OUTPUT_TIVI = "MLSWebcast_TiviMate.m3u8"
HEADERS = {
    "referer": https://mlswebcast.com/,
    "origin": https://mlswebcast.com,
    "user-agent": USER_AGENT
}

VLC_LOGO = "https://i.postimg.cc/nrPfn86k/Football.png"



def clean_event_title(title: str) -> str:
    """Clean only the event title (NOT metadata)."""
    if not title:
        return "MLS Game"

    t = title.strip()

    # Replace '@' → 'vs'
    t = t.replace("@", "vs")

    # Remove commas ONLY INSIDE TITLE
    t = t.replace(",", "")

    # Clean double spaces
    t = re.sub(r"\s{2,}", " ", t).strip()

    return t

# ------ Helpers ------

def log(*a, **kw):
    print(*a, **kw)
    sys.stdout.flush()


def clean_title(raw: str) -> str:
    """Keep only the main human title: drop site-suffix like ' | MLS Live Stream...'"""
    if not raw:
        return ""
    raw = raw.strip()
    # Often titles contain " | MLS Live Stream ...", split by pipe and keep leftmost useful part
    parts = [p.strip() for p in raw.split("|")]
    if parts:
        return parts[0]
    return raw


def find_event_links_from_homepage(html: str, base: str = BASE) -> list:
    """Try multiple strategies to extract event page URLs and associated text labels."""
    soup = BeautifulSoup(html, "lxml")
    links = []

    # Strategy 1: look for cards (common pattern the site used)
    for a in soup.select(".card .card-body a, .card a.btn, .card a"):
        href = a.get("href")
        if not href:
            continue
        href = urljoin(base, href)
        text = a.text.strip() or ""
        # also try to find the nearby <p class="card-text"> (event name)
        parent = a.find_parent(class_="card-body")
        if parent:
            p = parent.find("p", class_="card-text")
            if p and p.text.strip():
                text = p.text.strip()
        links.append((href, text))

    # Strategy 2: anchor tags pointing to the same host
    if not links:
        for a in soup.find_all("a", href=True):
            href = urljoin(base, a["href"])
            if href.startswith(base):
                text = (a.text or "").strip()
                links.append((href, text))

    # Strategy 3: search for any data-url patterns (fallback)
    if not links:
        for m in re.finditer(r'https?://mlswebcast\.com/[-\w/]+', html):
            href = m.group(0)
            links.append((href, ""))

    # Deduplicate while preserving order
    seen = set()
    out = []
    for href, text in links:
        if href in seen:
            # might update text if previously empty
            if not [t for (h, t) in out if h == href][0] and text:
                # replace previous tuple's text
                out = [(h, text if h == href else t) for (h, t) in out]
            continue
        seen.add(href)
        out.append((href, text))
    return out


def guess_title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    # prefer og:title or title or h1
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
    """
    Visit `url` using Playwright and capture the first .m3u8 network request.
    Returns tuple (m3u8_url, page_title_html) or (None, None).
    """
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
                # prefer playable playlists (not .ts segments)
                # filter out thumbnails or unrelated paths by checking /playlist/ or .m3u8
                if rurl.endswith(".m3u8") or "/playlist/" in rurl or "playlist" in rurl:
                    if not captured:
                        captured = rurl
        except Exception:
            pass

    try:
        page.on("response", resp_handler)

        # attempt to navigate and allow network activity
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            # continue — sometimes m3u8 will show up even if full page load timed out
            log(f"⚠️ Timeout loading {url} -- continuing to capture network events")
        except Exception as e:
            log(f"⚠️ Error navigating {url}: {e}")

        # also capture encoded/obfuscated strings inside page (some pages embed base64 reversed)
        content = await page.content()
        page_title_html = content

        # quick heuristic: look in page content for base64-ish strings that decode to .m3u8
        # pattern: look for 'aHR0c' (base64 for 'http') or reversed strings
        # Attempt to extract straightforward base64 encoded m3u8
        b64_candidates = set(re.findall(r'["\']([A-Za-z0-9+/=]{40,200})["\']', content))
        for c in b64_candidates:
            try:
                import base64
                dec = base64.b64decode(c).decode(errors="ignore")
                if ".m3u8" in dec:
                    # prefer dec containing live.webcastserver.online or /playlist/
                    if "m3u8" in dec and not captured:
                        captured = dec.strip()
                        log("🔎 Found candidate from base64 in page content")
                        break
            except Exception:
                continue

        # Many players only request m3u8 after clicking play. Attempt to click on a likely play-area.
        try:
            # try several selectors to click to trigger player network activity
            for sel in ["#player", ".player", ".play-button", ".play", "video", "body"]:
                try:
                    el = page.locator(sel)
                    if await el.count() > 0:
                        await el.first.click(timeout=1200, force=True)
                        await asyncio.sleep(1.0)
                except Exception:
                    # ignore click errors; just continue
                    pass
        except Exception:
            pass

        # wait up to a few seconds for network events to appear
        total_wait = 0.0
        max_wait = 8.0
        while total_wait < max_wait and not captured:
            await asyncio.sleep(0.6)
            total_wait += 0.6

        # final attempt: inspect page content again for 'playlist' urls (plain)
        if not captured:
            m = re.search(r'https?://[^\s"\'<>]+\.(?:m3u8)(?:\?[^\s"\'<>]*)?', content)
            if m:
                captured = m.group(0)

        # further attempt: look for reversed base64 pattern like encoded.split("").reverse().join("")
        if not captured:
            rev_pattern = re.search(r'encoded\s*=\s*["\']([A-Za-z0-9+/=]+)["\']', content)
            if rev_pattern:
                try:
                    import base64
                    # Some sites reverse then base64; try both directions
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
     - Webcast_VLC.m3u8 (simple: #EXTINF:-1,{title} \n {url})
     - Webcast_TiviMate.m3u8 (same but add |referer=...|origin=...|user-agent=... after url)
    """
    # VLC
    with open("MLSWebcast_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="MLS.Soccer.Dummy.us" '
                f'tvg-name="MLS" tvg-logo="{VLC_LOGO}" '
                f'group-title="MLS GAME",{title}\n'
            )
            f.write("#EXTVLCOPT:http-referrer=https://mlswebcast.com/\n")
            f.write("#EXTVLCOPT:http-origin=https://mlswebcast.com\n")
            f.write(
                "#EXTVLCOPT:http-user-agent="
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36\n"
            )
            f.write(f"{url}\n\n")

    # TiviMate
    ua_enc = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            t = title or ""
            f.write(f'#EXTINF:-1,{t}\n')
            # Append headers for TiviMate (pipe-separated)
            # note: double-equals in user-agent value used previously in your examples; keep single '=' before UA per common format
            f.write(f"{url}|referer={referer}|origin={origin}|user-agent={ua_enc}\n")
    log(f"✅ TiviMate playlist generated: {OUTPUT_TIVI}")


async def main():
    log("🚀 Starting MLS Webcast scraper (rebuilt)...")

    # Step 1: fetch homepage HTML (simple requests is fine and faster than headless browser)
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
        # Try to find event-candidate URLs by searching for typical path segments (fallback)
        fallback = set(re.findall(r'https?://mlswebcast\.com/[-\w/]+', homepage_html))
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
                # Determine title
                title = text_hint.strip() if text_hint else ""
                if not title and page_html:
                    title = guess_title_from_html(page_html)
                # clean trailing site suffixes
                title = clean_title(title)
                # ensure we only capture absolute m3u8 urls
                if not m3u8.lower().startswith("http"):
                    # if we captured something relative, try to resolve with base url
                    m3u8 = urljoin(url, m3u8)
                log(f"✅ Captured m3u8 for {url}: {m3u8}")
                found_entries.append((title, m3u8))
            else:
                log(f"⚠️ No m3u8 found for {url}")

    if not found_entries:
        log("❌ No streams captured.")
        return

    # Write playlists
    write_playlists(found_entries)
    log("✅ Done — playlists written.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Interrupted by user")
