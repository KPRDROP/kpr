import asyncio
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger

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

LOGO_MAP = {
    "Soccer.Dummy.us": "https://i.postimg.cc/vH3CwsWN/hd-yellow-and-black-classic-football-soccer-ball-png-7040816948787976l8sriy2gf.png",
    "NBA.Basketball.Dummy.us": "https://i.postimg.cc/vBt37rhW/c-HJpdm-F0ZS9sci9pb-WFn-ZXMvd2Vic2l0ZS8y-MDIz-LTA1L2pv-Yjk2My1h-LTEw-MS1w-Ln-Bu-Zw.jpg",
    "NHL.Hockey.Dummy.us": "https://i.postimg.cc/Vvb5QRhM/field-hockey-male-player-11548793248f7jrqkhesn.png",
    "Sports.Dummy.us": "https://i.postimg.cc/Bnh8FX2S/sporting-goods-volleyball-coach-badminton.jpg",
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
            f'tvg-logo="{info["logo"]}" '
            f'group-title="{info["group"]}",{info["name"]}'
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
# Capture m3u8
# -------------------------------------------------

async def capture_stream(page, url, index):
    stream_url = None

    def handle_response(response):
        nonlocal stream_url
        if ".m3u8" in response.url and not stream_url:
            stream_url = response.url

    page.on("response", handle_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(4000)
    except Exception:
        log.warning(f"URL {index}) navigation timeout — skipping")
        return None

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

            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            events = []

            # Get all match cards globally
            cards = await page.locator(".match-card").all()
            log.info(f"Detected {len(cards)} match cards")

            for card in cards:

                # 🔥 Find closest previous category-title
                category_locator = card.locator(
                    "xpath=preceding::div[contains(@class,'category-title')][1]"
                )

                if await category_locator.count() > 0:
                    category_name = (await category_locator.inner_text()).strip()
                else:
                    category_name = "Other Sports"

                link = await card.locator("a.match-content").get_attribute("href")
                if not link:
                    continue

                teams = await card.locator(".team-name").all_text_contents()
                if len(teams) < 2:
                    continue

                team1 = teams[0].strip()
                team2 = teams[1].strip()

                title = f"[{category_name}] {team1} vs {team2} ({TAG})"

                events.append({
                    "id": link,
                    "title": title,
                    "url": link,
                    "category": category_name,
                })

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

                category = ev["category"]

                cached[ev["id"]] = {
                    "name": ev["title"],
                    "url": stream,
                    "timestamp": now_ts,
                    "id": TVG_MAP.get(category, "Live.Event.us"),
                    "logo": LOGO_MAP.get(category, ""),
                    "group": category,
                }

        finally:
            await browser.close()

    CACHE_FILE.write(cached)
    OUTPUT_FILE.write_text(build_playlist(cached), encoding="utf-8")

    log.info(f"Successfully wrote {len(cached)} entries to powerstrm.m3u8")


if __name__ == "__main__":
    asyncio.run(scrape())
