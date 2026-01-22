import os
import re
import sys
import asyncio
from functools import partial
from urllib.parse import quote

from selectolax.parser import HTMLParser
from playwright.async_api import async_playwright

# Fix imports when running as script
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

urls: dict[str, dict] = {}


# ---------------- JS RENDER FIX ----------------

async def fetch_rendered_html() -> str:
    log.info("🌐 Launching browser for JS-rendered HTML")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=USER_AGENT_RAW,
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector(".ch-date", timeout=20000)

        html = await page.content()

        await browser.close()
        return html


async def process_event(url: str, idx: int) -> str | None:
    r = await network.request(url, log=log)
    if not r:
        return None

    data = r.json()
    urls = data.get("urls")
    if not urls:
        return None

    return urls[0]


async def scrape_events() -> dict:
    html = await fetch_rendered_html()
    soup = HTMLParser(html)

    pattern = re.compile(r"openEmbed\('([^']+)'\)", re.I)
    events = {}

    for row in soup.css(".channel"):
        date_node = row.css_first(".ch-date")
        cat_node = row.css_first(".ch-category")
        name_node = row.css_first(".ch-name")
        btn = row.css_first("a.hd-link.secondary")

        if not all([date_node, cat_node, name_node, btn]):
            continue

        onclick = btn.attributes.get("onclick", "")
        m = pattern.search(onclick)
        if not m:
            continue

        event_dt = Time.from_str(date_node.text(strip=True), timezone="EST")
        sport = cat_node.text(strip=True)
        event = name_node.text(strip=True)

        api = m.group(1).replace("player.php", "get-stream.php")

        key = f"[{sport}] {event} ({TAG})"
        events[key] = {
            "sport": sport,
            "event": event,
            "link": api,
            "event_ts": event_dt.timestamp(),
        }

    log.info(f"📺 Parsed {len(events)} events from rendered DOM")
    return events


def build_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]

    for title, ev in sorted(data.items(), key=lambda x: x[1]["event_ts"]):
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
    urls.update(cached)

    events = await scrape_events()
    log.info(f"Processing {len(events)} events")

    for i, ev in enumerate(events.values(), 1):
        stream = await network.safe_process(
            partial(process_event, ev["link"], i),
            url_num=i,
            semaphore=network.HTTP_S,
            log=log,
        )

        if not stream:
            continue

        ev["url"] = stream
        key = f"[{ev['sport']}] {ev['event']} ({TAG})"
        urls[key] = ev

    CACHE_FILE.write(urls)

    playlist = build_playlist(urls)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(playlist)

    log.info(f"✅ Saved {OUTPUT_FILE} ({len(urls)} entries)")


if __name__ == "__main__":
    asyncio.run(main())
