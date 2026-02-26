import asyncio
import os
import urllib.parse
from functools import partial

import feedparser
from playwright.async_api import async_playwright, Page

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

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

urls: dict[str, dict] = {}

VALID_SPORTS = [
    "MLB. Preseason",
    "MLB",
    "Basketball",
    "Football",
    "Ice Hockey",
]

# -------------------------------------------------
# PROCESS EVENT (FIXED USING ORIGINAL LOGIC)
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
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(1500)

        buttons = await page.query_selector_all(".lnktbj a[href*='webplayer']")

        labels = await page.eval_on_selector_all(
            ".lnktyt span",
            "els => els.map(e => e.textContent.trim().toLowerCase())",
        )

        target_href = None

        for btn, label in zip(buttons, labels):
            if label in ["web", "youtube"]:
                continue

            href = await btn.get_attribute("href")
            if href:
                target_href = href
                break

        if not target_href:
            log.warning(f"URL {url_num}) No valid webplayer found")
            return None

        if not target_href.startswith("http"):
            target_href = f"https:{target_href}"

        target_href = target_href.replace("livetv.sx", "livetv873.me")

        await page.goto(target_href, wait_until="domcontentloaded", timeout=10_000)

        try:
            await asyncio.wait_for(got_one.wait(), timeout=10)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return None

        if captured:
            log.info(f"URL {url_num}) Captured M3U8")
            return captured[0]

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

    xml = await network.request(SXLIVE_BASE_URL, log=log)
    if not xml:
        return events

    feed = feedparser.parse(xml.content)

    for entry in feed.entries:
        title = entry.get("title")
        link = entry.get("link")
        summary = entry.get("summary")
        date = entry.get("published")

        if not all([title, link, summary, date]):
            continue

        sprt = summary.split(".", 1)
        sport = sprt[0]
        league = sprt[1].strip() if len(sprt) > 1 else ""

        if sport not in VALID_SPORTS and league not in VALID_SPORTS:
            continue

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

# -------------------------------------------------
# GET EVENTS
# -------------------------------------------------

async def get_events(cached_keys):
    now = Time.clean(Time.now())

    events = XML_CACHE.load()
    if not events:
        events = await refresh_xml_cache(now.timestamp())
        XML_CACHE.write(events)

    live = []

    for k, v in events.items():
        if k in cached_keys:
            continue
        live.append(v)

    return live

# -------------------------------------------------
# PLAYLIST GENERATOR
# -------------------------------------------------

def generate_playlists(data: dict):
    encoded_ua = urllib.parse.quote(USER_AGENT, safe="")

    vlc_lines = ["#EXTM3U"]
    tivimate_lines = ["#EXTM3U"]

    for key, entry in sorted(data.items()):
        url = entry.get("url")
        if not url:
            continue

        extinf = (
            f'#EXTINF:-1 tvg-id="{entry.get("id")}" '
            f'tvg-logo="{entry.get("logo")}" '
            f'group-title="{entry.get("sport")}",{key}'
        )

        # VLC
        vlc_lines.append(extinf)
        vlc_lines.append(f"#EXTVLCOPT:http-referrer={SXLIVE_BASE_REF}")
        vlc_lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc_lines.append(url)
        vlc_lines.append("")

        # TiviMate
        tivimate_lines.append(extinf)
        tivimate_lines.append(
            f"{url}|referer={SXLIVE_BASE_REF}&user-agent={encoded_ua}"
        )
        tivimate_lines.append("")

    with open("sxlive_vlc.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(vlc_lines))

    with open("sxlive_tivimate.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(tivimate_lines))

# -------------------------------------------------
# SCRAPER
# -------------------------------------------------

async def scrape():
    cached = CACHE_FILE.load()
    valid = {k: v for k, v in cached.items() if v.get("url")}
    urls.update(valid)

    log.info(f"Loaded {len(valid)} cached events")

    events = await get_events(cached.keys())
    log.info(f"Processing {len(events)} event(s)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent=USER_AGENT,
        )

        page = await context.new_page()

        for i, ev in enumerate(events, 1):
            stream = await process_event(ev["link"], i, page)

            key = f"[{ev['sport']} - {ev['league']}] {ev['event']} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

            cached[key] = {
                "url": stream,
                "logo": logo,
                "base": SXLIVE_BASE_REF,
                "timestamp": ev["event_ts"],
                "id": tvg_id or "Live.Event.us",
                "link": ev["link"],
                "sport": ev["sport"],
            }

            if stream:
                urls[key] = cached[key]

        await browser.close()

    CACHE_FILE.write(cached)

    # GENERATE FILES
    generate_playlists(cached)
    log.info("Generated sxlive_vlc.m3u8 and sxlive_tivimate.m3u8")

# -------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
