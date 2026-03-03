import asyncio
import os
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

TAG = "WEBCAST"

BASE_URL = os.environ.get("WEBTV_MLB_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing WEBTV_MLB_BASE_URL secret")

BASE_URLS = {
    "MLB": BASE_URL
}

REFERER = BASE_URL
ORIGIN = BASE_URL.rstrip("/")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
UA_ENC = quote(USER_AGENT)

OUT_VLC = Path("webtv_vlc.m3u8")
OUT_TIVI = Path("webtv_tivimate.m3u8")

CACHE_FILE = Cache(TAG, exp=19_800)

urls: dict[str, dict[str, str | float]] = {}

# --------------------------------------------------

def fix_event(s: str) -> str:
    return " vs ".join(map(str.strip, s.split("@")))

# --------------------------------------------------

async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.info(f"URL {url_num}) Failed to load url.")
        return

    soup = HTMLParser(event_data.content)

    if not (iframe := soup.css_first('iframe[name="srcFrame"]')):
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

    pattern = re.compile(r"source:\s+(\'|\")(.*?)(\'|\")", re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")
        return

    log.info(f"URL {url_num}) Captured M3U8")
    return match[2]

# --------------------------------------------------

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    tasks = [network.request(url, log=log) for url in BASE_URLS.values()]
    results = await asyncio.gather(*tasks)

    events = []

    if not (
        soups := [(HTMLParser(html.content), html.url) for html in results if html]
    ):
        return events

    for soup, url in soups:
        sport = next((k for k, v in BASE_URLS.items() if v == url), "Live Event")

        for row in soup.css("tr.singele_match_date"):
            if not (vs_node := row.css_first("td.teamvs a")):
                continue

            event_name = vs_node.text(strip=True)

            for span in vs_node.css("span.mtdate"):
                date = span.text(strip=True)
                event_name = event_name.replace(date, "").strip()

            if not (href := vs_node.attributes.get("href")):
                continue

            event = fix_event(event_name)

            key = f"[{sport}] {event} ({TAG})"

            if key in cached_keys:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event,
                    "link": href,
                }
            )

    return events

# --------------------------------------------------

def build_playlists(data: dict[str, dict]):

    vlc = ["#EXTM3U"]
    tivimate = ["#EXTM3U"]

    channel_number = 200

    for name, e in data.items():

        if not e.get("url"):
            continue

        channel_number += 1

        # VLC FORMAT
        vlc.extend([
            f'#EXTINF:-1 tvg-chno="{channel_number}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={REFERER}",
            f"#EXTVLCOPT:http-origin={ORIGIN}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        # TIVIMATE FORMAT
        tivimate.extend([
            f'#EXTINF:-1 tvg-chno="{channel_number}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}',
            f'{e["url"]}|referer={REFERER}/|origin={ORIGIN}|user-agent={UA_ENC}',
        ])

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tivimate), encoding="utf-8")

    log.info("Playlists written successfully")

# --------------------------------------------------

async def scrape() -> None:

    cached_urls = CACHE_FILE.load() or {}

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}
    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(list(cached_urls.keys())):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):

            handler = partial(
                process_event,
                url=(link := ev["link"]),
                url_num=i,
            )

            stream_url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.PW_S,
                log=log,
            )

            sport, event = ev["sport"], ev["event"]
            key = f"[{sport}] {event} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            entry = {
                "url": stream_url,
                "logo": logo,
                "base": BASE_URL,
                "timestamp": now.timestamp(),
                "id": tvg_id or "MLB.Baseball.Dummy.us",
                "link": link,
            }

            cached_urls[key] = entry

            if stream_url:
                valid_count += 1
                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
    build_playlists(cached_urls)

# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
