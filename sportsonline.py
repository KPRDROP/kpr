import requests
import time
import logging
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scraper")

# Configuration
TXT_SCHEDULE_URL = "https://sportsonline.sn/prog.txt"
HTML_BASE_URL = "https://sportzonline.live/"
CHROME_PATH = "/usr/bin/google-chrome-beta"
M3U_FILE = "sportzonline.m3u"

RETRY_SLEEP = 1  # seconds

# Helpers

def fetch_schedule_txt():
    try:
        log.info(f"📥 Trying schedule TXT from: {TXT_SCHEDULE_URL}")
        r = requests.get(TXT_SCHEDULE_URL, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.warning(f"❌ Failed to fetch TXT schedule: {e}")
        return None

def parse_schedule_txt(raw):
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Expect format: "HH:MM   Title | url"
        parts = line.split("   ", 1)
        if len(parts) != 2:
            continue
        time_part, rest = parts
        if " | " in rest:
            title, link = rest.rsplit(" | ", 1)
        else:
            # fallback: last space
            p = rest.rsplit(" ", 1)
            title, link = p[0], p[-1]
        title = title.strip()
        link = link.strip()
        if not link.startswith("http"):
            link = HTML_BASE_URL.rstrip("/") + "/" + link.lstrip("/")
        events.append({"title": title, "url": link})
    log.info(f"Parsed {len(events)} events from TXT schedule")
    return events

def fetch_events_html():
    try:
        log.info(f"📥 Fetching HTML schedule from: {HTML_BASE_URL}")
        r = requests.get(HTML_BASE_URL, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.error(f"❌ Failed to fetch HTML schedule: {e}")
        return None

def parse_events_html(html):
    soup = BeautifulSoup(html, "lxml")
    events = []
    for a in soup.select("a[href*='/channels/']"):
        href = a.get("href")
        title = a.get_text(strip=True) or href
        if not href:
            continue
        url = href if href.startswith("http") else HTML_BASE_URL.rstrip("/") + href
        events.append({"title": title, "url": url})
    log.info(f"Parsed {len(events)} events from HTML schedule")
    return events

def extract_m3u8(playwright, page_url):
    browser = playwright.chromium.launch(
        executable_path=CHROME_PATH,
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    page = browser.new_page()
    page.goto(page_url, wait_until="networkidle")
    time.sleep(2)  # wait for JS
    content = page.content()
    page.close()
    browser.close()

    # Try to find m3u8 link
    for part in content.split('"'):
        if ".m3u8" in part:
            return part
    # fallback: regex or other heuristics could go here
    return None

def save_m3u(events):
    with open(M3U_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for e in events:
            m = e.get("m3u8")
            if not m:
                continue
            f.write(f"#EXTINF:-1,{e['title']}\n")
            f.write(f"{m}\n")

def main():
    log.info("🚀 Starting scraper")

    schedule_txt = fetch_schedule_txt()
    events = None
    if schedule_txt:
        events = parse_schedule_txt(schedule_txt)
    
    if not events:
        html = fetch_events_html()
        if html:
            events = parse_events_html(html)
    
    if not events or len(events) == 0:
        log.error("❌ Unable to find any events from both TXT and HTML sources.")
        return

    log.info(f"📺 Found {len(events)} events")

    with sync_playwright() as p:
        for ev in events:
            try:
                m3u8 = extract_m3u8(p, ev["url"])
                ev["m3u8"] = m3u8
                log.info(f"✅ {ev['title']} -> {m3u8}")
            except Exception as e:
                log.warning(f"❌ Failed {ev['title']}: {e}")
                ev["m3u8"] = None

    save_m3u(events)
    log.info(f"✅ Saved playlist with {len(events)} entries to {M3U_FILE}")

if __name__ == "__main__":
    main()
