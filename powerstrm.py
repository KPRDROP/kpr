import asyncio
import re
from pathlib import Path
from urllib.parse import quote_plus, urljoin

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, network

log = get_logger(__name__)

TAG = "POWERSTRM"
BASE_URL = "https://powerstreams.online/"
REFERER = "https://streams.center/"
ORIGIN = "https://streams.center"

CACHE_FILE = Cache("powerstrm.json", exp=10_800)
OUTPUT_FILE = Path("powerstrm.m3u8")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)
UA_ENC = quote_plus(UA)

TVG_MAP = {
    "Football": "Soccer.Dummy.us",
    "Basketball": "NBA.Basketball.Dummy.us",
    "Hockey": "NHL.Hockey.Dummy.us",
    "Other Sports": "Sports.Dummy.us",
}


# -------------------------------------------------
# Build playlist
# -------------------------------------------------

def build_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]
    chno = 1

    for key, info in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{info["id"]}" '
            f'tvg-name="{info["name"]}" '
            f'tvg-logo="{info["logo"]}" '
            f'group-title="Live Events",{info["name"]}'
        )
        lines.append(
            f'{info["url"]}'
            f'|Referer={REFERER}'
            f'|Origin={ORIGIN}'
            f'|User-Agent={UA_ENC}'
        )
        chno += 1

    return "\n".join(lines) + "\n"


# -------------------------------------------------
# Extract events from DOM
# -------------------------------------------------

async def get_events(page):
    events = []

    await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(3000)

    categories = await page.locator(".category-title").all()

    for cat in categories:
        category = (await cat.inner_text()).strip()

        if category not in TVG_MAP:
            continue

        # Go up to parent container
        parent = cat.locator("xpath=..")

        cards = await parent.locator(".match-card").all()

        for card in cards:
            link_el = card.locator("a.match-content")
            href = await link_el.get_attribute("href")

            team_names = await card.locator(".team-name").all_text_contents()
            if len(team_names) < 2:
                continue

            team1 = team_names[0].strip()
            team2 = team_names[1].strip()

            date_raw = await card.locator(".match-date").inner_text()
            date_clean = date_raw.replace(",", "").strip()

            title = f"[{category}] {team1} at {team2} ({TAG})"

            events.append(
                {
                    "id": href,
                    "title": title,
                    "url": href,
                    "category": category,
                    "date": date_clean,
                }
            )

    return events


# -------------------------------------------------
# Extract m3u8 from event page
# -------------------------------------------------

async def extract_stream(context, event_url, index):
    async with network.event_page(context) as page:
        await page.goto(event_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        html = await page.content()

        match = re.search(
            r'(https?:\/\/[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
            html,
            re.IGNORECASE,
        )

        if match:
            stream = match.group(1)
            log.info(f"URL {index}) captured stream")
            return stream

        log.warning(f"URL {index}) no m3u8 found")
        return None


# -------------------------------------------------
# Main scrape
# -------------------------------------------------

async def scrape():
    cached = CACHE_FILE.load() or {}
    log.info(f"Loaded {len(cached)} cached events")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        try:
            async with network.event_context(browser, stealth=False) as context:
                async with network.event_page(context) as page:
                    events = await get_events(page)

                log.info(f"Found {len(events)} events")

                if not events:
                    OUTPUT_FILE.write_text(
                        build_playlist(cached),
                        encoding="utf-8",
                    )
                    log.info(f"Wrote {len(cached)} cached entries")
                    return

                now_ts = Time.clean(Time.now()).timestamp()

                for i, ev in enumerate(events, start=1):
                    stream = await extract_stream(context, ev["url"], i)
                    if not stream:
                        continue

                    cached[ev["id"]] = {
                        "name": ev["title"],
                        "url": stream,
                        "logo": "",
                        "timestamp": now_ts,
                        "id": TVG_MAP.get(ev["category"]),
                    }

        finally:
            await browser.close()

    CACHE_FILE.write(cached)

    OUTPUT_FILE.write_text(build_playlist(cached), encoding="utf-8")
    log.info(f"Successfully wrote {len(cached)} entries to powerstrm.m3u8")


# -------------------------------------------------
# Run
# -------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
