import asyncio
import re
import requests
import logging
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from urllib.parse import quote

# ------------------- Logging -------------------
logging.basicConfig(
    filename="scrape.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S"))
logging.getLogger("").addHandler(console)
log = logging.getLogger("scraper")

# ------------------- Config -------------------
SCHEDULE_URL = "https://sportsonline.sn/prog.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

FALLBACK_LOGOS = {
    "basketball": "https://i.postimg.cc/FHBqZPjF/Basketball5.png",
    "football": "https://i.postimg.cc/FKq4YrPT/Rv-N0XSF.png",
    "nba": "https://i.postimg.cc/FHBqZPjF/Basketball5.png",
    "ufc": "https://i.postimg.cc/1Xr2rsKc/Combat-Sports.png",
    "miscellaneous": "https://i.postimg.cc/1Xr2rsKc/Combat-Sports.png",
}

TV_IDS = {
    "basketball": "Basketball.Dummy.us",
    "football": "Soccer.Dummy.us",
    "nba": "NBA.Dummy.us",
    "ufc": "UFC.Dummy.us",
    "miscellaneous": "Sports.Dummy.us",
}

CATEGORY_KEYWORDS = {
    "NBA": "NBA",
    "UFC": "UFC",
    "Football": "Football",
    "Soccer": "Football",
}

CONCURRENT_FETCHES = 4
RETRIES = 3
CLICK_WAIT = 3

# ------------------- Helpers -------------------
def strip_non_ascii(text: str) -> str:
    return re.sub(r"[^\x00-\x7F]+", "", text) if text else ""

def fetch_schedule():
    try:
        log.info(f"🌐 Fetching schedule from {SCHEDULE_URL}")
        r = requests.get(SCHEDULE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        log.error(f"❌ Failed to fetch schedule: {e}")
        return ""

def parse_schedule(raw):
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.startswith("="):
            continue
        try:
            time_part, rest = line.split("   ", 1)
            if " | " in rest:
                title, link = rest.rsplit(" | ", 1)
            else:
                parts = rest.rsplit(" ", 1)
                title, link = parts[0], parts[-1]
            title = strip_non_ascii(title.strip())
            link = link.strip()
            category = "Miscellaneous"
            for keyword, cat in CATEGORY_KEYWORDS.items():
                if keyword.lower() in title.lower():
                    category = cat
                    break
            events.append({"time": time_part, "title": title, "link": link, "category": category})
        except ValueError:
            continue
    log.info(f"📺 Parsed {len(events)} events from schedule")
    return events

async def extract_m3u8(page, url):
    found = None
    try:
        async def on_request(request):
            nonlocal found
            if ".m3u8" in request.url and not found:
                found = request.url
                log.info(f"  ⚡ Stream found: {found}")

        page.on("request", on_request)

        for attempt in range(1, RETRIES + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(CLICK_WAIT)
                if found:
                    break
            except PlaywrightTimeout:
                log.warning(f"⚠️ Timeout on {url} attempt {attempt}")
            except Exception as e:
                log.warning(f"⚠️ Error on {url} attempt {attempt}: {e}")

        page.remove_listener("request", on_request)

        if not found:
            html = await page.content()
            matches = re.findall(r'https?://[^\s"<>]+\.m3u8(?:\?[^"<>]*)?', html)
            if matches:
                found = matches[0]
                log.info(f"  🕵️ Fallback M3U8: {found}")
        return found
    except Exception as e:
        log.warning(f"⚠️ Failed extracting m3u8 from {url}: {e}")
        return None

async def process_event(event, ctx):
    page = await ctx.new_page()
    url = await extract_m3u8(page, event["link"])
    await page.close()
    return {"title": event["title"], "url": url, "category": event["category"]}

async def generate_playlist():
    raw = fetch_schedule()
    events = parse_schedule(raw)
    if not events:
        log.warning("❌ No events found.")
        return "#EXTM3U\n"

    content = ["#EXTM3U"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome-beta",  # Use system Chrome Beta
        )
        ctx = await browser.new_context(user_agent=USER_AGENT)

        sem = asyncio.Semaphore(CONCURRENT_FETCHES)

        async def worker(ev):
            async with sem:
                return await process_event(ev, ctx)

        results = await asyncio.gather(*[worker(ev) for ev in events])
        await browser.close()

    for r in results:
        if not r["url"]:
            continue
        cat = r["category"].lower()
        logo = FALLBACK_LOGOS.get(cat, FALLBACK_LOGOS["miscellaneous"])
        tv_id = TV_IDS.get(cat, TV_IDS["miscellaneous"])
        title = r["title"]
        content.append(
            f'#EXTINF:-1 tvg-id="{tv_id}" tvg-name="{title}" tvg-logo="{logo}" group-title="{cat}",{title}'
        )
        headers = f"referer=https://dukehorror.net/|origin=https://dukehorror.net|user-agent={ENCODED_USER_AGENT}"
        content.append(f"{r['url']}|{headers}")

    return "\n".join(content)

# ------------------- Main -------------------
if __name__ == "__main__":
    start = datetime.now()
    log.info("🚀 Starting SportsOnline scrape...")
    playlist = asyncio.run(generate_playlist())
    with open("SportsOnline_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(playlist)
    duration = (datetime.now() - start).total_seconds()
    log.info(f"✅ Finished in {duration:.2f} sec | Events: {len(playlist.splitlines())}")
