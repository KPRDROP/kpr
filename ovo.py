import asyncio
import re
import urllib.parse
from functools import partial
from urllib.parse import urljoin
import os

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "VOLOKIT"

CACHE_FILE = Cache(TAG, exp=10_800)
HTML_CACHE = Cache(f"{TAG}-html", exp=28_800)

BASE_URL = "http://volokit.xyz"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)

SPORT_ENDPOINTS = {
    "boxing": "BOXING",
    "mlb": "MLB",
    "nba": "NBA",
    "mls": "MLS",
    "nhl": "NHL",
    "race": "RACE",
    "ufc": "UFC",
}


# =========================
# PLAYLIST GENERATOR
# =========================

def _format_extinf(key: str, entry: dict) -> str:
    tvg_id = entry.get("id", "")
    logo = entry.get("logo", "")
    group = entry.get("sport", "Live")

    return (
        f'#EXTINF:-1 tvg-id="{tvg_id}" '
        f'tvg-logo="{logo}" '
        f'group-title="{group}",{key}'
    )


def generate_vlc_playlist(urls: dict, output_file="ovo_vlc.m3u8"):
    lines = ["#EXTM3U"]
    count = 0

    for key, entry in sorted(urls.items()):
        stream_url = entry.get("url")
        if not stream_url:
            continue

        lines.append(_format_extinf(key, entry))
        lines.append(f"#EXTVLCOPT:http-referrer={BASE_URL}/")
        lines.append(f"#EXTVLCOPT:http-origin={BASE_URL}")
        lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        lines.append(stream_url)
        lines.append("")
        count += 1

    # Ensure directory exists
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    if count > 0:
        log.info(f"Generated {output_file} with {count} events")
    return count


def generate_tivimate_playlist(urls: dict, output_file="ovo_tivimate.m3u8"):
    encoded_ua = urllib.parse.quote(USER_AGENT, safe="")
    lines = ["#EXTM3U"]
    count = 0

    for key, entry in sorted(urls.items()):
        stream_url = entry.get("url")
        if not stream_url:
            continue

        lines.append(_format_extinf(key, entry))

        header_string = (
            f"{stream_url}|"
            f"referer={BASE_URL}/&"
            f"origin={BASE_URL}&"
            f"user-agent={encoded_ua}"
        )

        lines.append(header_string)
        lines.append("")
        count += 1

    # Ensure directory exists
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    if count > 0:
        log.info(f"Generated {output_file} with {count} events")
    return count


def generate_all_playlists(urls: dict):
    vlc_count = generate_vlc_playlist(urls)
    tivimate_count = generate_tivimate_playlist(urls)
    total = vlc_count + tivimate_count
    log.info(f"Generated playlists - VLC: {vlc_count} events, TiviMate: {tivimate_count} events")
    return total


# =========================
# UPDATER LOGIC
# =========================

