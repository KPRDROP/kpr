import asyncio
import os
from functools import partial
from urllib.parse import urljoin, quote

from playwright.async_api import Browser, Page, Response
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIMSTRMS"

CACHE_FILE = Cache(TAG, exp=10_800)

BASE_URL = os.environ.get("TIM_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing TIM_BASE_URL secret")

SPORT_GENRES = {
    1: "Soccer",
    2: "Motorsport",
    3: "MMA",
    4: "Fight",
    5: "Boxing",
    6: "Wrestling",
    7: "Basketball",
    9: "Baseball",
    10: "Tennis",
    11: "Hockey",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


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
        base = data.get("base")

        if not url:
            continue

        extinf = (
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" '
            f'tvg-name="{name}" tvg-logo="{logo}" group-title="Live Events",{name}'
        )

        # VLC
        vlc_lines.append(extinf)
        vlc_lines.append(f"#EXTVLCOPT:http-referrer={base}")
        vlc_lines.append(f"#EXTVLCOPT:http-origin={base}")
        vlc_lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc_lines.append(url)

        # TiviMate
        tivimate_lines.append(extinf)

        tiv_url = (
            f"{url}"
            f"|referer={base}"
            f"|origin={base}"
            f"|user-agent={ua_encoded}"
        )

        tivimate_lines.append(tiv_url)

    with open("tim_vlc.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(vlc_lines))

    with open("tim_tivimate.m3u8", "w", encoding="utf8") as f:
        f.write("\n".join(tivimate_lines))

    log.info("Playlists generated: tim_vlc.m3u8 / tim_tivimate.m3u8")


# ---------------------------------------------------------
# NETWORK FILTER
# ---------------------------------------------------------

def sift_xhr(resp: Response) -> bool:
    resp_url = resp.url
    return "hmembeds.one/embed" in resp_url and resp.status == 200


# ---------------------------------------------------------
# PROCESS EVENT
# ---------------------------------------------------------

async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> tuple[str | None, str | None]:

    nones = None, None

    captured: list[str] = []

    got_one = asyncio.Event()

    handler = partial(
        network.capture_req,
        captured=captured,
        got_one=got_one,
    )

    page.on("request", handler)

    try:

        try:

            async with page.expect_response(sift_xhr, timeout=3000) as strm_resp:

                resp = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=6000,
                )

                if not resp or resp.status != 200:
                    log.warning(
                        f"URL {url_num}) Status Code: {resp.status if resp else 'None'}"
                    )
                    return nones

                response = await strm_resp.value
                embed_url = response.url

        except TimeoutError:

            log.warning(f"URL {url_num}) No available stream links.")
            return nones

        wait_task = asyncio.create_task(got_one.wait())

        try:

            await asyncio.wait_for(wait_task, timeout=8)

        except asyncio.TimeoutError:

            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return nones

        finally:

            if not wait_task.done():
                wait_task.cancel()

                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass

        if captured:

            log.info(f"URL {url_num}) Captured M3U8")

            return captured[0], embed_url

        log.warning(f"URL {url_num}) No M3U8 captured after waiting.")

        return nones

    except Exception as e:

        log.warning(f"URL {url_num}) {e}")

        return nones

    finally:

        page.remove_listener("request", handler)


# ---------------------------------------------------------
# EVENT DISCOVERY
# ---------------------------------------------------------

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:

    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    for card in soup.css("#eventsSection .card"):

        card_attrs = card.attributes

        if not (sport_id := card_attrs.get("data-genre")):
            continue

        elif not (sport := SPORT_GENRES.get(int(sport_id))):
            continue

        if not (event_name := card_attrs.get("data-search")):
            continue

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        if (not (watch_btn := card.css_first("a.btn-watch"))) or (
            not (href := watch_btn.attributes.get("href"))
        ):
            continue

        logo = None

        if card_thumb := card.css_first(".card-thumb img"):
            logo = card_thumb.attributes.get("src")

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": urljoin(BASE_URL, href),
                "logo": logo,
            }
        )

    return events


# ---------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------

async def scrape(browser: Browser) -> None:

    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        async with network.event_context(browser, stealth=False) as context:

            for i, ev in enumerate(events, start=1):

                async with network.event_page(context) as page:

                    handler = partial(
                        process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                    )

                    url, iframe = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    sport, event, logo = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": iframe,
                        "timestamp": now.timestamp(),
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:

                        valid_count += 1
                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:

        log.info("No new events found")

    CACHE_FILE.write(cached_urls)

    # generate playlists
    generate_playlists()
