import asyncio
import urllib.parse
import random
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

M3U8_FILE = "TheTV.m3u8"
BASE_URL = "https://thetvapp.link"
CHANNEL_LIST_URL = f"{BASE_URL}/live-tv"

SECTIONS_TO_APPEND = {
    "/nbastreams": "NBA",
    "/mlbstreams": "MLB",
    "/nhlstreams": "NHL",
    "/nflstreams": "NFL",
    "/soccerstreams": "Soccer",        
    "/cfbstreams": "NCAAF",
    "/ncaastreams": "NCAAB",
    "/f1streams": "F1",    
    "/wwestreams": "WWE",    
    "/boxingstreams": "Boxing",
    "/mmastreams": "MMA",
}

SPORTS_METADATA = {
    "MLB": {"tvg-id": "MLB.Baseball.Dummy.us", "logo": "https://i.postimg.cc/sDn8tvsK/major-league-baseball-logo-png-seeklogo-176127.png"},
    "PPV": {"tvg-id": "PPV.EVENTS.Dummy.us", "logo": "https://i.postimg.cc/y8ysVXP9/images-q-tbn-ANd9Gc-R6TUY0RT0w3qp-Hu-KZOesu8U3h4Ut-Y2A8-07Q-s.jpg"},
    "NFL": {"tvg-id": "NFL.Dummy.us", "logo": "https://i.postimg.cc/PxPjQGjk/nfl-logo-png-seeklogo-168592.png"},
    "NCAAF": {"tvg-id": "NCAA.Football.Dummy.us", "logo": "https://i.postimg.cc/ZqXf2XNt/ncaa-logo-png-seeklogo-184284.png"},
    "NBA": {"tvg-id": "NBA.Basketball.Dummy.us", "logo": "https://i.postimg.cc/2S626CFj/nba-logo-png-seeklogo-247736.png"},
    "NHL": {"tvg-id": "NHL.Hockey.Dummy.us", "logo": "https://i.postimg.cc/CxXHxkxY/nhl-logo-png-seeklogo-534236.png"},
}

def extract_real_m3u8(url: str):
    if "ping.gif" in url and "mu=" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        mu = qs.get("mu", [None])[0]
        if mu:
            return urllib.parse.unquote(mu)
    if ".m3u8" in url:
        return url
    return None

