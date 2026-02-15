#!/usr/bin/env python3
import asyncio
import os
import re
from functools import partial
from urllib.parse import urljoin, quote

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# ---------------- CONFIG ----------------
TAG = "XEAST"

BASE_URL = os.environ.get("XEAST_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing XEAST_BASE_URL secret")

OUTPUT_VLC = "east_vlc.m3u8"
OUTPUT_TIVI = "east_tivimate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
ENCODED_UA = quote(USER_AGENT, safe="")

CACHE_FILE = Cache(TAG, exp=10_800)

SPORT_ENDPOINTS = ["mma", "nba", "nhl", "soccer", "wwe"]

urls: dict[str, dict] = {}


# ---------------- SCRAPER ----------------
async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:

    # 🔧 FIX 1 — Ensure absolute URL
    if not url.startswith("http"):
        url = urljoin(BASE_URL, url)

    valid_m3u8 = re.compile(r'(var|const)\s+\w+\s*=\s*"([^"]+)"', re.I)
    nones = None, None

    if not (html := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load event page")
        return nones

    soup = HTMLParser(html.content)

    # 🔧 FIX 2 — Iterate ALL iframes (not just first)
    iframes = soup.css("iframe")

    if not iframes:
        log.warning(f"URL {url_num}) No iframe found")
        return nones

    for iframe in iframes:

        iframe_src = iframe.attributes.get("src")

        if (
            not iframe_src
            or iframe_src == "about:blank"
            or "ads" in iframe_src
            or "doubleclick" in iframe_src
        ):
            continue

        # 🔧 FIX 3 — Normalize iframe src
        if iframe_src.startswith("//"):
            iframe_src = "https:" + iframe_src
        elif iframe_src.startswith("/"):
            iframe_src = urljoin(url, iframe_src)
        elif not iframe_src.startswith("http"):
            iframe_src = urljoin(url, iframe_src)

        if not (iframe_html := await network.request(iframe_src, log=log)):
            continue

        if not (m := valid_m3u8.search(iframe_html.text)):
            continue

        encoded = m.group(2)

        try:
            stream = bytes.fromhex(encoded).decode("utf-8")
        except Exception:
            continue

        log.info(f"URL {url_num}) Captured M3U8")
        return stream, iframe_src

    log.warning(f"URL {url_num}) No valid player iframe found")
    return nones


# ---------------- EVENTS ----------------
async def get_events(cached_keys):
    tasks = [
        network.request(urljoin(BASE_URL, f"categories/{sport}/"), log=log)
        for sport in SPORT_ENDPOINTS
    ]

    pages = await asyncio.gather(*tasks)
    events = []

    for page in pages:
        if not page:
            continue

        soup = HTMLParser(page.content)
        sport = "Live Event"

        if h := soup.css_first("h1.text-3xl"):
            sport = h.text(strip=True).split("Streams")[0].strip()

        for card in soup.css("article.game-card"):
            team = card.css_first("h2.text-xl.font-semibold")
            link = card.css_first("a.stream-button")
            live = card.css_first("span.bg-green-600")

            if not (team and link and live and live.text(strip=True) == "LIVE"):
                continue

            name = team.text(strip=True)
            href = link.attributes.get("href")

            if not href:
                continue

            # 🔧 FIX 4 — Normalize event link
            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)

            key = f"[{sport}] {name} ({TAG})"
            if key in cached_keys:
                continue

            events.append({
                "sport": sport,
                "event": name,
                "link": href,
            })

    return events


# ---------------- MAIN ----------------
async def scrape():
    cached = CACHE_FILE.load()
    urls.update({k: v for k, v in cached.items() if v.get("url")})

    log.info(f"Loaded {len(urls)} event(s) from cache")
    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached.keys())
    log.info(f"Processing {len(events)} new URL(s)")

    now = Time.clean(Time.now()).timestamp()

    for i, ev in enumerate(events, 1):
        handler = partial(process_event, ev["link"], i)

        result = await network.safe_process(
            handler,
            url_num=i,
            semaphore=network.HTTP_S,
            log=log,
        )

        if not result:
            continue

        url, iframe = result
        if not url:
            continue

        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        key = f"[{ev['sport']}] {ev['event']} ({TAG})"
        urls[key] = cached[key] = {
            "url": url,
            "base": iframe,
            "logo": logo,
            "id": tvg_id or "Live.Event.us",
            "sport": ev["sport"],
            "event": ev["event"],
            "timestamp": now,
        }

    CACHE_FILE.write(cached)
    write_playlists()


# ---------------- PLAYLISTS ----------------
def write_playlists():
    vlc, tivi = ["#EXTM3U"], ["#EXTM3U"]

    for key, e in urls.items():
        title = f"[{e['sport']}] {e['event']} ({TAG})"
        referer = e["base"]

        extinf = (
            f'#EXTINF:-1 tvg-id="{e["id"]}" '
            f'tvg-name="{title}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{title}'
        )

        vlc.extend([
            extinf,
            f"#EXTVLCOPT:http-referrer={referer}",
            f"#EXTVLCOPT:http-origin={referer}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        tivi.extend([
            extinf,
            f'{e["url"]}|referer={referer}|origin={referer}|user-agent={ENCODED_UA}',
        ])

    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("\n".join(vlc) + "\n")

    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("\n".join(tivi) + "\n")

    log.info(f"Generated {OUTPUT_VLC} and {OUTPUT_TIVI}")


if __name__ == "__main__":
    asyncio.run(scrape())
