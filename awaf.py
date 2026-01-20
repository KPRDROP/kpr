import re
from functools import partial
from pathlib import Path
from urllib.parse import quote, urljoin

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FAWA"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)

BASE_URL = "http://www.fawanews.sc/"

OUTPUT_FILE = Path("awaf.m3u")

UA_ENC = (
    "Mozilla%2F5.0%20(Windows%20NT%2010.0%3B%20Win64%3B%20x64%3B%20rv%3A146.0)"
    "%20Gecko%2F20100101%20Firefox%2F146.0"
)


def build_playlist(data: dict) -> str:
    lines = ["#EXTM3U"]
    chno = 1

    for name, e in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{e["id"]}" '
            f'tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )
        lines.append(
            f'{e["url"]}'
            f'|referer={BASE_URL}'
            f'|origin={BASE_URL}'
            f'|user-agent={UA_ENC}'
        )
        chno += 1

    return "\n".join(lines) + "\n"


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        log.info(f"URL {url_num}) Failed to load url.")
        return

    valid_m3u8 = re.compile(
        r'(https?:\/\/[^"\'\s>]+\.m3u8(?:\?[^"\'\s>]*)?)',
        re.IGNORECASE,
    )

    if not (match := valid_m3u8.search(html_data.text)):
        log.info(f"URL {url_num}) No M3U8 found")
        return

    log.info(f"URL {url_num}) Captured M3U8")
    return match[1]


async def get_events(cached_hrefs: set[str]) -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    valid_event = re.compile(r"\d{1,2}:\d{1,2}")
    clean_event = re.compile(r"\s+-+\s+\w{1,4}")

    for item in soup.css(".user-item"):
        text = item.css_first(".user-item__name")
        subtext = item.css_first(".user-item__playing")
        link = item.css_first("a[href]")

        if not (text and subtext and link):
            continue

        href = quote(link.attributes.get("href", ""))

        if not href or href in cached_hrefs:
            continue

        details = subtext.text(strip=True)
        if not valid_event.search(details):
            continue

        sport = valid_event.split(details)[0].strip()
        event = clean_event.sub("", text.text(strip=True))

        events.append(
            {
                "sport": sport,
                "event": event,
                "link": urljoin(BASE_URL, href),
                "href": href,
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()
    cached_hrefs = {v["href"] for v in cached_urls.values()}
    cached_count = len(cached_urls)

    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} cached events")

    events = await get_events(cached_hrefs)
    log.info(f"Found {len(events)} new event(s)")

    if not events:
        log.info("No new events found")
        return

    now = Time.clean(Time.now()).timestamp()

    for i, ev in enumerate(events, 1):
        handler = partial(process_event, url=ev["link"], url_num=i)

        url = await network.safe_process(
            handler,
            url_num=i,
            semaphore=network.HTTP_S,
            log=log,
        )

        if not url:
            continue

        key = f"[{ev['sport']}] {ev['event']} ({TAG})"
        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        cached_urls[key] = {
            "url": url,
            "logo": logo,
            "base": BASE_URL,
            "timestamp": now,
            "id": tvg_id or "Live.Event.us",
            "href": ev["href"],
        }

    CACHE_FILE.write(cached_urls)

    playlist = build_playlist(cached_urls)
    OUTPUT_FILE.write_text(playlist, encoding="utf-8")

    log.info(f"Updated awaf.m3u with {len(cached_urls) - cached_count} new event(s)")
