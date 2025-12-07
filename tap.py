import asyncio
import urllib.parse
import random
import re
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

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
    if not href:
        return ""
    try:
        path = urllib.parse.urlparse(href).path
        last = path.rstrip("/").split("/")[-1]
    except Exception:
        last = href
    last = last.lower()
    last = re.sub(r'[^a-z0-9\-_]+', '-', last).strip('-')
    return last

# --- SCRAPING: TV CHANNEL LIST & PER-CHANNEL M3U8 CAPTURE ---
async def scrape_tv_urls(max_channels=None):
    """
    Return list of dicts:
      {
        "url": <m3u8>,
        "title": <clean title>,
        "logo": <absolute logo url or empty>,
        "tvg_id": <derived id or empty>
      }
    max_channels: optional int to limit how many channels we process (useful for testing)
    """
    results = []
    # timeouts
    LIST_GOTO_TIMEOUT = 25000
    CHANNEL_GOTO_TIMEOUT = 25000
    PER_CHANNEL_TOTAL_TIMEOUT = 28  # seconds (wraps the full per-channel work)

    async with async_playwright() as p:
        # Add no-sandbox flags which are needed in some CI environments
        browser = await p.firefox.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()

        print("🔄 Loading /tv channel list...")
        try:
            await page.goto(CHANNEL_LIST_URL, wait_until="domcontentloaded", timeout=LIST_GOTO_TIMEOUT)
        except PlaywrightTimeoutError:
            print("⚠️ Timeout loading channel list; continuing with whatever we have.")
        except Exception as e:
            print(f"❌ Failed to load channel list: {e}")
            await browser.close()
            return results

        # gather anchor elements
        try:
            anchors = await page.locator("ol.list-group a").all()
        except Exception as e:
            print(f"⚠️ Could not find channel anchors: {e}")
            anchors = []

        entries = []
        for a in anchors:
            try:
                href = await a.get_attribute("href")
                if not href:
                    continue
                title_raw = (await a.text_content()) or ""
                title = " - ".join(line.strip() for line in title_raw.splitlines() if line.strip())
                title = title.replace(",", "")
                logo = ""
                try:
                    img_el = a.locator("img").first
                    if await img_el.count() > 0:
                        img_src = await img_el.get_attribute("src")
                        if img_src:
                            logo = img_src
                            if logo.startswith("/"):
                                logo = BASE_URL.rstrip("/") + logo
                except Exception:
                    logo = ""
                tvg_id_attr = await a.get_attribute("data-tvg-id") or ""
                if not tvg_id_attr:
                    tvg_id_attr = derive_tvg_id_from_href(href)

                entries.append({
                    "href": href,
                    "title": title,
                    "logo": logo,
                    "tvg_id": tvg_id_attr
                })
            except Exception as e:
                print(f"⚠️ Skipping an anchor due to error: {e}")
                continue

        await page.close()

        total = len(entries)
        print(f"ℹ️ Found {total} channel links (will process up to {max_channels or 'all'}).")

        for idx, entry in enumerate(entries, start=1):
            if max_channels and idx > max_channels:
                break

            stream_url = None

            async def per_channel_work():
                nonlocal stream_url
                page = await context.new_page()
                handler = None
                try:
                    # attach response handler and keep reference so we can remove later
                    def on_response(resp):
                        nonlocal stream_url
                        try:
                            url = resp.url
                            real = extract_real_m3u8(url)
                            if real and not stream_url:
                                stream_url = real
                                print(f"✅ [TV] {entry['title']} → {real}")
                        except Exception:
                            pass

                    handler = on_response
                    page.on("response", handler)

                    full = BASE_URL + entry["href"]
                    try:
                        await page.goto(full, wait_until="domcontentloaded", timeout=CHANNEL_GOTO_TIMEOUT)
                    except PlaywrightTimeoutError:
                        print(f"⚠️ Channel page goto timeout: {entry['title']}")
                    except Exception as e:
                        print(f"⚠️ Error opening channel page {entry['title']}: {e}")

                    # short sleeps to let any player calls happen
                    await asyncio.sleep(random.uniform(1.4, 2.2))
                    try:
                        # try some interaction to trigger network requests (best-effort)
                        await page.locator("body").click(timeout=800, force=True)
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(0.6, 1.2))
                finally:
                    # cleanup handler & page
                    try:
                        if handler:
                            page.off("response", handler)
                    except Exception:
                        # page.off may not be supported on older playwright versions; ignore safely
                        try:
                            page.remove_listener("response", handler)
                        except Exception:
                            pass
                    try:
                        await page.close()
                    except Exception:
                        pass

            try:
                # run per-channel work with an overall timeout
                await asyncio.wait_for(per_channel_work(), timeout=PER_CHANNEL_TOTAL_TIMEOUT)
            except asyncio.TimeoutError:
                print(f"⚠️ Per-channel processing timed out: {entry['title']}")
            except Exception as e:
                print(f"⚠️ Per-channel unexpected error: {entry['title']}: {e}")

            if stream_url:
                entry["url"] = stream_url
                results.append(entry)
            else:
                print(f"⚠️ No m3u8 captured for {entry['title']}")

            # cooldown periodically
            if idx % 10 == 0:
                print("⏳ Cooling down Firefox...")
                await asyncio.sleep(random.uniform(2.2, 3.8))

        await browser.close()

    return results

