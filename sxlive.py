import asyncio
import os
import urllib.parse
from functools import partial

import feedparser
from playwright.async_api import Browser, Page

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SXLIVE"

CACHE_FILE = Cache(TAG, exp=10_800)
XML_CACHE = Cache(f"{TAG}-xml", exp=28_000)

# -------------------------------------------------
# SECRETS
# -------------------------------------------------

SXLIVE_BASE_URL = os.environ.get("SXLIVE_BASE_URL")
SXLIVE_BASE_REF = os.environ.get("SXLIVE_BASE_REF")

if not SXLIVE_BASE_URL or not SXLIVE_BASE_REF:
    raise RuntimeError("Missing required secrets")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)

VALID_SPORTS = [
    "MLB. Preseason",
    "MLB",
    "Basketball",
    "Football",
    "Ice Hockey",
]

# -------------------------------------------------
# PROCESS EVENT (ORIGINAL WORKING LOGIC)
# -------------------------------------------------

async def process_event(url: str, url_num: int, page: Page) -> str | None:
    captured = []
    got_one = asyncio.Event()

    handler = partial(
        network.capture_req,
        captured=captured,
        got_one=got_one,
    )

    page.on("request", handler)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
        await page.wait_for_timeout(1500)

        buttons = await page.query_selector_all(".lnktbj a[href*='webplayer']")

        labels = await page.eval_on_selector_all(
            ".lnktyt span",
            "elements => elements.map(el => el.textContent.trim().toLowerCase())",
        )

        for btn, label in zip(buttons, labels):
            if label in ["web", "youtube"]:
                continue

            href = await btn.get_attribute("href")
            if href:
                break
        else:
            log.warning(f"URL {url_num}) No valid sources found.")
            return None

        href = href if href.startswith("http") else f"https:{href}"
        href = href.replace("livetv.sx", "livetv873.me")

        await page.goto(href, wait_until="domcontentloaded", timeout=5_000)

        wait_task = asyncio.create_task(got_one.wait())

        try:
            await asyncio.wait_for(wait_task, timeout=6)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return None
        finally:
            if not wait_task.done():
                wait_task.cancel()
                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass

        if captured:
            log.info(f"URL {url_num}) Captured M3U8")
            return captured[0]

        log.warning(f"URL {url_num}) No M3U8 captured.")
        return None

    except Exception as e:
        log.warning(f"URL {url_num}) {e}")
        return None

    finally:
        page.remove_listener("request", handler)


# -------------------------------------------------
# XML CACHE
# -------------------------------------------------

async def refresh_xml_cache(now_ts: float):
    log.info("Refreshing XML cache")

    events = {}

    if not (xml_data := await network.request(SXLIVE_BASE_URL, log=log)):
        return events

    feed = feedparser.parse(xml_data.content)

    for entry in feed.entries:
        date = entry.get("published")
        link = entry.get("link")
        title = entry.get("title")
        summary = entry.get("summary")

        if not all([date, link, title, summary]):
            continue

        sprt = summary.split(".", 1)
        sport, league = sprt[0], "".join(sprt[1:]).strip()

        event_dt = Time.from_str(date)

        key = f"[{sport} - {league}] {title} ({TAG})"

        events[key] = {
            "sport": sport,
            "league": league,
            "event": title,
            "link": link.replace("livetv.sx", "livetv873.me"),
            "event_ts": event_dt.timestamp(),
            "timestamp": now_ts,
        }

    return events


async def get_events(cached_keys):
    now = Time.clean(Time.now())

    if not (events := XML_CACHE.load()):
        events = await refresh_xml_cache(now.timestamp())
        XML_CACHE.write(events)

    start_ts = now.delta(hours=-1).timestamp()
    end_ts = now.delta(minutes=5).timestamp()

    live = []

    for k, v in events.items():
        if k in cached_keys:
            continue

        if (
            v["sport"] not in VALID_SPORTS
            and v["league"] not in VALID_SPORTS
            and v["event"].lower() != "olympic games"
        ):
            continue

        if not start_ts <= v["event_ts"] <= end_ts:
            continue

        live.append(v)

    return live


# -------------------------------------------------
# PLAYLIST GENERATION
# -------------------------------------------------

def generate_playlists(data: dict):
    encoded_ua = urllib.parse.quote(USER_AGENT, safe="")

    vlc = ["#EXTM3U"]
    tivimate = ["#EXTM3U"]

    for key, entry in sorted(data.items()):
        if not entry.get("url"):
            continue

        extinf = (
            f'#EXTINF:-1 tvg-id="{entry.get("id")}" '
            f'tvg-logo="{entry.get("logo")}" '
            f'group-title="{entry.get("sport")}",{key}'
        )

        # VLC
        vlc.append(extinf)
        vlc.append(f"#EXTVLCOPT:http-referrer={SXLIVE_BASE_REF}")
        vlc.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc.append(entry["url"])
        vlc.append("")

        # TiviMate
        tivimate.append(extinf)
        tivimate.append(
            f'{entry["url"]}|referer={SXLIVE_BASE_REF}&user-agent={encoded_ua}'
        )
        tivimate.append("")

    with open("sxlive_vlc.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(vlc))

    with open("sxlive_tivimate.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(tivimate))


# -------------------------------------------------
# SCRAPER (RESTORED ORIGINAL STRUCTURE)
# -------------------------------------------------

async def scrape(browser: Browser):
    cached_urls = CACHE_FILE.load()
    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    urls.update(valid_urls)

    log.info(f"Loaded {len(valid_urls)} cached events")

    events = await get_events(cached_urls.keys())

    if events:
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, ignore_https=True) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        process_event,
                        url=ev["link"],
                        url_num=i,
                        page=page,
                    )

                    stream = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                        timeout=20,
                    )

                    sport, league, event, ts = (
                        ev["sport"],
                        ev["league"],
                        ev["event"],
                        ev["event_ts"],
                    )

                    key = f"[{sport} - {league}] {event} ({TAG})"
                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": stream,
                        "logo": logo,
                        "base": SXLIVE_BASE_REF,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": ev["link"],
                        "sport": sport,
                    }

                    cached_urls[key] = entry

                    if stream:
                        urls[key] = entry

    CACHE_FILE.write(cached_urls)
    
# GENERATE FILES
    generate_playlists(cached_urls)

    log.info("Generated sxlive_vlc.m3u8 and sxlive_tivimate.m3u8")

# -------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
