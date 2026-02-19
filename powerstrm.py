import asyncio
import re
from pathlib import Path
from urllib.parse import quote_plus

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

    for info in data.values():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{info["id"]}" '
            f'tvg-name="{info["name"]}" '
            f'tvg-logo="" '
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
# Capture m3u8 from network
# -------------------------------------------------

async def capture_stream(page, url, index):
    stream_url = None

    def handle_response(response):
        nonlocal stream_url
        if ".m3u8" in response.url:
            stream_url = response.url

    page.on("response", handle_response)

    await page.goto(url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(5000)

    if stream_url:
        log.info(f"URL {index}) captured stream")
    else:
        log.warning(f"URL {index}) no m3u8 captured")

    return stream_url


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
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(5000)

            # Collect ALL match-card links globally
            cards = await page.locator(".match-card a.match-content").all()

            log.info(f"Detected {len(cards)} match cards")

            events = []

            for card in cards:
                href = await card.get_attribute("href")
                if not href:
                    continue

                teams = await card.locator("..").locator(".team-name").all_text_contents()

                if len(teams) < 2:
                    continue

                team1 = teams[0].strip()
                team2 = teams[1].strip()

                # Try detect category by nearby category-title (best guess)
                category = "Other Sports"
                for key in TVG_MAP.keys():
                    if key.lower() in (await page.content()).lower():
                        category = key
                        break

                title = f"[{category}] {team1} at {team2} ({TAG})"

                events.append(
                    {
                        "id": href,
                        "title": title,
                        "url": href,
                        "category": category,
                    }
                )

            log.info(f"Found {len(events)} events")

            if not events:
                OUTPUT_FILE.write_text(build_playlist(cached), encoding="utf-8")
                log.info(f"Wrote {len(cached)} cached entries")
                return

            now_ts = Time.clean(Time.now()).timestamp()

            for i, ev in enumerate(events, start=1):
                stream = await capture_stream(page, ev["url"], i)
                if not stream:
                    continue

                cached[ev["id"]] = {
                    "name": ev["title"],
                    "url": stream,
                    "timestamp": now_ts,
                    "id": TVG_MAP.get(ev["category"], "Live.Event.us"),
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
