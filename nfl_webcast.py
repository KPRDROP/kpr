#!/usr/bin/env python3
import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus

from playwright.async_api import async_playwright

BASE = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

HEADERS = {
    "referer": BASE,
    "origin": BASE,
}

LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"


def log(msg):
    print(msg)
    sys.stdout.flush()


def clean_title(text: str) -> str:
    if not text:
        return "NFL Game"
    text = text.replace("@", "vs")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def extract_event_links(page):
    log("🔍 Extracting event links from DOM…")

    links = await page.evaluate("""
        () => {
            return Array.from(document.querySelectorAll("a[href]"))
                .map(a => a.href)
                .filter(h =>
                    h.includes("nflwebcast.com/") &&
                    h.includes("live-stream")
                );
        }
    """)

    # Deduplicate
    links = list(dict.fromkeys(links))
    return links


async def capture_m3u8(playwright, url):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    m3u8_url = None

    def on_response(resp):
        nonlocal m3u8_url
        try:
            if ".m3u8" in resp.url and not m3u8_url:
                m3u8_url = resp.url
        except:
            pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)

        # Intentar activar player
        for sel in ["video", ".play", ".btn", "body"]:
            try:
                await page.click(sel, timeout=1500, force=True)
                await asyncio.sleep(1)
            except:
                pass

        # Esperar tráfico
        for _ in range(20):
            if m3u8_url:
                break
            await asyncio.sleep(0.5)

    finally:
        await browser.close()

    return m3u8_url


def write_playlists(entries):
    # VLC
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-logo="{LOGO}" group-title="NFL",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={BASE}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(url + "\n\n")

    # TiviMate
    ua_enc = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(
                f"{url}|referer={BASE}|origin={BASE}|user-agent={ua_enc}\n"
            )

    log("✅ Playlists written successfully")


async def main():
    log("🚀 Starting NFL Webcast scraper (STABLE FIX)...")

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        log("🌐 Opening homepage…")
        await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(6)  # ← clave para Cloudflare

        event_links = await extract_event_links(page)
        await browser.close()

        log(f"🔎 Found {len(event_links)} event links")

        if not event_links:
            log("❌ No event pages found")
            return

        entries = []

        async with async_playwright() as p2:
            for idx, url in enumerate(event_links, 1):
                log(f"🎯 [{idx}/{len(event_links)}] Processing event")
                m3u8 = await capture_m3u8(p2, url)

                if m3u8:
                    title = clean_title(url.split("/")[-2].replace("-", " "))
                    entries.append((title, m3u8))
                    log(f"✅ Stream captured")
                else:
                    log("⚠️ No stream found")

        if not entries:
            log("❌ No streams captured")
            return

        write_playlists(entries)
        log("🎉 Done!")


if __name__ == "__main__":
    asyncio.run(main())
