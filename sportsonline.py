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
    '#EXTVLCOPT:http-referrer=ttps://dukehorror.net/'
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

NAV_TIMEOUT = 60000  # per page timeout in ms
CONCURRENT_FETCHES = 10  # concurrent PHP page fetches
RETRIES = 3  # retries per page

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
# Fetch real m3u8 from PHP page with Momentum click + ad tab handling + retries
# ------------------------
async def fetch_m3u8_from_php(page, php_url):
    found_urls = set()

    def response_handler(response):
        if ".m3u8" in response.url:
            found_urls.add(response.url)

    page.on("response", response_handler)

    for attempt in range(RETRIES):
        try:
            print(f"⏳ Loading PHP page (attempt {attempt+1}): {php_url}")
            await page.goto(php_url, timeout=NAV_TIMEOUT, wait_until="load")

            # -----------------------------
            # Momentum click + ad tab handling
            # -----------------------------
            try:
                await page.mouse.click(200, 200)
                print("  👆 First click triggered ad (if any)")

                pages_before = page.context.pages
                new_tab = None
                for _ in range(12):
                    pages_now = page.context.pages
                    if len(pages_now) > len(pages_before):
                        new_tab = [p for p in pages_now if p not in pages_before][0]
                        break
                    await asyncio.sleep(0.25)

                if new_tab:
                    await asyncio.sleep(0.5)
                    print(f"  🚫 Closing ad tab: {new_tab.url}")
                    await new_tab.close()

                await asyncio.sleep(1)
                await page.mouse.click(200, 200)
                print("  ▶️ Second click started player")
            except Exception as e:
                print(f"⚠️ Momentum click failed: {e}")

            # wait for m3u8 requests
            await asyncio.sleep(4)
            break  # success, exit retry loop

        except PlaywrightTimeout:
            print(f"⚠️ Timeout on {php_url}, retrying...")
            await asyncio.sleep(2)
            continue
        except Exception as e:
            print(f"⚠️ Error loading {php_url}: {e}")
            await asyncio.sleep(2)
            continue
    else:
        print(f"❌ Failed to load {php_url} after {RETRIES} attempts")

    page.remove_listener("response", response_handler)

    # Validate m3u8 URLs without altering domain
    async with aiohttp.ClientSession() as session:
        for url in found_urls:
            try:
                async with session.get(url, headers={"User-Agent": USER_AGENT}, timeout=10) as resp:
                    if resp.status == 200:
                        print(f"✅ Valid m3u8: {url}")
                        return url  # return first valid
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
                headers = f"referer=ttps://dukehorror.net/|origin=ttps://dukehorror.net|user-agent={ENCODED_USER_AGENT}"
                f.write(f'#EXTINF:-1 tvg-logo="{item["logo"]}" group-title="{category}",{item["title"]}\n')
                f.write(f"{item['url']}|{headers}\n\n")

        print(f"✅ Generated playlists for category '{category}': {vlc_file}, {tivimate_file}")

if __name__ == "__main__":
    asyncio.run(main())
