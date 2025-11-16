import asyncio
from urllib.parse import quote
from playwright.async_api import async_playwright
import requests

# Input schedule URL
SCHEDULE_URL = "https://sportsonline.sn/prog.txt"

# User-Agent for requests
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

# VLC/Kodi custom headers
VLC_HEADERS = [
    f'#EXTVLCOPT:http-user-agent={USER_AGENT}',
    '#EXTVLCOPT:http-referrer=https://sportsonline.sn/'
]

# Channel logos (add more as needed)
CHANNEL_LOGOS = {
    "Colombia x New Zealand": "https://example.com/logos/col_new.png",
    "Santos x Palmeiras": "https://example.com/logos/santos.png",
    "NBA: Denver Nuggets @ Minnesota Timberwolves": "https://example.com/logos/nba.png",
    # Add other logos here
}

# Default group-title
DEFAULT_GROUP = "Sports"

# Timeout for Playwright
NAV_TIMEOUT = 15000  # 15 seconds


def fetch_schedule():
    print(f"🌐 Fetching schedule from {SCHEDULE_URL}")
    r = requests.get(SCHEDULE_URL, headers={"User-Agent": USER_AGENT}, timeout=10)
    r.raise_for_status()
    return r.text


def parse_schedule(raw):
    """
    Parse schedule lines into dicts:
    [{'time': '00:00', 'title': 'Colombia x New Zealand', 'link': '...'}, ...]
    """
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            time_part, rest = line.split("   ", 1)
            title, link = rest.rsplit(" | ", 1)
            events.append({"time": time_part, "title": title.strip(), "link": link.strip()})
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
        await asyncio.sleep(2)  # allow JS to load
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
        playlist_items = []

        for event in events:
            title = event["title"]
            php_link = event["link"]

            m3u8_urls = await fetch_m3u8(p, php_link)
            if not m3u8_urls:
                continue

            playlist_items.append({
                "title": title,
                "urls": m3u8_urls,
                "logo": CHANNEL_LOGOS.get(title, ""),
                "group": DEFAULT_GROUP
            })

    # Write VLC/Kodi playlist
    with open("sportsonline.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in playlist_items:
            url = item["urls"][0]
            f.write(f'#EXTINF:-1 tvg-logo="{item["logo"]}" group-title="{item["group"]}",{item["title"]}\n')
            for h in VLC_HEADERS:
                f.write(f"{h}\n")
            f.write(url + "\n\n")

    # Write TiviMate playlist
    with open("sportsonline_tivimate.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in playlist_items:
            url = item["urls"][0]
            headers = f"referer=https://sportsonline.sn/|origin=https://sportsonline.sn|user-agent={ENCODED_USER_AGENT}"
            f.write(f'#EXTINF:-1 tvg-logo="{item["logo"]}" group-title="{item["group"]}",{item["title"]}\n')
            f.write(f"{url}|{headers}\n\n")

    print(f"✅ Playlists generated: {len(playlist_items)} channels")


if __name__ == "__main__":
    asyncio.run(main())
