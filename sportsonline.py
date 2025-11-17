import os
import time
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# -----------------------------
# CONFIG
# -----------------------------
CHROME_PATH = "/usr/bin/google-chrome-beta"
BASE_URL = "https://sportzonline.live/"

# Output
M3U_FILE = "sportzonline.m3u"

# -----------------------------
# FUNCTIONS
# -----------------------------

def fetch_schedule_html():
    """Fetch the main page HTML"""
    resp = requests.get(BASE_URL, timeout=15)
    resp.raise_for_status()
    return resp.text

def parse_events(html):
    """Parse categories and events from HTML"""
    soup = BeautifulSoup(html, "lxml")
    events = []

    # Example: all links in main schedule section
    for link in soup.select("a[href*='/channels/']"):
        title = link.get_text(strip=True)
        href = link["href"]
        full_url = href if href.startswith("http") else BASE_URL.rstrip("/") + href
        events.append({
            "title": title,
            "url": full_url
        })

    return events

def extract_m3u8_url(playwright, url):
    """Open event page and extract the actual m3u8 stream"""
    browser = playwright.chromium.launch(
        executable_path=CHROME_PATH,
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    page = browser.new_page()
    page.goto(url, wait_until="networkidle")
    time.sleep(2)  # wait for JS content

    # Try to find m3u8 URLs in page source
    content = page.content()
    page.close()
    browser.close()

    # Simple heuristic: find .m3u8 in page
    for part in content.split('"'):
        if ".m3u8" in part:
            return part
    return None

def save_m3u(events):
    """Save M3U playlist"""
    with open(M3U_FILE, "w") as f:
        f.write("#EXTM3U\n")
        for e in events:
            if e.get("m3u8"):
                f.write(f"#EXTINF:-1,{e['title']}\n")
                f.write(f"{e['m3u8']}\n")

# -----------------------------
# MAIN
# -----------------------------
def main():
    print("🚀 Starting SportsOnline scrape...")

    html = fetch_schedule_html()
    events = parse_events(html)
    print(f"📺 Found {len(events)} events")

    with sync_playwright() as p:
        for e in events:
            try:
                m3u8 = extract_m3u8_url(p, e["url"])
                e["m3u8"] = m3u8
                print(f"✅ {e['title']}: {m3u8}")
            except Exception as ex:
                print(f"❌ Failed {e['title']}: {ex}")
                e["m3u8"] = None

    save_m3u(events)
    print(f"✅ Saved {len(events)} events to {M3U_FILE}")

if __name__ == "__main__":
    main()