# --- Sports sections (similar to your previous implementation) ---
async def scrape_all_sports_sections(max_per_section=None):
    """
    Returns list of tuples (stream_url, group_name, title)
    Fixed: collect href/title strings while section page is open, then close page
    and visit each item with a fresh page (avoids using Locator objects after page.close()).
    """
    all_urls = []
    SECTION_GOTO_TIMEOUT = 25000
    PER_ITEM_TIMEOUT = 18

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = await browser.new_context()
        for section_path, group_name in SECTIONS_TO_APPEND.items():
            try:
                page = await context.new_page()
                section_url = BASE_URL + section_path
                print(f"\n📁 Loading section: {section_url}")
                try:
                    await page.goto(section_url, wait_until="domcontentloaded", timeout=SECTION_GOTO_TIMEOUT)
                except PlaywrightTimeoutError:
                    print(f"⚠️ Timeout loading section {group_name}; continuing with what we can.")
                except Exception as e:
                    print(f"⚠️ Error loading section page {group_name}: {e}")

                # Collect hrefs/titles into a plain list (no locators kept)
                items = []
                try:
                    anchors = await page.locator("ol.list-group a").all()
                    for a in anchors:
                        try:
                            href = await a.get_attribute("href")
                            title_raw = await a.text_content() or ""
                            if not href or not title_raw:
                                continue
                            title = " - ".join(line.strip() for line in title_raw.splitlines() if line.strip())
                            title = title.replace(",", "")
                            items.append({"href": href, "title": title})
                        except Exception as e:
                            # skip problematic anchor but continue
                            print(f"⚠️ Skipping an anchor in {group_name}: {e}")
                            continue
                except Exception as e:
                    print(f"⚠️ Failed to enumerate anchors in {group_name}: {e}")
                    items = []

                # close the section page now that we have plain data
                try:
                    await page.close()
                except Exception:
                    pass

                # iterate collected items and open new pages to capture m3u8
                for i, item in enumerate(items, start=1):
                    if max_per_section and i > max_per_section:
                        break

                    href = item["href"]
                    title = item["title"]
                    full_url = BASE_URL + href
                    stream_url = None

                    async def work_item():
                        nonlocal stream_url
                        sub = await context.new_page()
                        handler = None
                        try:
                            def on_resp(resp):
                                nonlocal stream_url
                                try:
                                    real = extract_real_m3u8(resp.url)
                                    if real and not stream_url:
                                        stream_url = real
                                        print(f"✅ [{group_name}] {title} → {real}")
                                except Exception:
                                    pass

                            handler = on_resp
                            sub.on("response", handler)
                            try:
                                await sub.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                            except PlaywrightTimeoutError:
                                # ignore individual goto timeouts; continue to allow network requests
                                pass
                            except Exception:
                                pass

                            await asyncio.sleep(random.uniform(1.2, 2.4))
                        finally:
                            # remove handler and close sub page cleanly
                            try:
                                if handler:
                                    sub.off("response", handler)
                            except Exception:
                                try:
                                    sub.remove_listener("response", handler)
                                except Exception:
                                    pass
                            try:
                                await sub.close()
                            except Exception:
                                pass

                    try:
                        await asyncio.wait_for(work_item(), timeout=PER_ITEM_TIMEOUT)
                    except asyncio.TimeoutError:
                        print(f"⚠️ Timeout for {title} in {group_name}")
                    except Exception as e:
                        print(f"⚠️ Error processing {title} in {group_name}: {e}")

                    if stream_url:
                        all_urls.append((stream_url, group_name, title))

                    # small cooldown every few items
                    if i % 8 == 0:
                        await asyncio.sleep(random.uniform(1.6, 3.5))

            except Exception as e:
                print(f"⚠️ Skipped section {group_name}: {e}")
                continue
        try:
            await browser.close()
        except Exception:
            pass

    return all_urls


