from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qsl
import os
import asyncio
import re

from playwright.async_api import async_playwright
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "STRMCNTR"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)
OUTPUT_FILE = Path("centerstrm.m3u")

# API URL FROM SECRET
BASE_URL = os.environ["CENTERSTRM_API"]
EMBED_BASE = "https://streame.center/"

UA_ENC = (
    "Mozilla%2F5.0%20(Windows%20NT%2010.0%3B%20Win64%3B%20x64)"
    "%20AppleWebKit%2F537.36%20(KHTML%2C%20like%20Gecko)"
    "%20Chrome%2F144.0.0.0%20Safari%2F537.36"
)

CATEGORIES = {
    4: "Basketball",
    9: "Football",
    13: "Baseball",
    14: "American Football",
    15: "Motor Sport",
    16: "Hockey",
    17: "Fight MMA",
    18: "Boxing",
    19: "NCAA Sports",
    20: "WWE",
    21: "Tennis",
    22: "Soccer",
    23: "MMA",
    24: "Volleyball",
}


def cleanup(s: str) -> str:
    """Clean text by keeping ASCII characters from the last part after —"""
    return "".join(i for i in s.split("—")[-1] if i.isascii()).strip()


async def process_event(url: str, url_num: int) -> str | None:
    """Process a single event to extract the stream URL"""
    try:
        if not (html_data := await network.request(url, log=log)):
            log.warning(f"URL {url_num}) Failed to load url.")
            return None

        soup = HTMLParser(html_data.content)

        iframe = soup.css_first("iframe")
        if not iframe or not (src := iframe.attributes.get("src")):
            log.warning(f"URL {url_num}) No iframe element found.")
            return None

        # Parse the iframe URL to extract stream ID
        splits = urlsplit(src)
        params = dict(parse_qsl(splits.query))
        
        if not (stream_id := params.get("stream")):
            log.warning(f"URL {url_num}) No stream ID found.")
            return None

        stream_url = f"https://edgestream2.pro/hls/{stream_id}.m3u8"
        log.info(f"URL {url_num}) Captured M3U8")
        
        return stream_url
        
    except Exception as e:
        log.error(f"URL {url_num}) Error processing: {str(e)[:50]}")
        return None


async def get_events(cached_ids: set[str]) -> list[dict]:
    """Get events by parsing HTML from the website"""
    now = Time.clean(Time.now())
    events = []

    # Fetch the game cards embed page
    if not (html_data := await network.request(
        urljoin(BASE_URL, "game-cards/embed"),
        log=log,
    )):
        log.error("Failed to load game cards")
        return events

    try:
        soup = HTMLParser(html_data.content)
    except Exception as e:
        log.error(f"Failed to parse HTML: {str(e)[:50]}")
        return events

    # Parse events from the page
    for card in soup.css(".game-card-group"):
        if not (sport_elem := card.css_first("h2")):
            continue

        sport = cleanup(sport_elem.text(strip=True))
        
        # Map sport names to category IDs if needed
        category_id = None
        for cat_id, cat_name in CATEGORIES.items():
            if cat_name.lower() in sport.lower():
                category_id = cat_id
                break

        for game in card.css(".game-card-row"):
            if not (name_elem := game.css_first("h3")):
                continue

            # Get event time
            if not (event_time_elem := game.css_first(".game-card-when > time")):
                continue

            if not (event_time := event_time_elem.attributes.get("datetime")):
                continue

            # Parse the event time
            try:
                event_dt = Time.fromisoformat(event_time).to_tz("EST")
            except Exception as e:
                log.debug(f"Failed to parse time: {str(e)[:30]}")
                continue

            # Check if event is today
            if event_dt.date() != now.date():
                continue

            event_name = name_elem.text(strip=True)

            # Process each stream source
            for source in game.css(".game-card-source > a.game-card-open-link"):
                if not (href := source.attributes.get("href")):
                    continue

                lang = source.text(strip=True)
                
                # Create unique event ID
                event_id = f"{sport}_{event_name}_{lang}".replace(" ", "_")
                
                # Skip if already cached
                if event_id in cached_ids:
                    continue

                # Build full URL
                if not href.startswith("http"):
                    href = urljoin(BASE_URL, href)

                events.append({
                    "id": event_id,
                    "sport": sport,
                    "event": f"{event_name} | {lang}" if lang else event_name,
                    "link": href,
                    "timestamp": now.timestamp(),
                    "category_id": category_id,
                    "language": lang,
                })

    log.info(f"Found {len(events)} events from HTML")
    return events


# -------------------------------------------------
# PLAYLIST BUILDER
# -------------------------------------------------
def build_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]
    ch = 1

    for e in data.values():
        name = e["name"]

        lines.append(
            f'#EXTINF:-1 tvg-chno="{ch}" '
            f'tvg-id="{e["id"]}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )

        lines.append(
            f'{e["url"]}'
            f'|referer=https://streame.center/'
            f'|origin=https://streame.center'
            f'|user-agent={UA_ENC}'
        )
        ch += 1

    return "\n".join(lines) + "\n"


# -------------------------------------------------
# MAIN SCRAPER
# -------------------------------------------------
async def scrape() -> None:
    cached = CACHE_FILE.load() or {}
    cached_ids = set(cached.keys())

    log.info(f"Loaded {len(cached)} cached events")

    events = await get_events(cached_ids)
    log.info(f"Found {len(events)} live/upcoming events")

    if not events:
        OUTPUT_FILE.write_text(build_playlist(cached), encoding="utf-8")
        log.info(f"Wrote {len(cached)} entries to centerstrm.m3u")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        try:
            async with network.event_context(browser, stealth=False) as context:
                processed = 0
                failed = 0
                
                for i, ev in enumerate(events, start=1):
                    async with network.event_page(context) as page:
                        try:
                            stream = await network.process_event(
                                page=page,
                                url=ev["link"],
                                url_num=i,
                                timeout=20,
                                log=log,
                            )
                        except Exception as e:
                            log.error(f"URL {i}) Failed: {e}")
                            failed += 1
                            continue

                        if not stream:
                            failed += 1
                            continue

                        # Get TVG info
                        tvg_id, logo = leagues.get_tvg_info(
                            ev["sport"], ev["event"]
                        )

                        # Use the event ID as cache key
                        cache_key = ev["id"]
                        
                        cached[cache_key] = {
                            "name": f"[{ev['sport']}] {ev['event']} ({TAG})",
                            "url": stream,
                            "logo": logo or "",
                            "timestamp": ev["timestamp"],
                            "id": tvg_id or "Live.Event.us",
                            "language": ev.get("language", ""),
                        }
                        processed += 1

                log.info(f"Successfully processed {processed} events, {failed} failed")

        finally:
            await browser.close()

    CACHE_FILE.write(cached)
    OUTPUT_FILE.write_text(build_playlist(cached), encoding="utf-8")

    log.info(f"Wrote {len(cached)} entries to centerstrm.m3u")


# -------------------------------------------------
# ENTRY POINT
# -------------------------------------------------
if __name__ == "__main__":
    log.info("Starting StreamCenter updater...")
    asyncio.run(scrape())
