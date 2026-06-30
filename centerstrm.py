from pathlib import Path
from urllib.parse import urljoin
import os
import asyncio
from collections import defaultdict

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "STRMCNTR"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)
API_FILE = Cache(f"{TAG.lower()}-api.json", exp=7_200)

OUTPUT_FILE = Path("centerstrm.m3u")

# API URL FROM SECRET
BASE_URL = os.environ["CENTERSTRM_API"]
EMBED_BASE = "https://streams.center/"

CATEGORIES = {
    #4: "Basketball",
    #9: "Football",
    9: "FIFA World Cup",,
    #13: "Baseball",
    13: "MLB",
    #14: "American Football",
    15: "Motor Sport",
    #16: "Hockey",
    17: "Fight MMA",
    18: "Boxing",
    19: "NCAA Sports",
    20: "WWE",
    21: "Tennis",
}

UA_ENC = (
    "Mozilla%2F5.0%20(Windows%20NT%2010.0%3B%20Win64%3B%20x64)"
    "%20AppleWebKit%2F537.36%20(KHTML%2C%20like%20Gecko)"
    "%20Chrome%2F144.0.0.0%20Safari%2F537.36"
)


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
            f'|referer=https://streams.center/'
            f'|origin=https://streams.center'
            f'|user-agent={UA_ENC}'
        )
        ch += 1

    return "\n".join(lines) + "\n"


# -------------------------------------------------
# API EVENT DISCOVERY
# -------------------------------------------------
async def get_events(cached_ids: set[str]) -> list[dict]:
    now = Time.clean(Time.now())
    
    # Track language variants to avoid duplicates
    event_counter = defaultdict(int)
    
    api_data = API_FILE.load(per_entry=False, index=-1)
    if not api_data:
        log.info("Refreshing API cache")
        if r := await network.request(
            BASE_URL,
            log=log,
            params={"pageNumber": 1, "pageSize": 500},
        ):
            api_data = r.json()
            API_FILE.write(api_data)
        else:
            return []

    events = []
    
    PRE_START = 12  # Increased from 6 to catch more upcoming events
    POST_END = 4    # Increased from 2 to catch events that just ended

    for row in api_data:
        event_id = row.get("id")
        name = row.get("gameName")
        category_id = row.get("categoryId")
        embed = row.get("videoUrl")
        begin = row.get("beginPartie")
        end = row.get("endPartie")

        if not all([event_id, name, category_id, embed, begin, end]):
            continue

        if str(event_id) in cached_ids:
            continue

        sport = CATEGORIES.get(category_id)
        if not sport:
            continue

        start_dt = Time.from_str(begin, timezone="CET")
        end_dt = Time.from_str(end, timezone="CET")

        # Extended time window to catch more events
        if not (
            start_dt.delta(hours=-PRE_START)
            <= now
            <= end_dt.delta(hours=POST_END)
        ):
            continue

        # Parse embed URLs - handle multiple streams
        embed_urls = []
        if ";" in embed:
            # Multiple streams separated by semicolon
            for stream_entry in embed.split(";"):
                if "<" in stream_entry:
                    url, lang = stream_entry.split("<", 1)
                    embed_urls.append((url.strip(), lang.strip()))
                else:
                    embed_urls.append((stream_entry.strip(), "Main"))
        else:
            # Single stream
            if "<" in embed:
                url, lang = embed.split("<", 1)
                embed_urls.append((url.strip(), lang.strip()))
            else:
                embed_urls.append((embed.strip(), "Main"))

        for url, lang in embed_urls:
            if not url.startswith("http"):
                url = urljoin(EMBED_BASE, url)
            
            # Create unique event name with language/counter
            event_key = f"{name}|{lang}"
            event_counter[event_key] += 1
            
            event_name = f"{name} ({lang})" if lang != "Main" else name
            if event_counter[event_key] > 1:
                event_name = f"{event_name} #{event_counter[event_key]}"

            events.append(
                {
                    "id": f"{event_id}_{lang}_{event_counter[event_key]}",
                    "sport": sport,
                    "event": event_name,
                    "embed": url,
                    "timestamp": start_dt.timestamp(),
                    "original_id": str(event_id),
                    "language": lang,
                }
            )

    # Sort by timestamp to process oldest first
    events.sort(key=lambda x: x["timestamp"])
    
    return events


# -------------------------------------------------
# MAIN SCRAPER
# -------------------------------------------------
async def scrape() -> None:
    cached = CACHE_FILE.load()
    cached_ids = set()
    
    # Track both original IDs and generated IDs
    for key in cached.keys():
        # Extract original event ID if possible
        if "_" in key:
            orig_id = key.split("_")[0]
            cached_ids.add(orig_id)
        cached_ids.add(key)

    log.info(f"Loaded {len(cached)} cached events")

    events = await get_events(cached_ids)
    log.info(f"Found {len(events)} live/upcoming API events")

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
                for i, ev in enumerate(events, start=1):
                    async with network.event_page(context) as page:
                        try:
                            stream = await network.process_event(
                                page=page,
                                url=ev["embed"],
                                url_num=i,
                                timeout=20,
                                log=log,
                            )
                        except Exception as e:
                            log.error(f"URL {i}) Failed: {e}")
                            continue

                        if not stream:
                            continue

                        tvg_id, logo = leagues.get_tvg_info(
                            ev["sport"], ev["event"]
                        )

                        # Use unique ID for caching
                        cache_key = ev["id"]
                        
                        cached[cache_key] = {
                            "name": f"[{ev['sport']}] {ev['event']} ({TAG})",
                            "url": stream,
                            "logo": logo,
                            "timestamp": ev["timestamp"],
                            "id": tvg_id or "Live.Event.us",
                            "language": ev.get("language", "Main"),
                            "original_id": ev.get("original_id", ev["id"]),
                        }

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