def fix_event(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split())


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.info(f"URL {url_num}) Failed to load url.")
        return None

    soup = HTMLParser(event_data.content)

    if not (iframe := soup.css_first('iframe[height="100%"]')):
        log.warning(f"URL {url_num}) No iframe element found.")
        return None

    if not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")
        return None

    if not (
        iframe_src_data := await network.request(
            iframe_src,
            headers={"Referer": url},
            log=log,
        )
    ):
        log.info(f"URL {url_num}) Failed to load iframe source.")
        return None

    # CORRECTED PATTERN - regex
    pattern = re.compile(r'(var|const)\s+(\w+)\s*=\s*"([^"]*)"', re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No source found.")
        return None

    captured_url = match.group(3)
    log.info(f"URL {url_num}) Captured M3U8")
    
    return captured_url


async def refresh_html_cache(url: str, sport: str, now: Time):
    events = {}

    if not (html_data := await network.request(url, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    date = now.date()

    if date_node := soup.css_first("tr.date"):
        date = date_node.text(strip=True).replace(",", "")

    for card in soup.css("#events .table .vevent.theevent"):
        if not (href := card.css_first("a").attributes.get("href")):
            continue

        name_node = card.css_first(".teamtd.event")
        time_node = card.css_first(".time")

        if not (name_node and time_node):
            continue

        name = name_node.text(strip=True).replace("@", "vs")
        time = time_node.text(strip=True)

        event_sport = SPORT_ENDPOINTS.get(sport, "Live Events")
        event_name = fix_event(name)

        try:
            event_dt = Time.from_str(f"{date} {time}", timezone="UTC")
        except:
            event_dt = now

        # Ensure href is absolute URL
        if not href.startswith('http'):
            href = urljoin(BASE_URL, href)

        key = f"[{event_sport}] {event_name} ({TAG})"

        events[key] = {
            "sport": event_sport,
            "event": event_name,
            "link": href,
            "event_ts": event_dt.timestamp(),
            "timestamp": now.timestamp(),
        }

    return events


async def get_events(cached_keys):
    now = Time.clean(Time.now())

    if not (events := HTML_CACHE.load()):
        log.info("Refreshing HTML cache")

        sport_urls = {
            sport: urljoin(BASE_URL, f"sport/{sport}")
            for sport in SPORT_ENDPOINTS
        }

        tasks = [
            refresh_html_cache(url, sport, now)
            for sport, url in sport_urls.items()
        ]

        results = await asyncio.gather(*tasks)
        events = {k: v for data in results for k, v in data.items()}

        HTML_CACHE.write(events)

    live = []

    # TIME FILTER — process everything
    for k, v in events.items():
        if k in cached_keys:
            continue

        live.append(v)

    log.info(f"Found {len(live)} new events to process")
    return live


# 🔥 ONLY SHOWING FIXED PARTS — KEEP REST SAME

async def scrape() -> None:
    cached_urls = CACHE_FILE.load() or {}

    # ✅ Keep ONLY valid cached entries
    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}

    valid_count = cached_count = len(valid_urls)

    urls.clear()
    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} valid event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(list(cached_urls.keys()))

    new_valid = 0  # ✅ track real success

    if events:
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=(link := ev["link"]),
                url_num=i,
            )

            stream_url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            if not stream_url:
                continue

            sport, event, ts = (
                ev["sport"],
                ev["event"],
                ev["event_ts"],
            )

            key = f"[{sport}] {event} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            entry = {
                "url": url,
                "logo": logo,
                "base": link,
                "timestamp": ts,
                "id": tvg_id or "Live.Event.us",
                "link": link,
                "sport": sport,
            }

            cached_urls[key] = entry

            if url:
                valid_count += 1
                urls[key] = entry
                log.info(f"Successfully captured URL for: {key}")

    if new_count := valid_count - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")
    else:
        log.info("No new events found")

    # Save updated cache
    CACHE_FILE.write(cached_urls)

    # GENERATE PLAYLIST FILES - Use cached_urls which contains all events
    total_events = generate_all_playlists(cached_urls)
    
    # Verify files were created and have content
    vlc_exists = os.path.exists("ovo_vlc.m3u8")
    tivimate_exists = os.path.exists("ovo_tivimate.m3u8")
    
    if vlc_exists and tivimate_exists:
        vlc_size = os.path.getsize("ovo_vlc.m3u8")
        tivimate_size = os.path.getsize("ovo_tivimate.m3u8")
        log.info(f"Output files created - VLC: {vlc_size} bytes, TiviMate: {tivimate_size} bytes")
        
        if total_events > 0:
            log.info(f"Successfully created output files with {total_events} total events")
        else:
            log.warning("Output files created but contain 0 events")
    else:
        log.error(f"Failed to create output files - VLC exists: {vlc_exists}, TiviMate exists: {tivimate_exists}")


async def main():
    """Main function to run the scraper"""
    log.info("Starting OVO scraper")
    await scrape()
    log.info("OVO scraper completed")


def run():
    """Synchronous entry point for the scraper"""
    asyncio.run(main())


if __name__ == "__main__":
    run()
