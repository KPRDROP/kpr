#!/usr/bin/env python3
import base64
import json
import re
from functools import partial
from pathlib import Path
from urllib.parse import quote

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
TAG = "STRMBTW"

BASE_URLS = [
    "https://hiteasport.info",
    "https://streambtw.com",
]

REFERER = "https://hiteasport.info/"
ORIGIN = "https://hiteasport.info"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
UA_ENC = quote(USER_AGENT)

OUT_VLC = Path("Streambtw_VLC.m3u8")
OUT_TIVI = Path("Streambtw_TiviMate.m3u8")

CACHE_FILE = Cache(TAG, exp=3600)

urls: dict[str, dict[str, str | float]] = {}

# --------------------------------------------------
def fix_league(s: str) -> str:
    pattern = re.compile(r"^\w*-\w*", re.I)
    return " ".join(s.split("-")) if pattern.search(s) else s

# --------------------------------------------------
async def process_event(url: str, url_num: int) -> str | None:
    if not (html := await network.request(url, log=log)):
        return None

    m = re.search(r'var\s+\w+\s*=\s*"([^"]+)"', html.text, re.I)
    if not m:
        log.info(f"URL {url_num}) No M3U8 found")
        return None

    stream = m.group(1)

    if not stream.startswith("http"):
        stream = base64.b64decode(stream).decode("utf-8")

    log.info(f"URL {url_num}) Captured M3U8")
    return stream

# --------------------------------------------------
async def get_events() -> list[dict[str, str]]:
    events: list[dict[str, str]] = []

    for base in BASE_URLS:
        log.info(f'Scraping from "{base}"')

        if not (html := await network.request(base, log=log)):
            continue

        soup = HTMLParser(html.content)

        script_text = None
        for s in soup.css("script"):
            t = s.text() or ""
            if "const DATA" in t:
                script_text = t
                break

        if not script_text:
            continue

        m = re.search(
            r"const\s+DATA\s*=\s*(\[\s*.*?\s*\]);",
            script_text,
            re.S,
        )
        if not m:
            continue

        # 🔑 ORIGINAL WORKING NORMALIZATION (DO NOT TOUCH)
        data_js = m[1].replace("\n      ", "").replace("\n    ", "")
        s1 = re.sub(r"{\s", '{"', data_js)
        s2 = re.sub(r':"', '":"', s1)
        s3 = re.sub(r":\[", '":[', s2)
        s4 = re.sub(r"},\]", "}]", s3)
        s5 = re.sub(r'",\s', '","', s4)

        try:
            data: list[dict[str, str]] = json.loads(s5)
        except Exception as e:
            log.warning(f"DATA parse failed: {e}")
            continue

        for matches in data:
            league = matches["title"]
            items = matches["items"]

            for info in items:
                events.append({
                    "sport": fix_league(league),
                    "event": info["title"],
                    "link": info["url"],
                })

        if events:
            break  # stop after first valid base

    return events

# --------------------------------------------------
def build_playlists(data: dict[str, dict]):
    vlc = ["#EXTM3U"]
    tiv = ["#EXTM3U"]

    ch = 1
    for name, e in data.items():
        # VLC
        vlc.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )
        vlc.append(f"#EXTVLCOPT:http-referrer={REFERER}")
        vlc.append(f"#EXTVLCOPT:http-origin={ORIGIN}")
        vlc.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc.append(e["url"])

        # TiviMate
        tiv.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )
        tiv.append(
            f'{e["url"]}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}'
        )

        ch += 1

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")
    OUT_TIVI.write_text("\n".join(tiv), encoding="utf-8")

    log.info("Playlists written: Streambtw_VLC.m3u8, Streambtw_TiviMate.m3u8")

# --------------------------------------------------
async def scrape():
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
    else:
        events = await get_events()
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(process_event, ev["link"], i)

            url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            if not url:
                continue

            key = f"[{ev['sport']}] {ev['event']} ({TAG})"
            tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

            urls[key] = {
                "url": url,
                "logo": logo,
                "base": ev["link"],
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
                "link": ev["link"],
            }

        CACHE_FILE.write(urls)

    build_playlists(urls)

# --------------------------------------------------
if __name__ == "__main__":
    import asyncio
    asyncio.run(scrape())
