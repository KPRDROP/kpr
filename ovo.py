import asyncio
import re
import urllib.parse
from functools import partial
from urllib.parse import urljoin

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
    "mlb": "MLB",
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

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_tivimate_playlist(urls: dict, output_file="ovo.tivimate.m3u8"):
    encoded_ua = urllib.parse.quote(USER_AGENT, safe="")

    lines = ["#EXTM3U"]

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

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def generate_all_playlists(urls: dict):
    generate_vlc_playlist(urls)
    generate_tivimate_playlist(urls)


# =========================
# SCRAPER LOGIC
# =========================

def fix_event(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split())


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.info(f"URL {url_num}) Failed to load url.")
        return

    soup = HTMLParser(event_data.content)

    if not (iframe := soup.css_first('iframe[height="100%"]')):
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    if not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")
        return

    if not (
        iframe_src_data := await network.request(
            iframe_src,
            headers={"Referer": url},
            log=log,
        )
    ):
        log.info(f"URL {url_num}) Failed to load iframe source.")
        return

    pattern = re.compile(r'source:\s+"([^"]*)"', re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No source found.")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return match[1]


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

        event_sport = SPORT_ENDPOINTS[sport]
        event_name = fix_event(name)

        event_dt = Time.from_str(f"{date} {time}", timezone="UTC")

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

    # REMOVE TIME FILTER — process everything not cached
    for k, v in events.items():
        if k in cached_keys:
            continue

        live.append(v)

    return live


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    if events:
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=(link := ev["link"]),
                url_num=i,
            )

            url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

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

    if new_count := valid_count - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)

    # GENERATE PLAYLIST FILES
    generate_all_playlists(cached_urls)
    log.info("Generated ovo_vlc.m3u8 and ovo.tivimate.m3u8")

if __name__ == "__main__":
    asyncio.run(scrape())
