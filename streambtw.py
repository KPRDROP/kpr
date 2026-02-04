import base64
import re
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
TAG = "STRMBTW"
BASE_URL = "https://hiteasport.info/"

CACHE_FILE = Cache(TAG, exp=3600)

urls: dict[str, dict[str, str | float]] = {}

# --------------------------------------------------
EVENT_RE = re.compile(
    r"""
    league\s*:\s*"(?P<league>[^"]+)"|
    title\s*:\s*"(?P<title>[^"]+)"|
    url\s*:\s*"(?P<url>/[^"]+)"
    """,
    re.VERBOSE | re.IGNORECASE,
)

STREAM_VAR_RE = re.compile(
    r'var\s+\w+\s*=\s*"([^"]+)"', re.IGNORECASE
)

# --------------------------------------------------
def fix_league(s: str) -> str:
    return " ".join(s.split("-"))

# --------------------------------------------------
async def process_event(url: str, url_num: int) -> str | None:
    if not (html := await network.request(url, log=log)):
        return None

    if not (m := STREAM_VAR_RE.search(html.text)):
        log.info(f"URL {url_num}) No M3U8 found")
        return None

    stream = m.group(1)

    if not stream.startswith("http"):
        try:
            stream = base64.b64decode(stream).decode("utf-8")
        except Exception:
            return None

    log.info(f"URL {url_num}) Captured M3U8")
    return stream

# --------------------------------------------------
async def get_events() -> list[dict[str, str]]:
    events = []

    html = await network.request(BASE_URL, log=log)
    if not html:
        return events

    soup = HTMLParser(html.text)

    # 🔥 NEW: scan scripts instead of DOM
    scripts = "\n".join(
        s.text() for s in soup.css("script") if s.text()
    )

    matches = []
    current = {}

    for m in EVENT_RE.finditer(scripts):
        if m.group("league"):
            current["sport"] = fix_league(m.group("league"))
        elif m.group("title"):
            current["event"] = m.group("title")
        elif m.group("url"):
            current["link"] = urljoin(BASE_URL, m.group("url"))

        if len(current) == 3:
            matches.append(current)
            current = {}

    for ev in matches:
        events.append(ev)

    return events

# --------------------------------------------------
async def scrape() -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
        return

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events()
    log.info(f"Processing {len(events)} new URL(s)")

    if not events:
        return

    now = Time.clean(Time.now())

    for i, ev in enumerate(events, 1):
        handler = partial(
            process_event,
            url=ev["link"],
            url_num=i,
        )

        url = await network.safe_process(
            handler,
            url_num=i,
            semaphore=network.HTTP_S,
            log=log,
        )

        if not url:
            continue

        sport, event, link = ev["sport"], ev["event"], ev["link"]
        key = f"[{sport}] {event} ({TAG})"

        tvg_id, logo = leagues.get_tvg_info(sport, event)

        urls[key] = {
            "url": url,
            "logo": logo,
            "base": BASE_URL,
            "timestamp": now.timestamp(),
            "id": tvg_id or "Live.Event.us",
            "link": link,
        }

    log.info(f"Collected {len(urls)} event(s)")
    CACHE_FILE.write(urls)
