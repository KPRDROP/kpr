import asyncio
from functools import partial
from urllib.parse import quote
import os

import feedparser
from playwright.async_api import Browser, Page

# ✅ FIX: absolute imports (no relative import)
from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "LIVETVSX"

CACHE_FILE = Cache(TAG, exp=10_800)
XML_CACHE = Cache(f"{TAG}-xml", exp=28_000)

# ✅ FROM SECRETS
BASE_URL = os.environ.get("SXLIVE_BASE_URL")
BASE_REF = os.environ.get("SXLIVE_BASE_REF")

if not BASE_URL or not BASE_REF:
    raise RuntimeError("❌ Missing SXLIVE_BASE_URL or SXLIVE_BASE_REF secret")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
    "Gecko/20100101 Firefox/146.0"
)

OUTPUT_VLC = "sxlive_vlc.m3u8"
OUTPUT_TIVI = "sxlive_tivimate.m3u8"

VALID_SPORTS = {
    "Football",
    "Basketball",
    "Ice Hockey",
    
}

# -------------------------------------------------
async def process_event(url: str, url_num: int, page: Page) -> str | None:
    captured: list[str] = []
    got_one = asyncio.Event()

    handler = partial(
        network.capture_req,
        captured=captured,
        got_one=got_one,
    )

    page.on("request", handler)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(1_500)

        buttons = await page.query_selector_all(".lnktbj a[href*='webplayer']")
        labels = await page.eval_on_selector_all(
            ".lnktyt span",
            "els => els.map(e => e.textContent.trim().toLowerCase())",
        )

        for btn, label in zip(buttons, labels):
            if label in {"web", "youtube"}:
                continue

            href = await btn.get_attribute("href")
            if href:
                break
        else:
            log.warning(f"URL {url_num}) No valid sources found.")
            return None

        href = href if href.startswith("http") else f"https:{href}"

        await page.goto(href, wait_until="domcontentloaded", timeout=5_000)

        try:
            await asyncio.wait_for(got_one.wait(), timeout=6)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return None

        if captured:
            log.info(f"URL {url_num}) Captured M3U8")
            return captured[0]

        log.warning(f"URL {url_num}) No M3U8 captured.")
        return None

    except Exception as e:
        log.warning(f"URL {url_num}) Exception: {e}")
        return None

    finally:
        page.remove_listener("request", handler)

# -------------------------------------------------
async def refresh_xml_cache(now_ts: float) -> dict:
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

        sport, *rest = summary.split(".", 1)
        league = rest[0].strip() if rest else ""

        if sport not in VALID_SPORTS:
            continue

        ts = Time.from_str(date).timestamp()
        key = f"[{sport} - {league}] {title} ({TAG})"

        events[key] = {
            "sport": sport,
            "league": league,
            "event": title,
            "link": link,
            "event_ts": ts,
            "timestamp": now_ts,
        }

    return events

# -------------------------------------------------
async def get_events(cached_keys):
    now = Time.clean(Time.now())

    events = XML_CACHE.load() or await refresh_xml_cache(now.timestamp())
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
def write_playlists():
    ua_enc = quote(USER_AGENT)

    vlc = ["#EXTM3U"]
    tivi = ["#EXTM3U"]

    for chno, (name, e) in enumerate(sorted(urls.items()), 1):
        if not e.get("url"):
            continue

        vlc.extend([
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}',
            f"#EXTVLCOPT:http-referrer={BASE_REF}",
            f"#EXTVLCOPT:http-origin={BASE_REF}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        tivi.extend([
            f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}',
            f'{e["url"]}|referer={BASE_REF}|origin={BASE_REF}|user-agent={ua_enc}',
        ])

    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("\n".join(vlc))

    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("\n".join(tivi))

    log.info("✅ SXLive playlists written")

# -------------------------------------------------
async def scrape(browser: Browser):
    cached = CACHE_FILE.load()
    urls.update({k: v for k, v in cached.items() if v.get("url")})

    events = await get_events(cached.keys())

    if events:
        async with network.event_context(browser, ignore_https=True) as ctx:
            for i, ev in enumerate(events, 1):
                async with network.event_page(ctx) as page:
                    url = await process_event(ev["link"], i, page)

                    tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])
                    key = f"[{ev['sport']} - {ev['league']}] {ev['event']} ({TAG})"

                    cached[key] = {
                        "url": url,
                        "logo": logo,
                        "base": BASE_REF,
                        "timestamp": ev["event_ts"],
                        "id": tvg_id or "Live.Event.us",
                        "link": ev["link"],
                    }

                    if url:
                        urls[key] = cached[key]

    CACHE_FILE.write(cached)
    write_playlists()

# -------------------------------------------------
if __name__ == "__main__":
    asyncio.run(network.run(scrape))
