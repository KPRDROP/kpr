import asyncio
import logging
import requests
from pathlib import Path
from playwright.async_api import async_playwright

# -----------------------------
# Configuration
# -----------------------------
PROG_TXT_URL = "https://sportsonline.sn/prog.txt"
OUTPUT_M3U = Path("sportzonline.m3u")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# -----------------------------
# Utility Functions
# -----------------------------
def fetch_prog_txt(url):
    """Download prog.txt and return list of PHP URLs"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        lines = resp.text.splitlines()
        # Filter lines that look like channel URLs (.php)
        php_links = [line.strip() for line in lines if line.strip().endswith(".php")]
        logging.info(f"Found {len(php_links)} PHP links in prog.txt")
        return php_links
    except Exception as e:
        logging.error(f"Failed to fetch prog.txt: {e}")
        return []

async def extract_m3u8(page, url):
    """Open the page and try to extract the m3u8 URL from player"""
    try:
        title, url = line.split("|")
        title = title.strip()
        url = url.strip()
        await page.goto(url, timeout=15000)
        # Wait for video element
        await page.wait_for_selector("video", timeout=10000)
        
        # Intercept network requests to find m3u8
        m3u8_url = None
        async def handle_route(route):
            nonlocal m3u8_url
            if ".m3u8" in route.request.url:
                m3u8_url = route.request.url
            await route.continue_()
        
        await page.route("**/*", handle_route)
        await page.wait_for_timeout(5000)  # wait for requests to fire
        return m3u8_url
    except Exception as e:
        logging.warning(f"Failed to extract m3u8 from {url}: {e}")
        return None

async def scrape_all(php_links):
    """Scrape all PHP links concurrently"""
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",  # use system Chrome
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context()
        page = await context.new_page()
        
        for link in php_links:
            logging.info(f"Scraping {link}")
            m3u8 = await extract_m3u8(page, link)
            if m3u8:
                results.append((link, m3u8))
                logging.info(f"Found m3u8: {m3u8}")
            else:
                logging.info(f"No m3u8 found for {link}")
        
        await browser.close()
    return results

# -----------------------------
# Generate M3U Playlist
# -----------------------------
def save_m3u(events):
    with OUTPUT_M3U.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for idx, (php_url, m3u8_url) in enumerate(events, 1):
            title = php_url.split("/")[-1].replace(".php", "")
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{m3u8_url}\n")
    logging.info(f"✅ Saved {len(events)} streams to {OUTPUT_M3U}")

# -----------------------------
# Main
# -----------------------------
async def main():
    logging.info("🚀 Starting scrape...")
    php_links = fetch_prog_txt(PROG_TXT_URL)
    if not php_links:
        logging.warning("No PHP links found, exiting.")
        return
    events = await scrape_all(php_links)
    if not events:
        logging.warning("No m3u8 streams found.")
    else:
        save_m3u(events)
    logging.info("✅ Finished scrape.")

if __name__ == "__main__":
    asyncio.run(main())
