import asyncio
import json
import logging
import re
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-8s %(message)s',
    datefmt='%Y-%m-%d | %H:%M:%S'
)

API_URL = "https://embedhd.org/api-event.php"
OUT_FILE = Path("embedhd.m3u")

M3U8_RE = re.compile(r"\.m3u8(\?|$)", re.I)


async def fetch_events():
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get(API_URL, timeout=20) as r:
            r.raise_for_status()
            return await r.json()


async def resolve_m3u8(page, url, idx):
    found = {"url": None}

    def on_request(req):
        if found["url"]:
            return
        if M3U8_RE.search(req.url):
            found["url"] = req.url
            logging.info(f"URL {idx}) 🎯 Found m3u8")

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)

        # Give maestrohd1.js time to execute
        for _ in range(60):
            if found["url"]:
                break
            await asyncio.sleep(0.5)

        if not found["url"]:
            raise TimeoutError("m3u8 not detected")

        return found["url"]

    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass


async def main():
    logging.info("🚀 Starting EmbedHD scraper...")

    events = await fetch_events()
    streams = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )

        page = await context.new_page()

        urls = []
        for ev in events:
            if ev.get("status") != "LIVE":
                continue
            for s in ev.get("streams", []):
                urls.append({
                    "title": ev["title"],
                    "link": s["link"]
                })

        logging.info(f"Processing {len(urls)} new URL(s)")

        for i, ev in enumerate(urls, 1):
            try:
                m3u8 = await resolve_m3u8(page, ev["link"], i)
                streams.append((ev["title"], m3u8))
            except Exception as e:
                logging.warning(f"URL {i}) Failed: {e}")

        await browser.close()

    if streams:
        with OUT_FILE.open("w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for title, url in streams:
                f.write(f"#EXTINF:-1,{title}\n{url}\n")

    logging.info(f"Wrote {len(streams)} total events")


if __name__ == "__main__":
    asyncio.run(main())
