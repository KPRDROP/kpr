import asyncio
import urllib.parse
import random
import re
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright

M3U8_FILE = "TheTV.m3u8"
BASE_URL = "https://thetvapp.to"
CHANNEL_LIST_URL = f"{BASE_URL}/tv"

SECTIONS_TO_APPEND = {
    "/nba": "NBA",
    "/mlb": "MLB",
    "/wnba": "WNBA",
    "/nfl": "NFL",
    "/ncaaf": "NCAAF",
    "/ncaab": "NCAAB",
    "/soccer": "Soccer",
    "/ppv": "PPV",
    "/events": "Events",
    "/nhl": "NHL",
}

SPORTS_METADATA = {
    "MLB": {"tvg-id": "MLB.Baseball.Dummy.us", "logo": "https://i.postimg.cc/sDn8tvsK/major-league-baseball-logo-png-seeklogo-176127.png"},
    "PPV": {"tvg-id": "PPV.EVENTS.Dummy.us", "logo": "https://i.postimg.cc/y8ysVXP9/images-q-tbn-ANd9Gc-R6TUY0RT0w3qp-Hu-KZOesu8U3h4Ut-Y2A8-07Q-s.jpg"},
    "NFL": {"tvg-id": "NFL.Dummy.us", "logo": "https://i.postimg.cc/PxPjQGjk/nfl-logo-png-seeklogo-168592.png"},
    "NCAAF": {"tvg-id": "NCAA.Football.Dummy.us", "logo": "https://i.postimg.cc/ZqXf2XNt/ncaa-logo-png-seeklogo-184284.png"},
    "NBA": {"tvg-id": "NBA.Basketball.Dummy.us", "logo": "https://i.postimg.cc/2S626CFj/nba-logo-png-seeklogo-247736.png"},
    "NHL": {"tvg-id": "NHL.Hockey.Dummy.us", "logo": "https://i.postimg.cc/CxXHxkxY/nhl-logo-png-seeklogo-534236.png"},
}

# Helper: extract real m3u8 urls from responses
def extract_real_m3u8(url: str):
    """
    Patterns:
    - Some responses are ping.gif?mu=<encoded_m3u8>
    - Some responses contain .m3u8 directly
    Return full decoded m3u8 or None
    """
    if not url:
        return None
    if "ping.gif" in url and "mu=" in url:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        mu = qs.get("mu", [None])[0]
        if mu:
            return urllib.parse.unquote(mu)
    if ".m3u8" in url:
        return url
    return None

def derive_tvg_id_from_href(href: str) -> str:
    """
    Derive a simple tvg-id from channel href or title.
    Example: /ch/a-e-east -> a-e-east
    Normalizes to lower-case, alnum and '-'
    """
    if not href:
        return ""
    # take last path component
    try:
        path = urllib.parse.urlparse(href).path
        last = path.rstrip("/").split("/")[-1]
    except Exception:
        last = href
    # sanitize
    last = last.lower()
    last = re.sub(r'[^a-z0-9\-_]+', '-', last).strip('-')
    return last

