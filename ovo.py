import asyncio
import re
from functools import partial
from urllib.parse import urljoin
from pathlib import Path
from urllib.parse import quote
import os

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "VOLOKIT"

CACHE_FILE = Cache(TAG, exp=10_800)

HTML_CACHE = Cache(f"{TAG}-html", exp=28_800)

BASE_URL = os.environ.get("OVO_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing OVO_BASE_URL secret")

REFERER = BASE_URL if BASE_URL.endswith("/") else BASE_URL + "/"
ORIGIN = BASE_URL.rstrip("/")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)

UA_ENC = quote(USER_AGENT, safe="")

OUT_VLC = Path("ovo_vlc.m3u8")
OUT_TIVI = Path("ovo.tivimate.m3u8")

SPORT_ENDPOINTS = {
    "mlb": "MLB",
    # "nfl": "NFL",
    # "nhl": "NHL",
}


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
        log.warning(f"URL {url_num}) No Clappr source found.")

        return

    log.info(f"URL {url_num}) Captured M3U8")

    return match[1]


async def refresh_html_cache(
    url: str,
    sport: str,
    now: Time,
) -> dict[str, dict[str, str | float]]:

    events = {}

    if not (html_data := await network.request(url, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    date = now.date()

    if date_node := soup.css_first("tr.date"):
        date = date_node.text(strip=True)

    for card in soup.css("#events .table .vevent.theevent"):
        if not (href := card.css_first("a").attributes.get("href")):
            continue

        name_node = card.css_first(".teamtd.event")

        time_node = card.css_first(".time")

        if not (name_node and time_node):
            continue

        name, time = name_node.text(strip=True), time_node.text(strip=True)

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


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (events := HTML_CACHE.load()):
        log.info("Refreshing HTML cache")

        sport_urls = {
            sport: urljoin(BASE_URL, f"sport/{sport}") for sport in SPORT_ENDPOINTS
        }

        tasks = [
            refresh_html_cache(
                url,
                sport,
                now,
            )
            for sport, url in sport_urls.items()
        ]

        results = await asyncio.gather(*tasks)

        events = {k: v for data in results for k, v in data.items()}

        HTML_CACHE.write(events)

    live = []

    start_ts = now.delta(minutes=-30).timestamp()
    end_ts = now.delta(minutes=30).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue

        if not start_ts <= v["event_ts"] <= end_ts:
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
  
build_playlists(cached_urls)

def build_playlists(data: dict[str, dict]):

    vlc = ["#EXTM3U"]
    tivi = ["#EXTM3U"]

    for name, e in data.items():

        if not e.get("url"):
            continue

        extinf = (
            f'#EXTINF:-1 tvg-id="{e["id"]}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )

        # VLC FORMAT
        vlc.extend([
            extinf,
            f"#EXTVLCOPT:http-referrer={REFERER}",
            f"#EXTVLCOPT:http-origin={ORIGIN}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        # TIVIMATE FORMAT (PIPE STYLE)
        tivi.extend([
            extinf,
            f'{e["url"]}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}',
        ])

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tivi), encoding="utf-8")

    log.info("ovo_vlc.m3u8 and ovo.tivimate.m3u8 generated")
