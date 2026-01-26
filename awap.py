import os
import base64
import re
import urllib.parse
from functools import partial

import feedparser
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "PAWA"

CACHE_FILE = Cache(TAG, exp=10_800)

BASE_URL = os.environ.get("PAWA_API_URL")
if not BASE_URL:
    raise RuntimeError("Missing PAWA_API_URL secret")

OUTPUT_VLC = "awap_vlc.m3u8"
OUTPUT_TIVI = "awap_tivimate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
)


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.info(f"URL {url_num}) Failed to load url.")
        return

    soup = HTMLParser(event_data.content)

    iframe = soup.css_first("iframe")
    if not iframe:
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    iframe_src = iframe.attributes.get("src")
    if not iframe_src:
        log.warning(f"URL {url_num}) No iframe source found.")
        return

    iframe_data = await network.request(iframe_src, log=log)
    if not iframe_data:
        log.info(f"URL {url_num}) Failed to load iframe source.")
        return

    pattern = re.compile(r"source:\s*window\.atob\(\s*'([^']+)'\s*\)", re.I)
    match = pattern.search(iframe_data.text)
    if not match:
        log.warning(f"URL {url_num}) No Clappr source found.")
        return

    log.info(f"URL {url_num}) Captured M3U8")
    return base64.b64decode(match[1]).decode("utf-8")


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    html_data = await network.request(BASE_URL, log=log)
    if not html_data:
        return events

    feed = feedparser.parse(html_data.content)

    for entry in feed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue

        sport = "Live Event"
        title = title.replace(" v ", " vs ")

        key = f"[{sport}] {title} ({TAG})"
        if key in cached_keys:
            continue

        events.append(
            {
                "sport": sport,
                "event": title,
                "link": link,
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()
    cached_count = len(cached_urls)
    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())
    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.clean(Time.now()).timestamp()

        for i, ev in enumerate(events, start=1):
            handler = partial(process_event, url=ev["link"], url_num=i)
            url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            if not url:
                continue

            sport, event, link = ev["sport"], ev["event"], ev["link"]
            key = f"[{sport}] {event} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info(sport, event)

            urls[key] = cached_urls[key] = {
                "url": url,
                "logo": logo,
                "base": link,
                "timestamp": now,
                "id": tvg_id or "Live.Event.us",
                "event": event,
            }

    CACHE_FILE.write(cached_urls)
    write_playlists(cached_urls)


def write_playlists(entries: dict):
    vlc = ['#EXTM3U']
    tivi = ['#EXTM3U']

    encoded_ua = urllib.parse.quote(USER_AGENT, safe="")

    for idx, (key, e) in enumerate(entries.items(), start=1):
        title = f"[Live Event] {e['event']} (PAWA)"
        ref = e["base"]

        extinf = (
            f'#EXTINF:-1 tvg-chno="{idx}" '
            f'tvg-id="{e["id"]}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{title}'
        )

        # VLC
        vlc.append(extinf)
        vlc.append(f"#EXTVLCOPT:http-referrer={ref}")
        vlc.append(f"#EXTVLCOPT:http-origin={ref}")
        vlc.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc.append(e["url"])

        # TiviMate
        tivi.append(extinf)
        tivi.append(
            f'{e["url"]}'
            f'|referer={ref}'
            f'|origin={ref}'
            f'|user-agent={encoded_ua}'
        )

    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("\n".join(vlc) + "\n")

    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("\n".join(tivi) + "\n")

    log.info(f"Generated {OUTPUT_VLC} and {OUTPUT_TIVI}")
