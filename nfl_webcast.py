#!/usr/bin/env python3
import asyncio
import re
from playwright.async_api import async_playwright

BASE_URL = "https://nflwebcast.com/"
EVENT_PATTERN = re.compile(
    r"^https://nflwebcast\.com/.+-live-stream-online-free/?$"
)

async def scrape_nflwebcast():
    print("🚀 Starting NFL Webcast scraper (FINAL FIX)...")

    event_links = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        page = await context.new_page()

        print("🌐 Loading NFLWebcast homepage (Cloudflare protected)...")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

        # ⏳ Give Cloudflare time to finish
        await page.wait_for_timeout(8000)

        print("🔍 Extracting all <a href> links...")
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href)"
        )

        print(f"🔎 Found {len(links)} total links on page")

        for link in links:
            if EVENT_PATTERN.match(link):
                event_links.add(link)

        await browser.close()

    if not event_links:
        print("❌ No event pages found")
        return

    print(f"🏈 Found {len(event_links)} NFL event pages:\n")

    for url in sorted(event_links):
        print(url)

    print("\n🎉 Done.")

if __name__ == "__main__":
    asyncio.run(scrape_nflwebcast())
