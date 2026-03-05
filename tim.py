import os
from functools import partial
from typing import Any
from urllib.parse import urljoin, quote

from playwright.async_api import Browser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIMSTRMS"

CACHE_FILE = Cache(TAG, exp=10_800)
API_FILE = Cache(f"{TAG}-api", exp=19_800)

# -------------------------------------------------
# SECRETS
# -------------------------------------------------

API_URL = os.environ.get("TIM_API_URL")
BASE_URL = os.environ.get("TIM_BASE_URL")

if not API_URL:
    raise RuntimeError("Missing TIM_API_URL secret")

if not BASE_URL:
    raise RuntimeError("Missing TIM_BASE_URL secret")

# -------------------------------------------------
# PLAYLIST SETTINGS
# -------------------------------------------------

OUT_VLC = "tim_vlc.m3u8"
OUT_TIVI = "tim_tivimate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)

UA_ENC = quote(USER_AGENT)

# -------------------------------------------------

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


# -------------------------------------------------
# PLAYLIST WRITER
# -------------------------------------------------

def write_playlists(data: dict):

    vlc_lines = ["#EXTM3U"]
    tiv_lines = ["#EXTM3U"]

    for name, e in data.items():

        url = e["url"]
        ref = e["base"]

        if not url:
            continue

        extinf = (
            f'#EXTINF:-1 tvg-chno="" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )

        # ----------------
        # VLC FORMAT
        # ----------------

        vlc_lines.append(extinf)
        vlc_lines.append(f"#EXTVLCOPT:http-referrer={ref}")
        vlc_lines.append(f"#EXTVLCOPT:http-origin={ref}")
        vlc_lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc_lines.append(url)

        # ----------------
        # TIVIMATE FORMAT
        # ----------------

        tiv_lines.append(extinf)

        tiv_lines.append(
            f"{url}|referer={ref}|origin={ref}|user-agent={UA_ENC}"
        )

    with open(OUT_VLC, "w", encoding="utf-8") as f:
        f.write("\n".join(vlc_lines))

    with open(OUT_TIVI, "w", encoding="utf-8") as f:
        f.write("\n".join(tiv_lines))

    log.info("Playlists written successfully")


# -------------------------------------------------
# EVENTS FROM API
# -------------------------------------------------

async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):

        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(API_URL, log=log):
            api_data: list[dict] = r.json()
            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    for info in api_data:

        if not (category := info.get("category")) or category != "Events":
            continue

        stream_events: list[dict[str, Any]] = info["events"]

        for ev in stream_events:

            if (genre := ev["genre"]) not in SPORT_GENRES:
                continue

            event_dt = Time.from_str(ev["time"], timezone="EST")

            if not start_dt <= event_dt <= end_dt:
                continue

            name: str = ev["name"]
            url_id: str = ev["URL"]
            logo: str | None = ev.get("logo")

            sport = SPORT_GENRES[genre]

            if f"[{sport}] {name} ({TAG})" in cached_keys:
                continue

            if not (streams := ev["streams"]) or not (url := streams[0].get("url")):
                continue

            events.append(
                {
                    "sport": sport,
                    "event": name,
                    "link": urljoin(BASE_URL, f"watch?id={url_id}"),
                    "ref": url,
                    "logo": logo,
                    "timestamp": event_dt.timestamp(),
                }
            )

    return events


# -------------------------------------------------
# SCRAPER
# -------------------------------------------------

async def scrape(browser: Browser) -> None:

    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):

        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:

            for i, ev in enumerate(events, start=1):

                async with network.event_page(context) as page:

                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        log=log,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    sport, event, logo, ref, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                        ev["ref"],
                        ev["timestamp"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": ref,
                        "timestamp": ts,
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

    # WRITE PLAYLISTS
    write_playlists(urls)
