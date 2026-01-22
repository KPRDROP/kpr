import os
import re
import sys
import asyncio
from urllib.parse import quote

from selectolax.parser import HTMLParser
from playwright.async_api import async_playwright

# Fix imports when run as script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "SHARK"

BASE_URL = os.getenv("SHARK_BASE_URL")
if not BASE_URL:
    raise RuntimeError("❌ SHARK_BASE_URL secret not set")

OUTPUT_FILE = "strmshark_tivimate.m3u8"

USER_AGENT_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
USER_AGENT = quote(USER_AGENT_RAW, safe="")

CACHE_FILE = Cache("shark.json", exp=10800)


# ---------------- JS RENDER ----------------

async def fetch_rendered_html() -> str:
    log.info("🌐 Launching browser for JS-rendered HTML")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(user_agent=USER_AGENT_RAW)
        page = await context.new_page()

        await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_selector("a.hd-link.secondary", timeout=20000)

        html = await page.content()
        await browser.close()
        return html


async def fetch_stream(api_url: str) -> str | None:
    r = await network.request(api_url, log=log)
    if not r:
        return None

    data = r.json()
    urls = data.get("urls")
    if not urls:
        return None

    return urls[0]


# ---------------- SCRAPER ----------------

async def scrape_events() -> dict:
    html = await fetch_rendered_html()
    soup = HTMLParser(html)

    events = {}

    for btn in soup.css("a.hd-link.secondary"):
        onclick = btn.attributes.get("onclick", "")
        m = re.search(r"openEmbed\('([^']+)'\)", onclick)
        if not m:
            continue

        player_url = m.group(1)
        api_url = player_url.replace("player.php", "get-stream.php")

        # Walk UP to find metadata
        parent = btn.parent
        for _ in range(6):
            if not parent:
                break

            date_node = parent.css_first(".ch-date")
            cat_node = parent.css_first(".ch-category")
            name_node = parent.css_first(".ch-name")

            if date_node and cat_node and name_node:
                break

            parent = parent.parent
        else:
            continue

        event_dt = Time.from_str(date_node.text(strip=True), timezone="EST")
        sport = cat_node.text(strip=True)
        event = name_node.text(strip=True)

        key = f"[{sport}] {event} ({TAG})"

        events[key] = {
            "sport": sport,
            "event": event,
            "event_ts": event_dt.timestamp(),
            "api": api_url,
        }

    log.info(f"📺 Parsed {len(events)} events from rendered DOM")
    return events


# ---------------- OUTPUT ----------------

def build_playlist(events: dict) -> str:
    lines = ["#EXTM3U"]

    for title, ev in sorted(events.items(), key=lambda x: x[1]["event_ts"]):
        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        name = f"[{ev['sport']}] {ev['event']} ({TAG})"

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id or "Live.Event.us"}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{logo}" '
            f'group-title="Live Events",{name}'
        )

        lines.append(
            f'{ev["url"]}'
            f'|referer={BASE_URL}'
            f'|origin={BASE_URL}'
            f'|user-agent={USER_AGENT}'
        )

    return "\n".join(lines) + "\n"


async def main():
    cached = CACHE_FILE.load() or {}
    log.info(f"Loaded {len(cached)} cached events")

    events = await scrape_events()
    log.info(f"Processing {len(events)} events")

    for k, ev in events.items():
        stream = await fetch_stream(ev["api"])
        if not stream:
            continue

        ev["url"] = stream
        cached[k] = ev

    CACHE_FILE.write(cached)

    playlist = build_playlist(cached)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(playlist)

    log.info(f"✅ Saved {OUTPUT_FILE} ({len(cached)} entries)")


if __name__ == "__main__":
    asyncio.run(main())
