import asyncio
import re
import urllib.parse
from datetime import datetime
from playwright.async_api import async_playwright

BASE_URL = "https://buffstreams.plus/"
REFERER = BASE_URL
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
ENCODED_UA = urllib.parse.quote(USER_AGENT)

# --- Category metadata ---
TV_INFO = {
    "nfl": ("Football.Dummy.us", "https://i.postimg.cc/tRNpSGCq/Maxx.png", "NFL"),
    "nba": ("NBA.Basketball.Dummy.us", "https://i.postimg.cc/jdqKB3LW/Basketball-2.png", "NBA"),
    "mlb": ("MLB.Baseball.Dummy.us", "https://i.postimg.cc/FsFmwC7K/Baseball3.png", "MLB"),
    "nhl": ("Hockey.Dummy.us", "https://i.postimg.cc/jjJGbN7F/Hockey.png", "NHL"),
    "soccer": ("Soccer.Dummy.us", "https://i.postimg.cc/HsWHFvV0/Soccer.png", "Soccer"),
    "tennis": ("Tennis.Dummy.us", "https://i.postimg.cc/KYQ1rzT8/Tennis.png", "Tennis"),
    "boxing": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/8c4GjMnH/Combat-Sports.png", "Boxing"),
    "mma": ("UFC.Fight.Pass.Dummy.us", "https://i.postimg.cc/59Sb7W9D/Combat-Sports2.png", "MMA"),
    "f1": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Formula 1"),
    "ppv": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/mkj4tC62/PPV.png", "PPV"),
    "misc": ("Sports.Dummy.us", "https://i.postimg.cc/qMm0rc3L/247.png", "Random Events"),
}

# --- Match both .m3u8 and playlist/JS endpoints ---
STREAM_REGEX = re.compile(
    r"https?://[a-zA-Z0-9\.\-_/]+/(playlist|stream|load-playlist)[^\s\"']+"
)

async def extract_streams_from_page(page, url):
    """Extract both visible and network-captured streams."""
    streams = set()
    try:
        print(f"🔍 Opening: {url}")
        await page.goto(url, timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=15000)

        html = await page.content()
        streams.update(re.findall(STREAM_REGEX, html))

        # Capture from JS network requests (XHR/fetch)
        for request in page.context.requests:
            req_url = request.url
            if re.search(STREAM_REGEX, req_url):
                streams.add(req_url)

        print(f"  ➕ Found {len(streams)} possible streams.")
    except Exception as e:
        print(f"[!] Error extracting {url}: {e}")

    return list(streams)

def get_tv_info(url_or_title: str):
    lower = url_or_title.lower()
    for key, (tvgid, logo, group) in TV_INFO.items():
        if key in lower:
            return tvgid, logo, group
    return TV_INFO["misc"]

async def scrape_buffstreams():
    print("▶️ Starting BuffStreams playlist generation...\n")
    playlist_lines = [
        '#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"',
        f"# Last Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        print(f"🌐 Visiting {BASE_URL}")
        await page.goto(BASE_URL)
        await page.wait_for_load_state("domcontentloaded")

        # Discover category/event links
        links = await page.eval_on_selector_all("a[href]", "els => els.map(a => a.href)")
        event_links = [
            l for l in links
            if any(k in l.lower() for k in TV_INFO.keys())
            and "javascript:" not in l
        ]
        event_links = list(dict.fromkeys(event_links))  # dedupe

        print(f"✅ Found {len(event_links)} candidate event links.\n")

        for event_url in event_links:
            tv_id, logo, group_name = get_tv_info(event_url)
            event_title = event_url.split("/")[-1].replace("-", " ").title()

            streams = await extract_streams_from_page(page, event_url)
            if not streams:
                print(f"❌ No streams for {event_title}")
                continue

            for s in streams:
                playlist_lines.append(
                    f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tv_id}" '
                    f'group-title="BuffStreams - {group_name}",{event_title}'
                )
                playlist_lines.append(
                    f"{s}|referer={REFERER}|user-agent={ENCODED_UA}"
                )
                print(f"✅ Added stream: {event_title}")

        await browser.close()

    output_file = "BuffStreams_Playlist.m3u8"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(playlist_lines))

    print(f"\n🎉 Finished! Saved playlist as {output_file}")

if __name__ == "__main__":
    asyncio.run(scrape_buffstreams())
