import asyncio
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote, quote_plus, urljoin

from playwright.async_api import Browser
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "FAWA"
BASE_URL = "http://www.fawanews.sc/"

REFERER = "http://www.fawanews.sc/"
ORIGIN = "http://www.fawanews.sc"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)
OUTPUT_FILE = Path("awaf.m3u")
TIVIMATE_FILE = Path("awaf_tivimate.m3u")

# Encoded User-Agent for TiViMate pipe
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)
UA_ENC = quote_plus(UA)


# -------------------------------------------------
# Build the VLC/standard playlist text
# -------------------------------------------------
def build_playlist(data: dict[str, dict]) -> str:
    lines = ["#EXTM3U"]
    chno = 1

    for title, info in data.items():
        source = info.get("source") or info.get("url")
        if not source:
            continue

        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{info.get("id", "Live.Event.us")}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{info.get("logo", "")}" '
            f'group-title="Live Events",{title}'
        )
        lines.append(f"#EXTVLCOPT:http-referrer={REFERER}")
        lines.append(f"#EXTVLCOPT:http-origin={ORIGIN}")
        lines.append(f"#EXTVLCOPT:http-user-agent={UA}")
        lines.append(source)
        chno += 1

    return "\n".join(lines) + "\n"


# -------------------------------------------------
# Build the TiviMate playlist text (pipe headers)
# -------------------------------------------------
def build_tivimate_playlist(data: dict[str, dict]) -> str:
    lines = ["#EXTM3U"]
    chno = 1

    for title, info in data.items():
        source = info.get("source") or info.get("url")
        if not source:
            continue

        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="{info.get("id", "Live.Event.us")}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{info.get("logo", "")}" '
            f'group-title="Live Events",{title}'
        )
        lines.append(
            f"{source}"
            f"|referer={REFERER}"
            f"|origin={ORIGIN}"
            f"|user-agent={UA_ENC}"
        )
        chno += 1

    return "\n".join(lines) + "\n"


# -------------------------------------------------
# Parse events from main homepage HTML
# -------------------------------------------------
async def get_events(cached_links: set[str]) -> list[Event]:
    events: list[Event] = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    valid_event = re.compile(r"\d{1,2}:\d{1,2}")
    clean_event = re.compile(r"\s+-+\s+\w{1,4}")

    for item in soup.css(".user-item"):
        text_elem = item.css_first(".user-item__name")
        subtext_elem = item.css_first(".user-item__playing")
        link_elem = item.css_first("a[href]")

        if not (text_elem and subtext_elem):
            continue

        elif not (href := link_elem.attributes.get("href")):
            continue

        elif (link := urljoin(f"{html_data.url}", quote(href))) in cached_links:
            continue

        event_name, details = text_elem.text(strip=True), subtext_elem.text(strip=True)

        if not valid_event.search(details):
            continue

        sport = valid_event.split(details)[0].strip()

        events.append(
            Event(
                sport=sport,
                name=clean_event.sub("", event_name),
                link=link,
            )
        )

    return events


# -------------------------------------------------
# Main scrape function
# -------------------------------------------------
async def scrape(browser: Browser) -> None:
    cached_sources = CACHE_FILE.load() or {}

    cached_links = {entry["link"] for entry in cached_sources.values() if entry.get("link")}

    valid_sources = {k: v for k, v in cached_sources.items() if v.get("source")}

    valid_count = cached_count = len(valid_sources)

    urls: dict[str, dict] = dict(valid_sources)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_links)

    if events:
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.rn()

        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=ev.link,
                        url_num=i,
                        page=page,
                        log=log,
                    )

                    source = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.HTTP_S,
                        log=log,
                    )

                    key = f"[{ev.sport}] {ev.name} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

                    entry = {
                        "source": source,
                        "logo": logo,
                        "refer": BASE_URL,
                        "timestamp": now.timestamp(),
                        "tvg-id": tvg_id or "Live.Event.us",
                        "link": ev.link,
                    }

                    cached_sources[key] = entry

                    if source:
                        valid_count += 1
                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_sources)

    # Write both playlists from the valid entries only
    vlc_out = build_playlist(urls)
    tivimate_out = build_tivimate_playlist(urls)

    OUTPUT_FILE.write_text(vlc_out, encoding="utf-8")
    TIVIMATE_FILE.write_text(tivimate_out, encoding="utf-8")

    log.info(f"Successfully wrote {len(urls)} entries to {OUTPUT_FILE.name}")
    log.info(f"Successfully wrote {len(urls)} entries to {TIVIMATE_FILE.name}")


# -------------------------------------------------
# Run scraper
# -------------------------------------------------
async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            await scrape(browser)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
