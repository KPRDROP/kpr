import asyncio
import os
from functools import partial

import feedparser
from playwright.async_api import async_playwright, Page

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

TAG = "LIVETVSX"

CACHE_FILE = Cache(TAG, exp=10_800)
XML_CACHE = Cache(f"{TAG}-xml", exp=28_000)

# -------------------------------------------------
# ✅ SECRETS FROM ENVIRONMENT (GitHub Actions)
# -------------------------------------------------
BASE_URL = os.environ.get("BASE_URL")
BASE_REF = os.environ.get("BASE_REF")

if not BASE_URL or not BASE_REF:
    raise RuntimeError(
        "Missing required secrets: BASE_URL and/or BASE_REF"
    )

VALID_SPORTS = {
    "Football",
    "Basketball",
    "Ice Hockey",
}

urls = {}

# -------------------------------------------------

async def process_event(url: str, url_num: int, page: Page) -> str | None:
    captured = set()
    got_one = asyncio.Event()

    def capture(req):
        try:
            if ".m3u8" in req.url.lower():
                captured.add(req.url)
                got_one.set()
        except Exception:
            pass

    page.context.on("requestfinished", capture)
    page.context.on("response", lambda r: capture(r.request))

    try:
        await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4_000)

        # Trigger playback multiple times
        for _ in range(3):
            for frame in page.frames:
                try:
                    await frame.evaluate("""
                        () => {
                            const v = document.querySelector('video');
                            if (v) {
                                v.muted = true;
                                v.play();
                            }
                            document.body?.click();
                        }
                    """)
                except Exception:
                    pass
            await page.wait_for_timeout(2_000)

        try:
            await asyncio.wait_for(got_one.wait(), timeout=30)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return None

        if captured:
            stream = sorted(captured)[0]
            log.info(f"URL {url_num}) Captured M3U8")
            return stream

        log.warning(f"URL {url_num}) No valid source")
        return None

    except Exception as e:
        log.warning(f"URL {url_num}) Exception: {e}")
        return None

    finally:
        page.context.remove_listener("requestfinished", capture)

# -------------------------------------------------

async def refresh_xml_cache(now_ts: float):
    log.info("Refreshing XML cache")

    events = {}
    xml = await network.request(BASE_URL, log=log)

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

        sport, *league = summary.split(".", 1)
        if sport not in VALID_SPORTS:
            continue

        event_ts = Time.from_str(date).timestamp()
        league = league[0].strip() if league else ""

        key = f"[{sport} - {league}] {title} ({TAG})"

        events[key] = {
            "sport": sport,
            "league": league,
            "event": title,
            "link": link,
            "event_ts": event_ts,
            "timestamp": now_ts,
        }

    return events

# -------------------------------------------------

async def get_events(cached_keys):
    now = Time.clean(Time.now())

    events = XML_CACHE.load()
    if not events:
        events = await refresh_xml_cache(now.timestamp())
        XML_CACHE.write(events)

    live = []
    start_ts = now.delta(hours=-1).timestamp()
    end_ts = now.delta(minutes=5).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue
        if start_ts <= v["event_ts"] <= end_ts:
            live.append(v)

    return live

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
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()

        for i, ev in enumerate(events, 1):
            stream = await process_event(ev["link"], i, page)

            key = f"[{ev['sport']} - {ev['league']}] {ev['event']} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

            cached[key] = {
                "url": stream,
                "logo": logo,
                "base": BASE_REF,
                "timestamp": ev["event_ts"],
                "id": tvg_id or "Live.Event.us",
                "link": ev["link"],
            }

            if stream:
                urls[key] = cached[key]

        await browser.close()

    CACHE_FILE.write(cached)

# -------------------------------------------------

if __name__ == "__main__":
    asyncio.run(scrape())
