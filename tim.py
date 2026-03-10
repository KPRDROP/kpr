import asyncio
import os
import re
from functools import partial
from urllib.parse import urljoin, quote

from playwright.async_api import Browser, Page, Response, Frame
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
# ROBUST M3U8 CAPTURE WITH INTERACTION
# ---------------------------------------------------------

async def capture_m3u8(
    page: Page,
    embed_frame: Frame | None,
    url_num: int,
    timeout: int = 30,
) -> str | None:
    """
    Listen for m3u8 requests/responses and interact with the player.
    """
    captured = []
    got_one = asyncio.Event()

    def handle_request(request):
        req_url = request.url.lower()
        if ".m3u8" in req_url and not any(
            x in req_url for x in ["hmembeds.one", "analytics", "tracking"]
        ):
            captured.append(request.url)
            got_one.set()
            log.info(f"URL {url_num}) M3U8 request: {request.url}")

    def handle_response(response):
        resp_url = response.url.lower()
        # Check URL
        if ".m3u8" in resp_url and not any(
            x in resp_url for x in ["hmembeds.one", "analytics", "tracking"]
        ):
            captured.append(response.url)
            got_one.set()
            log.info(f"URL {url_num}) M3U8 response: {response.url}")
        # Check content-type header
        try:
            content_type = response.headers.get("content-type", "").lower()
            if "mpegurl" in content_type or "application/vnd.apple.mpegurl" in content_type:
                if not any(x in resp_url for x in ["hmembeds.one", "analytics", "tracking"]):
                    captured.append(response.url)
                    got_one.set()
                    log.info(f"URL {url_num}) M3U8 by content-type: {response.url}")
        except:
            pass

    page.on("request", handle_request)
    page.on("response", handle_response)

    try:
        # Wait a bit for the player to initialize
        await asyncio.sleep(2)

        # If we have an embed frame, try to click the play button inside it
        if embed_frame:
            try:
                # Try various play button selectors
                selectors = [
                    "button",
                    ".play-button",
                    ".vjs-big-play-button",
                    ".jw-icon-play",
                    ".mejs-playpause-button",
                    "[aria-label='Play']",
                    ".fp-playbtn",
                    "video",
                ]
                for selector in selectors:
                    try:
                        btn = await embed_frame.wait_for_selector(selector, timeout=2000)
                        if btn:
                            await btn.click()
                            log.info(f"URL {url_num}) Clicked play button: {selector}")
                            await asyncio.sleep(1)
                            break
                    except:
                        continue

                # If no button found, click the center of the frame
                await embed_frame.mouse.click(640, 360)
                log.info(f"URL {url_num}) Clicked center of embed frame")
            except Exception as e:
                log.debug(f"URL {url_num}) Interaction error: {e}")

        # Also try to execute JavaScript to play any video elements
        await page.evaluate("""
            () => {
                const videos = document.querySelectorAll('video');
                videos.forEach(v => { try { v.play(); } catch(e) {} });
                const frames = document.querySelectorAll('iframe');
                frames.forEach(f => {
                    try {
                        const doc = f.contentDocument || f.contentWindow.document;
                        const vids = doc.querySelectorAll('video');
                        vids.forEach(v => { try { v.play(); } catch(e) {} });
                    } catch(e) {}
                });
            }
        """)
        log.info(f"URL {url_num}) Executed JavaScript play attempts")

        # Wait for m3u8 capture
        try:
            await asyncio.wait_for(got_one.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8 after {timeout}s")
            return None

        return captured[0] if captured else None

    finally:
        page.remove_listener("request", handle_request)
        page.remove_listener("response", handle_response)


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
    embed_url: str | None = None

    got_stream = asyncio.Event()

    # -----------------------------
    # REQUEST SNIFFER
    # -----------------------------
    async def req_listener(req):

        rurl = req.url.lower()

        if ".m3u8" in rurl:
            captured.append(req.url)
            got_stream.set()

    page.on("request", req_listener)

    # -----------------------------
    # RESPONSE SNIFFER
    # -----------------------------
    async def resp_listener(resp):

        rurl = resp.url.lower()

        if ".m3u8" in rurl:
            captured.append(resp.url)
            got_stream.set()

        try:
            ct = resp.headers.get("content-type", "")
            if "mpegurl" in ct:
                captured.append(resp.url)
                got_stream.set()
        except:
            pass

    page.on("response", resp_listener)

    try:

        resp = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=10000,
        )

        if not resp or resp.status != 200:
            log.warning(f"URL {url_num}) Bad response")

        await page.wait_for_timeout(3000)

        # -----------------------------
        # iframe detection
        # -----------------------------
        for frame in page.frames:

            furl = frame.url

            if "hmembeds" in furl or "embed" in furl:
                embed_url = furl

        # -----------------------------
        # interaction trigger
        # -----------------------------
        for _ in range(3):

            try:
                await page.mouse.click(500, 400)
                await page.wait_for_timeout(1200)
            except:
                pass

        # -----------------------------
        # wait for network stream
        # -----------------------------
        try:

            await asyncio.wait_for(got_stream.wait(), timeout=12)

        except asyncio.TimeoutError:

            log.warning(f"URL {url_num}) Network sniff timeout")

        # -----------------------------
        # iframe deep sniff
        # -----------------------------
        if not captured:

            for frame in page.frames:

                try:

                    html = await frame.content()

                    if ".m3u8" in html:

                        import re

                        match = re.search(
                            r"https?://[^\"']+\.m3u8[^\"']*",
                            html,
                        )

                        if match:
                            captured.append(match.group(0))
                            break

                except:
                    pass

        # -----------------------------
        # DOM fallback
        # -----------------------------
        if not captured:

            try:

                html = await page.content()

                import re

                match = re.search(
                    r"https?://[^\"']+\.m3u8[^\"']*",
                    html,
                )

                if match:
                    captured.append(match.group(0))

            except:
                pass

        # -----------------------------
        # RESULT
        # -----------------------------
        if captured:

            log.info(f"URL {url_num}) M3U8 captured")

            return captured[0], embed_url or url

        log.warning(f"URL {url_num}) No stream found")

        return nones

    except Exception as e:

        log.warning(f"URL {url_num}) {e}")

        return nones

    finally:

        page.remove_listener("request", req_listener)
        page.remove_listener("response", resp_listener)


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

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}

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

                    # Get result from safe_process; it may be None on timeout/error
                    result = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        timeout=60,  # Increased to 60 seconds
                        log=log,
                    )

                    if result is None:
                        url, iframe = None, None
                    else:
                        url, iframe = result

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
                        log.info(f"URL {i}) Stream captured: {url}")
                    else:
                        log.warning(f"URL {i}) No stream found")

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)

    # generate playlists
    generate_playlists()


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

from playwright.async_api import async_playwright


async def main():

    log.info("Starting TIM Streams updater")

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )

        try:
            await scrape(browser)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
