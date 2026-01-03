#!/usr/bin/env python3
import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ---------------- CONFIG ----------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"
)

BASE = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

HEADERS = {
    "referer": BASE,
    "origin": BASE,
}

VLC_LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"

EVENT_RE = re.compile(
    r"https://nflwebcast\.com/[a-z0-9-]+-live-stream-online-free/?$",
    re.I,
)

# ---------------- UTILS ----------------

def log(*a):
    print(*a)
    sys.stdout.flush()


def clean_event_title(title: str) -> str:
    if not title:
        return "NFL Game"
    title = title.replace("@", "vs")
    title = title.replace(",", "")
    title = re.sub(r"\s{2,}", " ", title).strip()
    return title


def guess_title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"]

    h1 = soup.find("h1")
    if h1 and h1.text:
        return h1.text.strip()

    title = soup.find("title")
    if title and title.text:
        return title.text.strip()

    return "NFL Game"

# ---------------- EVENT DETECTION ----------------

async def get_event_links(playwright):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    log("🌐 Loading NFLWebcast homepage (Cloudflare bypass)…")
    await page.goto(BASE, wait_until="networkidle", timeout=60000)

    # Allow Cloudflare JS challenge to finish
    await asyncio.sleep(6)

    html = await page.content()
    await browser.close()

    links = sorted(set(EVENT_RE.findall(html)))
    return links

# ---------------- M3U8 CAPTURE ----------------

async def capture_m3u8(playwright, url):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    found = None
    page_html = None

    def on_response(resp):
        nonlocal found
        try:
            if ".m3u8" in resp.url and not found:
                found = resp.url
        except Exception:
            pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(5)

        page_html = await page.content()

        if not found:
            m = re.search(r"https?://[^\s\"']+\.m3u8[^\s\"']*", page_html)
            if m:
                found = m.group(0)

        # Try click autoplay
        for sel in ["video", "#player", ".player", "body"]:
            try:
                el = page.locator(sel)
                if await el.count():
                    await el.first.click(force=True, timeout=1000)
                    await asyncio.sleep(1)
            except Exception:
                pass

        await asyncio.sleep(4)

    except PlaywrightTimeoutError:
        log(f"⚠️ Timeout loading {url}")

    await browser.close()
    return found, page_html

# ---------------- PLAYLIST OUTPUT ----------------

def write_playlists(entries):
    ua_enc = quote_plus(USER_AGENT)

    # VLC
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="NFL.us" tvg-name="NFL" '
                f'tvg-logo="{VLC_LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={HEADERS['referer']}\n")
            f.write(f"#EXTVLCOPT:http-origin={HEADERS['origin']}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{url}\n\n")

    # TiviMate
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(
                f"{url}|referer={HEADERS['referer']}|"
                f"origin={HEADERS['origin']}|user-agent={ua_enc}\n"
            )

    log(f"✅ Playlists written:")
    log(f"   - {OUTPUT_VLC}")
    log(f"   - {OUTPUT_TIVI}")

# ---------------- MAIN ----------------

async def main():
    log("🚀 Starting NFL Webcast scraper (FINAL REAL FIX)")

    async with async_playwright() as p:
        event_urls = await get_event_links(p)

        log(f"🔍 Found {len(event_urls)} event pages")

        if not event_urls:
            log("❌ No event pages found")
            return

        entries = []

        for idx, url in enumerate(event_urls, 1):
            log(f"🔎 [{idx}/{len(event_urls)}] Processing {url}")

            m3u8, html = await capture_m3u8(p, url)

            if not m3u8:
                log("⚠️ No m3u8 found")
                continue

            title = clean_event_title(guess_title_from_html(html))
            if not m3u8.startswith("http"):
                m3u8 = urljoin(url, m3u8)

            log(f"✅ Captured stream: {m3u8}")
            entries.append((title, m3u8))

    if not entries:
        log("❌ No streams captured")
        return

    write_playlists(entries)
    log("🎉 Done.")

if __name__ == "__main__":
    asyncio.run(main())