# Playlist helpers (unchanged logic, kept robust)
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
    """
    Remove current sports groups and append new ones from sports_urls list of tuples.
    This version also replaces '@' with 'vs' in event titles for nicer output.
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
        # sanitize title, remove commas, normalize whitespace
        safe_title = title.replace(",", "").strip()

        # Replace " @ " and "@" with " vs " (case-sensitive fine for sports titles)
        # Keep spacing tidy: collapse multiple spaces
        safe_title = safe_title.replace(" @ ", " vs ")
        safe_title = safe_title.replace("@", " vs ")
        safe_title = " ".join(safe_title.split())

        # append HD suffix
        safe_title = f"{safe_title} HD"

        meta = SPORTS_METADATA.get(group, {})
        extinf = (
            f'#EXTINF:-1 tvg-id="{meta.get("tvg-id","")}" '
            f'tvg-name="{safe_title}" tvg-logo="{meta.get("logo","")}" '
            f'group-title="TheTV - {group}",{safe_title}'
        )
        cleaned.append(extinf)
        cleaned.append(url)
    return cleaned

def append_missing_tv_channels(lines, tv_entries):
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

# Main orchestration
async def main():
    if not Path(M3U8_FILE).exists():
        print(f"⚠️ {M3U8_FILE} not found — creating template")
        Path(M3U8_FILE).write_text("#EXTM3U\n", encoding="utf-8")

    lines = Path(M3U8_FILE).read_text(encoding="utf-8").splitlines()
    lines = clean_m3u_header(lines)

    print("🔧 Updating TV URLs and metadata...")
    # Set max_channels=None for full run; set an int for testing to limit churn
    tv_entries = await scrape_tv_urls(max_channels=None)
    only_urls = [e["url"] for e in tv_entries if "url" in e]

    if only_urls:
        lines = replace_urls_only(lines, only_urls)

    print("🧹 Removing SD entries...")
    lines = remove_sd_entries(lines)

    print("⚽ Replacing Sports Sections...")
    sports_urls = await scrape_all_sports_sections(max_per_section=40)
    if sports_urls:
        lines = replace_sports_section(lines, sports_urls)

    if tv_entries:
        lines = append_missing_tv_channels(lines, tv_entries)

    Path(M3U8_FILE).write_text("\n".join(lines), encoding="utf-8")
    print("✅ Done — TheTV.m3u8 updated with metadata and streams.")

if __name__ == "__main__":
    asyncio.run(main())
