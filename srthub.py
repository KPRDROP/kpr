import asyncio
import os
from urllib.parse import quote

from playwright.async_api import async_playwright
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues

log = get_logger(__name__)

TAG = "STRHUB"

CACHE_FILE = Cache(TAG, exp=10800)

BASE_URL = os.environ.get("SRTHUB_BASE_URL")

if not BASE_URL:
    raise RuntimeError("Missing SRTHUB_BASE_URL secret")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

REFERRER = "https://storytrench.net/"

SPORT_ENDPOINTS = [
    "sport_68c02a4464a38",
    "sport_68c02a4465113",
    "sport_68c02a446582f",
    "sport_68c02a4466011",
    "sport_68c02a44669f3",
    "sport_68c02a4466f56",
    "sport_68c02a44674e9",
    "sport_68c02a4467a48",
    "sport_68c02a4467fc1",
    "sport_68c02a4468624",
    "sport_68c02a4468cf7",
    "sport_68c02a4469422",
]

urls = {}


# ---------------------------------------------------------
# PLAYLIST GENERATOR
# ---------------------------------------------------------

def generate_playlists():

    vlc_lines = ["#EXTM3U"]
    tiv_lines = ["#EXTM3U"]

    ua_enc = quote(USER_AGENT, safe="")

    for chno, (name, data) in enumerate(urls.items(), start=1):

        url = data["url"]
        logo = data["logo"]
        tvg_id = data["id"]

        extinf = (
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" '
            f'tvg-name="{name}" tvg-logo="{logo}" group-title="Live Events",{name}'
        )

        vlc_lines.append(extinf)
        vlc_lines.append(f"#EXTVLCOPT:http-referrer={REFERRER}")
        vlc_lines.append(f"#EXTVLCOPT:http-origin={REFERRER}")
        vlc_lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc_lines.append(url)

        tiv_lines.append(extinf)

        tiv_lines.append(
            f"{url}|referer={REFERRER}|origin={REFERRER}|user-agent={ua_enc}"
        )

    with open("srthub_vlc.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(vlc_lines))

    with open("srthub_tivimate.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(tiv_lines))

    log.info("Playlists generated: srthub_vlc.m3u8 / srthub_tivimate.m3u8")


# ---------------------------------------------------------
# M3U8 DETECTION
# ---------------------------------------------------------

async def detect_stream(page):

    stream = None

    async def handler(response):
        nonlocal stream
        url = response.url

        if ".m3u8" in url:
            stream = url

    page.on("response", handler)

    await asyncio.sleep(10)

    return stream


# ---------------------------------------------------------
# EVENT PAGE SCRAPER
# ---------------------------------------------------------

async def process_event(browser, event):

    page = await browser.new_page()

    await page.set_extra_http_headers(
        {
            "referer": REFERRER,
            "origin": REFERRER,
            "user-agent": USER_AGENT,
        }
    )

    await page.goto(event["link"], timeout=60000)

    await page.wait_for_timeout(8000)

    stream = await detect_stream(page)

    await page.close()

    return stream


# ---------------------------------------------------------
# EVENT DISCOVERY (TODAY ONLY)
# ---------------------------------------------------------

async def get_events(browser):

    today = Time.now().date()

    events = []

    page = await browser.new_page()

    for sport in SPORT_ENDPOINTS:

        url = f"{BASE_URL}/events/{today}/{sport}"

        try:

            await page.goto(url, timeout=60000)

        except Exception:
            continue

        html = await page.content()

        soup = HTMLParser(html)

        sport_name = soup.css_first(".sport-name")

        sport_title = sport_name.text(strip=True) if sport_name else "Sport"

        for section in soup.css(".events-section"):

            league_node = section.css_first(".section-titlte")

            league = league_node.text(strip=True) if league_node else ""

            for event in section.css(".section-event"):

                teams = event.css_first(".event-competitors")

                if not teams:
                    continue

                name = teams.text(strip=True)

                btn = event.css_first(".event-button a")

                if not btn:
                    continue

                link = btn.attributes.get("href")

                key = f"[{league}] {name} ({TAG})"

                events.append(
                    {
                        "sport": sport_title,
                        "league": league,
                        "event": name,
                        "link": link,
                        "key": key,
                    }
                )

    await page.close()

    return events


# ---------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------

async def scrape(browser):

    cached = CACHE_FILE.load()

    urls.update({k: v for k, v in cached.items() if v.get("url")})

    log.info(f"Loaded {len(urls)} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(browser)

    log.info(f"Processing {len(events)} events")

    for ev in events:

        key = ev["key"]

        if key in urls:
            continue

        stream = await process_event(browser, ev)

        if not stream:
            continue

        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        entry = {
            "url": stream.split("?")[0],
            "logo": logo,
            "id": tvg_id or "Live.Event.us",
        }

        urls[key] = entry

        cached[key] = entry

        log.info(f"Captured stream: {key}")

    CACHE_FILE.write(cached)

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
