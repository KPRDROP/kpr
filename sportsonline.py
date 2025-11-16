import asyncio
from urllib.parse import quote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
import aiohttp
import requests
from collections import defaultdict

# ------------------------
# Configuration
# ------------------------
SCHEDULE_URL = "https://sportsonline.sn/prog.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

VLC_HEADERS = [
    f'#EXTVLCOPT:http-user-agent={USER_AGENT}',
    '#EXTVLCOPT:http-referrer=https://sportsonline.sn/'
]

CHANNEL_LOGOS = {
    "Colombia x New Zealand": "https://example.com/logos/col_new.png",
    "Santos x Palmeiras": "https://example.com/logos/santos.png",
    "NBA: Denver Nuggets @ Minnesota Timberwolves": "https://example.com/logos/nba.png",
    "UFC 322: Prelims": "https://example.com/logos/ufc.png",
}

CATEGORY_KEYWORDS = {
    "NBA": "Basketball",
    "UFC": "Combat Sports",
    "Football": "Football",
    "Soccer": "Football",
    "x": "Football",
}

NAV_TIMEOUT = 45000  # per page timeout in ms
CONCURRENT_FETCHES = 10  # concurrent PHP page fetches
RETRIES = 3  # retries per page

FIXED_DOMAIN = "https://yzarygw.7380990745.xyz:8443"

# ------------------------
# Fetch and parse schedule
# ------------------------
def fetch_schedule():
    print(f"🌐 Fetching schedule from {SCHEDULE_URL}")
    r = requests.get(SCHEDULE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
    r.raise_for_status()
    return r.text

def parse_schedule(raw):
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            time_part, rest = line.split("   ", 1)
            title, link = rest.rsplit(" | ", 1)
            title = title.strip()
            link = link.strip()
            category = "Miscellaneous"
            for keyword, cat in CATEGORY_KEYWORDS.items():
                if keyword.lower() in title.lower():
                    category = cat
                    break
            events.append({"time": time_part, "title": title, "link": link, "category": category})
        except ValueError:
            continue
    print(f"📺 Parsed {len(events)} events")
    return events

# ------------------------
# Fetch real m3u8 from PHP page
# ------------------------
async def fetch_m3u8_from_php(page, php_url):
    found_urls = set()

    def response_handler(response):
        if ".m3u8" in response.url:
            found_urls.add(response.url)

    page.on("response", response_handler)

    try:
        print(f"⏳ Loading PHP page: {php_url}")
        await page.goto(php_url, timeout=NAV_TIMEOUT, wait_until="networkidle")
        # Momentum click: click any play button to trigger stream
        try:
            await page.click("button[class*=play], .vjs-big-play-button", timeout=5000)
            print(f"▶️ Clicked play button on {php_url}")
        except PlaywrightTimeout:
            print(f"⚠️ No play button found on {php_url}, continuing")
        await asyncio.sleep(4)  # wait for m3u8 request
    except Exception as e:
        print(f"⚠️ Failed to load {php_url}: {e}")
    finally:
        page.remove_listener("response", response_handler)

    # Validate m3u8 URLs
    async with aiohttp.ClientSession() as session:
        for url in found_urls:
            try:
                if "twhjon.7380990745.xyz" in url:
                    url = url.replace("twhjon.7380990745.xyz", "yzarygw.7380990745.xyz")
                async with session.get(url, headers={"User-Agent": USER_AGENT}, timeout=10) as resp:
                    if resp.status == 200:
                        print(f"✅ Valid m3u8: {url}")
                        return url  # pick first valid
            except Exception:
                continue
    print(f"❌ No valid m3u8 found for {php_url}")
    return None

# ------------------------
# Main routine
# ------------------------
async def main():
    raw = fetch_schedule()
    events = parse_schedule(raw)
    categorized = defaultdict(list)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        semaphore = asyncio.Semaphore(CONCURRENT_FETCHES)

        async def fetch_event(event):
            async with semaphore:
                page = await context.new_page()
                url = None
                for attempt in range(RETRIES):
                    url = await fetch_m3u8_from_php(page, event["link"])
                    if url:
                        break
                    else:
                        print(f"⚠️ Retry {attempt+1} for {event['title']}")
                        await asyncio.sleep(2)
                await page.close()
                if url:
                    categorized[event["category"]].append({
                        "title": event["title"],
                        "url": url,
                        "logo": CHANNEL_LOGOS.get(event["title"], "")
                    })

        await asyncio.gather(*(fetch_event(e) for e in events))
        await browser.close()

    # ------------------------
    # Generate playlists per category
    # ------------------------
    for category, items in categorized.items():
        safe_name = category.replace(" ", "_").lower()
        vlc_file = f"sportsonline_{safe_name}.m3u"
        tivimate_file = f"sportsonline_{safe_name}_tivimate.m3u"

        # VLC/Kodi playlist
        with open(vlc_file, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for item in items:
                f.write(f'#EXTINF:-1 tvg-logo="{item["logo"]}" group-title="{category}",{item["title"]}\n')
                for h in VLC_HEADERS:
                    f.write(f"{h}\n")
                f.write(item["url"] + "\n\n")

        # TiviMate playlist
        with open(tivimate_file, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for item in items:
                headers = f"referer=https://sportsonline.sn/|origin=https://sportsonline.sn|user-agent={ENCODED_USER_AGENT}"
                f.write(f'#EXTINF:-1 tvg-logo="{item["logo"]}" group-title="{category}",{item["title"]}\n')
                f.write(f"{item['url']}|{headers}\n\n")

        print(f"✅ Generated playlists for category '{category}': {vlc_file}, {tivimate_file}")

if __name__ == "__main__":
    asyncio.run(main())
