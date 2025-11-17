import time
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://sportzonline.live/"
CHROME_PATH = "/usr/bin/google-chrome-beta"
M3U_FILE = "sportzonline.m3u"

def fetch_events():
    resp = requests.get(BASE_URL, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    events = []
    for link in soup.select("a[href*='/channels/']"):
        title = link.get_text(strip=True)
        url = link["href"]
        url = url if url.startswith("http") else BASE_URL.rstrip("/") + url
        events.append({"title": title, "url": url})
    return events

def extract_m3u8(page_url, playwright):
    browser = playwright.chromium.launch(
        executable_path=CHROME_PATH,
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    page = browser.new_page()
    page.goto(page_url, wait_until="networkidle")
    time.sleep(2)
    content = page.content()
    page.close()
    browser.close()

    # find first .m3u8 in HTML
    for part in content.split('"'):
        if ".m3u8" in part:
            return part
    return None

def save_m3u(events):
    with open(M3U_FILE, "w") as f:
        f.write("#EXTM3U\n")
        for e in events:
            if e.get("m3u8"):
                f.write(f"#EXTINF:-1,{e['title']}\n{e['m3u8']}\n")

def main():
    print("🚀 Starting scrape...")
    events = fetch_events()
    print(f"📺 Found {len(events)} events")

    with sync_playwright() as p:
        for e in events:
            try:
                m3u8 = extract_m3u8(e["url"], p)
                e["m3u8"] = m3u8
                print(f"✅ {e['title']}: {m3u8}")
            except Exception as ex:
                print(f"❌ {e['title']} failed: {ex}")
                e["m3u8"] = None

    save_m3u(events)
    print(f"✅ Saved {len(events)} events to {M3U_FILE}")

if __name__ == "__main__":
    main()