async def scrape_tv_urls():
    """
    Return list of dicts:
      {
        "url": <m3u8>,
        "title": <clean title>,
        "logo": <absolute logo url or empty>,
        "tvg_id": <derived id or empty>
      }
    """
    results = []

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("🔄 Loading /tv channel list...")
        try:
            await page.goto(CHANNEL_LIST_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"❌ Failed to load channel list: {e}")
            await browser.close()
            return results

        # gather anchor elements under ol.list-group a
        anchors = await page.locator("ol.list-group a").all()
        entries = []
        for a in anchors:
            href = await a.get_attribute("href")
            if not href:
                continue
            title_raw = (await a.text_content()) or ""
            title = " - ".join(line.strip() for line in title_raw.splitlines() if line.strip())
            title = title.replace(",", "")
            # try to get img src inside anchor
            logo = ""
            try:
                img_src = await a.locator("img").first.get_attribute("src")
                if img_src:
                    logo = img_src
                    # convert relative src to absolute
                    if logo.startswith("/"):
                        logo = BASE_URL.rstrip("/") + logo
            except Exception:
                logo = ""

            # some anchors may provide data attributes
            tvg_id_attr = await a.get_attribute("data-tvg-id") or ""
            if not tvg_id_attr:
                tvg_id_attr = derive_tvg_id_from_href(href)

            entries.append({
                "href": href,
                "title": title,
                "logo": logo,
                "tvg_id": tvg_id_attr
            })

        await page.close()

        # Now visit each channel page and capture first m3u8 response
        total = len(entries)
        for idx, entry in enumerate(entries, start=1):
            stream_url = None
            page = await context.new_page()

            async def on_response(resp):
                nonlocal stream_url
                try:
                    real = extract_real_m3u8(resp.url)
                    if real and not stream_url:
                        stream_url = real
                        print(f"✅ [TV] {entry['title']} → {real}")
                except Exception:
                    pass

            page.on("response", on_response)

            full = BASE_URL + entry["href"]
            try:
                await page.goto(full, wait_until="domcontentloaded", timeout=60000)
                # small random delay to allow player to request m3u8
                await asyncio.sleep(random.uniform(2.8, 3.6))
                # extra clicks to force lazy players
                try:
                    await page.locator("body").click(timeout=1000, force=True)
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(0.6, 1.2))
            except Exception as e:
                print(f"⚠️ Failed to open {entry['title']} ({full}): {e}")

            await page.close()

            if stream_url:
                entry["url"] = stream_url
                results.append(entry)

            # cooldown periodically
            if idx % 10 == 0:
                print("⏳ Cooling down Firefox...")
                await asyncio.sleep(random.uniform(3.0, 4.6))

        await browser.close()

    return results

async def scrape_all_sports_sections():
    """
    Returns list of tuples (stream_url, group_name, title)
    This is similar to your prior implementation but uses the extract_real_m3u8 helper.
    """
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

# Playlist helpers
def clean_m3u_header(lines):
    """
    Remove existing #EXTM3U header and insert ours with timestamp.
    """
    lines = [l for l in lines if not l.strip().startswith("#EXTM3U")]
    ts = int(datetime.utcnow().timestamp())
    lines.insert(
        0,
        f'#EXTM3U url-tvg="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz" # Updated: {ts}'
    )
    return lines

def replace_urls_only(lines, new_urls):
    """
    Replace only the URL lines (http...) in the existing file with new_urls in order.
    If new_urls shorter than existing, keep remaining original URLs.
    """
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
    """
    Remove EXTINF entries which mention 'SD' in their title.
    """
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
    """
    Remove current sports groups and append new ones from sports_urls list of tuples.
    """
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

def append_missing_tv_channels(lines, tv_entries):
    """
    Append TV channels not present in the file, with metadata.
    tv_entries is list of dicts as returned by scrape_tv_urls.
    """
    existing_urls = set(l.strip() for l in lines if l.startswith("http"))
    output = lines.copy()

    for entry in tv_entries:
        if entry.get("url") in existing_urls:
            continue
        extinf = (
            f'#EXTINF:-1 tvg-id="{entry.get("tvg_id","")}" '
            f'tvg-name="{entry.get("title","")}" '
            f'tvg-logo="{entry.get("logo","")}" '
            f'group-title="TheTV - Channels",{entry.get("title","")}'
        )
        output.append(extinf)
        output.append(entry.get("url"))
    return output

async def main():
    # Ensure M3U file exists - create a minimal template if missing
    if not Path(M3U8_FILE).exists():
        print(f"⚠️ {M3U8_FILE} not found — creating template")
        Path(M3U8_FILE).write_text("#EXTM3U\n", encoding="utf-8")

    lines = Path(M3U8_FILE).read_text(encoding="utf-8").splitlines()
    lines = clean_m3u_header(lines)

    print("🔧 Updating TV URLs and metadata...")
    tv_entries = await scrape_tv_urls()  # list of dicts
    only_urls = [e["url"] for e in tv_entries if "url" in e]

    if only_urls:
        lines = replace_urls_only(lines, only_urls)

    print("🧹 Removing SD entries...")
    lines = remove_sd_entries(lines)

    print("⚽ Replacing Sports Sections...")
    sports_urls = await scrape_all_sports_sections()
    if sports_urls:
        lines = replace_sports_section(lines, sports_urls)

    # Append any new TV channels (with metadata)
    if tv_entries:
        lines = append_missing_tv_channels(lines, tv_entries)

    Path(M3U8_FILE).write_text("\n".join(lines), encoding="utf-8")
    print("✅ Done — TheTV.m3u8 updated with metadata and streams.")

if __name__ == "__main__":
    asyncio.run(main())
