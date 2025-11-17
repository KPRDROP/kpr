#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import asyncio
import logging
import requests
from pathlib import Path
from playwright.async_api import async_playwright

# -----------------------------
# Configuration
# -----------------------------
SCHEDULE_URL = "https://sportsonline.sn/prog.txt"  # Update if URL changes
OUTPUT_FILE = Path("sportzonline.m3u")
LOG_FILE = Path("scraper.log")

# -----------------------------
# Logging setup
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# -----------------------------
# Helper functions
# -----------------------------
def fetch_schedule(url: str) -> list[str]:
    """Fetch schedule TXT and return list of lines containing events."""
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        lines = [line.strip() for line in r.text.splitlines() if line.strip()]
        logging.info(f"📺 Found {len(lines)} events in schedule")
        return lines
    except Exception as e:
        logging.error(f"❌ Failed to fetch schedule: {e}")
        return []

async def extract_m3u8(page, php_url: str) -> list[str]:
    """Given a PHP URL, load page and extract all m3u8 URLs."""
    streams = []
    try:
        await page.goto(php_url, timeout=15000)
        # Playwright may need to wait for player to load JS
        await asyncio.sleep(3)

        # Look for m3u8 URLs in HTML or JS
        content = await page.content()
        urls = re.findall(r"https?://[^\s'\";]+\.m3u8", content)
        streams.extend(urls)

        # Also try page.evaluate for player objects if needed
        # urls = await page.eval_on_selector_all('video', 'els => els.map(v => v.src)')
        # streams.extend(urls)

    except Exception as e:
        logging.warning(f"Failed to extract m3u8 from {php_url}: {e}")
    return list(set(streams))  # Remove duplicates

async def scrape_events(events: list[str]) -> list[tuple[str, str]]:
    """Scrape all events and return list of tuples (title, m3u8_url)."""
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        for line in events:
            if "|" not in line:
                logging.warning(f"Skipping malformed line: {line}")
                continue
            title, url = map(str.strip, line.split("|", maxsplit=1))
            logging.info(f"Scraping {title} | {url}")
            m3u8_list = await extract_m3u8(page, url)
            if not m3u8_list:
                logging.info(f"No m3u8 found for {title} | {url}")
                continue
            for m3u8 in m3u8_list:
                results.append((title, m3u8))
        await browser.close()
    return results

def save_m3u(playlist: list[tuple[str, str]], output_file: Path):
    """Save M3U playlist to file."""
    with output_file.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in playlist:
            f.write(f"#EXTINF:-1,{title}\n{url}\n")
    logging.info(f"✅ Saved {len(playlist)} streams to {output_file}")

# -----------------------------
# Main execution
# -----------------------------
async def main():
    logging.info("🚀 Starting scrape...")
    events = fetch_schedule(SCHEDULE_URL)
    if not events:
        logging.warning("No events found.")
        return

    playlist = await scrape_events(events)
    if not playlist:
        logging.warning("No m3u8 streams found.")
        return

    save_m3u(playlist, OUTPUT_FILE)
    logging.info("✅ Finished scrape.")

if __name__ == "__main__":
    asyncio.run(main())
