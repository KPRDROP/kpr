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
    "Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
)
ENCODED_UA = quote(USER_AGENT, safe="")

CACHE_FILE = Cache(TAG, exp=10_800)

SPORT_ENDPOINTS = ["mma", "nba", #"nfl", 
                   "nhl", "soccer", "wwe"]

urls: dict[str, dict] = {}

# ---------------- SCRAPER ----------------
async def process_event(url: str, url_num: int):
    pattern = re.compile(r'var\s+\w+\s*=\s*"([^"]+)"', re.IGNORECASE)

    if not (html := await network.request(url, log=log)):
        return None, None

    soup = HTMLParser(html.content)
    iframe = soup.css_first("iframe")

    if not iframe:
        log.warning(f"URL {url_num}) No iframe found")
        return None, None

    src = iframe.attributes.get("src", "").strip()

    # 🔒 HARD BLOCK BAD IFRAMES
    if not src or src in {"about:blank", "/blank"} or not src.startswith("http"):
        log.warning(f"URL {url_num}) Invalid iframe src: {src}")
        return None, None

    if not (iframe_html := await network.request(src, log=log)):
        return None, None

    if not (m := pattern.search(iframe_html.text)):
        log.warning(f"URL {url_num}) No Clappr source found")
        return None, None

    log.info(f"URL {url_num}) Captured M3U8")
    return bytes.fromhex(m[1]).decode("utf-8"), src


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
            team = card.css_first("h2.text-xl")
            link = card.css_first("a.stream-button")
            live = card.css_first("span.bg-green-600")

            if not (team and link and live and live.text(strip=True) == "LIVE"):
                continue

            name = team.text(strip=True)
            href = link.attributes.get("href")

            key = f"[{sport}] {name} ({TAG})"
            if key in cached_keys:
                continue

            events.append({"sport": sport, "event": name, "link": href})

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

        # 🛡️ SAFE UNPACK
        if not result:
            continue

        url, iframe = result
        if not url or not iframe:
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

        # VLC
        vlc.extend([
            extinf,
            f"#EXTVLCOPT:http-referrer={referer}",
            f"#EXTVLCOPT:http-origin={referer}",
            f"#EXTVLCOPT:http-user-agent={USER_AGENT}",
            e["url"],
        ])

        # TiviMate
        tivi.extend([
            extinf,
            f'{e["url"]}'
            f'|referer={referer}'
            f'|origin={referer}'
            f'|user-agent={ENCODED_UA}',
        ])

    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("\n".join(vlc) + "\n")

    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("\n".join(tivi) + "\n")

    log.info(f"Generated {OUTPUT_VLC} and {OUTPUT_TIVI}")


if __name__ == "__main__":
    asyncio.run(scrape())
