import asyncio
import os
from functools import partial
from urllib.parse import urljoin, quote

from playwright.async_api import Browser, async_playwright
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRHUB"

CACHE_FILE = Cache(TAG, exp=10800)

HTML_CACHE = Cache(f"{TAG}-html", exp=19800)

BASE_URL = os.environ.get("SRTHUB_BASE_URL")

if not BASE_URL:
    raise RuntimeError("Missing SRTHUB_BASE_URL secret")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

REFERRER = "https://storytrench.net/"

SPORT_ENDPOINTS = [
    "sport_68c02a4464a38",  # Soccer
    "sport_68c02a4465113",  # American Football
    "sport_68c02a446582f",  # Baseball
    "sport_68c02a4466011",  # Basketball
    "sport_68c02a44669f3",  # Cricket
    "sport_68c02a4466f56",  # Hockey
    "sport_68c02a44674e9",  # MMA
    "sport_68c02a4467a48",  # Racing
    "sport_68c02a4467fc1",  # Rugby
    "sport_68c02a4468624",  # Rugby League
    "sport_68c02a4468cf7",  # Tennis
    "sport_68c02a4469422",  # Volleyball
]


# ---------------------------------------------------------
# PLAYLIST GENERATOR
# ---------------------------------------------------------

def generate_playlists():
    vlc_lines = ["#EXTM3U"]
    tivimate_lines = ["#EXTM3U"]

    ua_encoded = quote(USER_AGENT, safe="")

    for chno, (name, data) in enumerate(urls.items(), start=1):
        url = data.get("url")
        logo = data.get("logo") or ""
        tvg_id = data.get("id")
        base = data.get("base") or REFERRER

        if not url:
            continue

        extinf = (
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" '
            f'tvg-name="{name}" tvg-logo="{logo}" group-title="Live Events",{name}'
        )

        # VLC playlist
        vlc_lines.append(extinf)
        vlc_lines.append(f"#EXTVLCOPT:http-referrer={base}")
        vlc_lines.append(f"#EXTVLCOPT:http-origin={base}")
        vlc_lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc_lines.append(url)

        # TiviMate playlist
        tivimate_lines.append(extinf)
        tiv_url = (
            f"{url}"
            f"|referer={base}"
            f"|origin={base}"
            f"|user-agent={ua_encoded}"
        )
        tivimate_lines.append(tiv_url)

    with open("srthub_vlc.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(vlc_lines))

    with open("srthub_tivimate.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(tivimate_lines))

    log.info("Playlists generated: srthub_vlc.m3u8 / srthub_tivimate.m3u8")


# ---------------------------------------------------------
# FETCH LISTING PAGE WITH PLAYWRIGHT
# ---------------------------------------------------------

async def fetch_listing_page(browser: Browser, url: str) -> str | None:
    """Load a page with Playwright and return its HTML content."""
    context = None
    page = None
    try:
        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Referer": REFERRER}
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        # Give a little extra time for any dynamic content
        await page.wait_for_timeout(2000)
        content = await page.content()
        return content
    except Exception as e:
        log.error(f"Failed to fetch {url} with Playwright: {e}")
        return None
    finally:
        if page:
            await page.close()
        if context:
            await context.close()


# ---------------------------------------------------------
# HTML CACHE REFRESH (USING PLAYWRIGHT)
# ---------------------------------------------------------

async def refresh_html_cache(browser: Browser, date: str, sport_id: str, ts: float) -> dict:
    events = {}

    url = urljoin(BASE_URL, f"events/{date}")
    full_url = f"{url}?sport_id={sport_id}"
    log.info(f"Fetching listing: {full_url}")

    html_content = await fetch_listing_page(browser, full_url)
    if not html_content:
        return events

    soup = HTMLParser(html_content)

    # Iterate over each event section (each sport category)
    for section in soup.css(".events-section"):
        # Extract sport name (from .sport-name if available, else fallback to .section-titlte)
        sport = "Unknown"
        if sport_node := section.css_first(".sport-name"):
            sport = sport_node.text(strip=True)
        else:
            # Fallback: use the section title as sport name
            if title_node := section.css_first(".section-titlte"):
                sport = title_node.text(strip=True)

        # Extract league name from .section-titlte (if present)
        league = ""
        if league_node := section.css_first(".section-titlte"):
            league = league_node.text(strip=True)

        # Process each event inside the section
        for event in section.css(".section-event"):
            # Build event name from competitors
            event_name = "Live Event"
            if teams := event.css_first(".event-competitors"):
                text = teams.text(strip=True)
                if "vs." in text:
                    home, away = text.split("vs.", 1)
                    event_name = f"{away.strip()} vs {home.strip()}"
                else:
                    event_name = text

            # Event URL
            if not (event_button := event.css_first(".event-button a")):
                continue
            href = event_button.attributes.get("href")
            if not href:
                continue

            # Event start time
            countdown = event.css_first(".event-countdown")
            if not countdown:
                continue
            event_date = countdown.attributes.get("data-start")
            if not event_date:
                continue

            event_dt = Time.from_str(event_date, timezone="UTC")

            # Build unique key for caching
            key = f"[{sport}] {event_name} ({TAG})"

            events[key] = {
                "sport": sport,
                "league": league,
                "event": event_name,
                "link": href,
                "event_ts": event_dt.timestamp(),
                "timestamp": ts,
            }

    return events


# ---------------------------------------------------------
# EVENT DISCOVERY
# ---------------------------------------------------------

async def get_events(browser: Browser, cached_keys: set) -> list:
    now = Time.clean(Time.now())

    if not (events := HTML_CACHE.load()):
        log.info("Refreshing HTML cache")

        # Only fetch today's date (removed tomorrow)
        tasks = [
            refresh_html_cache(browser, now.date(), sport_id, now.timestamp())
            for sport_id in SPORT_ENDPOINTS
        ]

        results = await asyncio.gather(*tasks)

        events = {k: v for data in results for k, v in data.items()}

        HTML_CACHE.write(events)

    live = []

    start_ts = now.delta(hours=-1).timestamp()
    end_ts = now.delta(minutes=1).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue
        if not start_ts <= v["event_ts"] <= end_ts:
            continue
        live.append(v)

    return live


# ---------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------

async def scrape(browser: Browser):
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(browser, set(cached_urls.keys())):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        timeout=5,
                        log=log,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
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
                        "base": REFERRER,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1
                        entry["url"] = url.split("?")[0]
                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)

    generate_playlists()


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

async def main():
    log.info("Starting STRHUB scraper")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        try:
            await scrape(browser)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
