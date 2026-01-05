#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ---------------- CONFIG ----------------

BASE = "https://nflwebcast.com/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

HEADERS = {
    "referer": BASE,
    "origin": BASE,
    "user-agent": USER_AGENT,
}

VLC_LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"

# ----------------------------------------


def log(*args):
    print(*args)
    sys.stdout.flush()


# ---------- TITLE CLEAN ----------

def clean_event_title(title: str) -> str:
    if not title:
        return "NFL Game"
    t = title.replace("@", "vs").replace(",", "")
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


# ---------- CLOUDFLARE BYPASS ----------

async def load_homepage_with_cf(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )

    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 800},
    )

    page = await context.new_page()

    log("🌐 Loading NFLWebcast homepage (Cloudflare JS challenge)…")
    await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)

    # wait for cf_clearance
    for _ in range(30):
        cookies = await context.cookies()
        if any(c["name"] == "cf_clearance" for c in cookies):
            log("✅ Cloudflare clearance obtained")
            await page.reload(wait_until="domcontentloaded")
            html = await page.content()
            await browser.close()
            return html
        await asyncio.sleep(1)

    log("❌ Cloudflare clearance NOT obtained")
    await browser.close()
    return ""


# ---------- EVENT LINK EXTRACTION ----------

def find_event_links_from_homepage(html: str):
    soup = BeautifulSoup(html, "lxml")
    events = []

    # DIRECT MATCH FOR WATCH BUTTONS
    for a in soup.select("a.btn.btn-info[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue
        if "live-stream" not in href:
            continue
        url = urljoin(BASE, href)
        events.append((url, a.text.strip()))

    # fallback: any nflwebcast.com/*-live-stream*
    if not events:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "nflwebcast.com" in href and "live-stream" in href:
                events.append((href, a.text.strip()))

    # deduplicate
    seen = set()
    out = []
    for url, text in events:
        if url not in seen:
            seen.add(url)
            out.append((url, text))

    return out


# ---------- M3U8 CAPTURE ----------

async def capture_m3u8_from_event(playwright, url):
    browser = await playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    captured = None

    def on_response(resp):
        nonlocal captured
        try:
            if ".m3u8" in resp.url and not captured:
                captured = resp.url
        except Exception:
            pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # try to start player
        for sel in ["iframe", "video", "button", "body"]:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click(force=True, timeout=1000)
            except Exception:
                pass

        # wait network
        for _ in range(15):
            if captured:
                break
            await asyncio.sleep(1)

        # HTML fallback
        if not captured:
            html = await page.content()
            m = re.search(r'https?://[^"\']+\.m3u8[^"\']*', html)
            if m:
                captured = m.group(0)

    finally:
        await browser.close()

    return captured


# ---------- PLAYLIST WRITER ----------

def write_playlists(entries):
    # VLC
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-logo="{VLC_LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={BASE}\n")
            f.write(f"#EXTVLCOPT:http-origin={BASE}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{url}\n\n")

    # TiviMate
    ua_enc = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(
                f"{url}|referer={BASE}|origin={BASE}|user-agent={ua_enc}\n"
            )

    log("✅ Playlists generated:")
    log(f" - {OUTPUT_VLC}")
    log(f" - {OUTPUT_TIVI}")


# ---------- MAIN ----------

async def main():
    log("🚀 Starting NFL Webcast scraper (FINAL REAL FIX)")

    async with async_playwright() as p:
        homepage_html = await load_homepage_with_cf(p)

        if not homepage_html:
            log("❌ No event pages found")
            return

        events = find_event_links_from_homepage(homepage_html)
        log(f"🔍 Found {len(events)} event pages")

        if not events:
            log("❌ No event pages found")
            return

        results = []

        for idx, (url, _) in enumerate(events, 1):
            log(f"🔎 [{idx}/{len(events)}] Processing {url}")
            m3u8 = await capture_m3u8_from_event(p, url)
            if m3u8:
                title = clean_event_title(url.split("/")[-2].replace("-", " "))
                log(f"✅ m3u8 captured: {m3u8}")
                results.append((title, m3u8))
            else:
                log("⚠️ No m3u8 found")

        if not results:
            log("❌ No streams captured")
            return

        write_playlists(results)
        log("🎉 Done")


if __name__ == "__main__":
    asyncio.run(main())
