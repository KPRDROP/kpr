import asyncio
from urllib.parse import quote
from playwright.async_api import async_playwright
import requests
from collections import defaultdict

# Input schedule URL
SCHEDULE_URL = "https://sportsonline.sn/prog.txt"

# User-Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

# VLC/Kodi headers
VLC_HEADERS = [
    f'#EXTVLCOPT:http-user-agent={USER_AGENT}',
    '#EXTVLCOPT:http-referrer=https://dukehorror.net/'
]

# Logos (example, add more)
CHANNEL_LOGOS = {
    "Colombia x New Zealand": "https://example.com/logos/col_new.png",
    "Santos x Palmeiras": "https://example.com/logos/santos.png",
    "NBA: Denver Nuggets @ Minnesota Timberwolves": "https://example.com/logos/nba.png",
    "UFC 322: Prelims": "https://example.com/logos/ufc.png",
    # Add more logos
}

# Category keywords
CATEGORY_KEYWORDS = {
    "NBA": "Basketball",
    "UFC": "Combat Sports",
    "Football": "Football",
    "Soccer": "Football",
    "x": "Football",
}

NAV_TIMEOUT = 15000  # 15s


def fetch_schedule():
    print(f"🌐 Fetching schedule from {SCHEDULE_URL}")
    r = requests.get(SCHEDULE_URL, headers={"User-Agent": USER_AGENT}, timeout=10)
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
            # Determine category
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


async def fetch_m3u8(playwright, url):
    """Open PHP link in headless Chromium, extract .m3u8"""
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()
    found_urls = set()

    def handle_response(response):
        if ".m3u8" in response.url:
            found_urls.add(response.url)

    page.on("response", handle_response)

    try:
        await page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"⚠️ Failed to open {url}: {e}")
    finally:
        page.remove_listener("response", handle_response)
        await browser.close()

    return list(found_urls)


async def main():
    raw = fetch_schedule()
    events = parse_schedule(raw)

    async with async_playwright() as p:
        categorized = defaultdict(list)

        for event in events:
            title = event["title"]
            php_link = event["link"]
            category = event["category"]
            logo = CHANNEL_LOGOS.get(title, "")

            m3u8_urls = await fetch_m3u8(p, php_link)
            if not m3u8_urls:
                continue

            categorized[category].append({
                "title": title,
                "urls": m3u8_urls,
                "logo": logo
            })

    # Generate playlists per category
    for category, items in categorized.items():
        safe_name = category.replace(" ", "_").lower()
        vlc_file = f"sportsonline_{safe_name}.m3u"
        tivimate_file = f"sportsonline_{safe_name}_tivimate.m3u"

        # VLC/Kodi playlist
        with open(vlc_file, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for item in items:
                url = item["urls"][0]
                f.write(f'#EXTINF:-1 tvg-logo="{item["logo"]}" group-title="{category}",{item["title"]}\n')
                for h in VLC_HEADERS:
                    f.write(f"{h}\n")
                f.write(url + "\n\n")

        # TiviMate playlist
        with open(tivimate_file, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for item in items:
                url = item["urls"][0]
                headers = f"referer=https://dukehorror.net/|user-agent={ENCODED_USER_AGENT}"
                f.write(f'#EXTINF:-1 tvg-logo="{item["logo"]}" group-title="{category}",{item["title"]}\n')
                f.write(f"{url}|{headers}\n\n")

        print(f"✅ Generated playlists for category '{category}': {vlc_file}, {tivimate_file}")


if __name__ == "__main__":
    asyncio.run(main())