async def scrape_single_tv(context, href, title_raw):
    full_url = BASE_URL + href
    title = " - ".join(line.strip() for line in title_raw.splitlines() if line.strip())
    title = title.replace(",", "")
    stream_url = None
    page = await context.new_page()

    async def handle_response(response):
        nonlocal stream_url
        real = extract_real_m3u8(response.url)
        if real and not stream_url:
            stream_url = real
            print(f"✅ [TV] {title} → {real}")

    page.on("response", handle_response)
    try:
        await page.goto(full_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(random.uniform(2.8, 3.5))
    except Exception as e:
        print(f"⚠️ Failed {title}: {e}")
    await page.close()
    return stream_url

async def scrape_tv_urls():
    urls = []
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        print("🔄 Loading /live-tv channel list...")
        await page.goto(CHANNEL_LIST_URL, wait_until="domcontentloaded", timeout=60000)
        links = await page.locator("ol.list-group a").all()
        hrefs_and_titles = [
            (await l.get_attribute("href"), await l.text_content())
            for l in links if await l.get_attribute("href")
        ]
        await page.close()

        for idx, (href, title_raw) in enumerate(hrefs_and_titles, 1):
            stream = await scrape_single_tv(context, href, title_raw)
            if stream:
                urls.append(stream)
            if idx % 10 == 0:
                print("⏳ Cooling down Firefox after 10 pages...")
                await asyncio.sleep(random.uniform(3.0, 4.5))

        await browser.close()
    return urls

def clean_m3u_header(lines):
    lines = [l for l in lines if not l.strip().startswith("#EXTM3U")]
    ts = int(datetime.utcnow().timestamp())
    lines.insert(
        0,
        f'#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz" # Updated: {ts}'
    )
    return lines

def replace_urls_only(lines, new_urls):
    replaced = []
    url_idx = 0
    for line in lines:
        if line.strip().startswith("http") and url_idx < len(new_urls):
            replaced.append(new_urls[url_idx])
            url_idx += 1
        else:
            replaced.append(line)
    return replaced

def remove_sd_entries(lines):
    cleaned = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if line.strip().startswith("#EXTINF") and "SD" in line.upper():
            skip_next = True
            continue
        cleaned.append(line)
    return cleaned

def replace_sports_section(lines, sports_urls):
    cleaned = []
    skip_next = False
    sports_groups = tuple(f'TheTV - {s}' for s in SECTIONS_TO_APPEND.values())
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        if any(group in line for group in sports_groups):
            skip_next = True
            continue
        cleaned.append(line)
    for url, group, title in sports_urls:
        title = title.replace(",", "").strip() + " HD"
        meta = SPORTS_METADATA.get(group, {})
        extinf = (
            f'#EXTINF:-1 tvg-id="{meta.get("tvg-id","")}" '
            f'tvg-name="{title}" tvg-logo="{meta.get("logo","")}" '
            f'group-title="TheTV - {group}",{title}'
        )
        cleaned.append(extinf)
        cleaned.append(url)
    return cleaned

async def scrape_all_sports_sections():
    all_urls = []
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        for section_path, group_name in SECTIONS_TO_APPEND.items():
            try:
                page = await context.new_page()
                section_url = BASE_URL + section_path
                print(f"\n📁 Loading section: {section_url}")
                await page.goto(section_url, wait_until="domcontentloaded", timeout=60000)
                links = await page.locator("ol.list-group a").all()
                for idx, link in enumerate(links, 1):
                    href = await link.get_attribute("href")
                    title_raw = await link.text_content()
                    if not href or not title_raw:
                        continue
                    title = " - ".join(line.strip() for line in title_raw.splitlines() if line.strip())
                    title = title.replace(",", "")
                    full_url = BASE_URL + href
                    stream_url = None
                    sub = await context.new_page()

                    async def handle_response(response):
                        nonlocal stream_url
                        real = extract_real_m3u8(response.url)
                        if real and not stream_url:
                            stream_url = real
                            print(f"✅ [{group_name}] {title} → {real}")

                    sub.on("response", handle_response)
                    try:
                        await sub.goto(full_url, wait_until="domcontentloaded", timeout=60000)
                        await asyncio.sleep(random.uniform(2.8, 3.5))
                    except Exception as e:
                        print(f"⚠️ {group_name} page failed: {e}")
                    await sub.close()
                    if stream_url:
                        all_urls.append((stream_url, group_name, title))
                    if idx % 8 == 0:
                        print("⏳ Cooling down Firefox to sync responses...")
                        await asyncio.sleep(random.uniform(3.0, 4.5))
                await page.close()
            except Exception as e:
                print(f"⚠️ Skipped {group_name}: {e}")
                continue
        await browser.close()
    return all_urls

async def main():
    if not Path(M3U8_FILE).exists():
        print(f"❌ File not found: {M3U8_FILE}")
        return
    lines = Path(M3U8_FILE).read_text(encoding="utf-8").splitlines()
    lines = clean_m3u_header(lines)
    print("🔧 Updating TV URLs only...")
    new_urls = await scrape_tv_urls()
    if new_urls:
        lines = replace_urls_only(lines, new_urls)
    print("🧹 Removing SD entries...")
    lines = remove_sd_entries(lines)
    print("⚽ Replacing Sports Sections...")
    sports_urls = await scrape_all_sports_sections()
    if sports_urls:
        lines = replace_sports_section(lines, sports_urls)
    Path(M3U8_FILE).write_text("\n".join(lines), encoding="utf-8")
    print("✅ Done — SD removed, URLs replaced, old sports wiped, new ones appended.")

if __name__ == "__main__":
    asyncio.run(main())
