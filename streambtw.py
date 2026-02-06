#!/usr/bin/env python3
import base64
import re
from functools import partial
from urllib.parse import urljoin, quote
from pathlib import Path

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

urls: dict[str, dict] = {}

M3U8_VAR_RE = re.compile(r'var\s+\w+\s*=\s*"([^"]+)"', re.IGNORECASE)

# --------------------------------------------------
def fix_league(s: str) -> str:
    return " ".join(s.split("-"))

# --------------------------------------------------
async def process_event(url: str, url_num: int) -> str | None:
    html = await network.request(url, log=log)
    if not html:
        return None

    if not (m := M3U8_VAR_RE.search(html.text)):
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

    for base in BASE_URLS:
        log.info(f'Scraping from "{base}"')

        html = await network.request(base, log=log)
        if not html:
            continue

        soup = HTMLParser(html.content)

        # ---------------------------
        # ✅ NEW LAYOUT (league cards)
        # ---------------------------
        for card in soup.css(".league"):
            league_el = card.css_first(".league-title")
            if not league_el:
                continue

            league = fix_league(league_el.text(strip=True))

            for ev in card.css(".match"):
                name_el = ev.css_first(".match-name")
                btn = ev.css_first("a.watch-btn")

                if not (name_el and btn):
                    continue

                href = btn.attributes.get("href")
                if not href:
                    continue

                events.append({
                    "sport": league,
                    "event": name_el.text(strip=True),
                    "link": urljoin(base, href),
                    "base": base,
                })

        # ---------------------------
        # 🔁 FALLBACK: legacy layout
        # ---------------------------
        if not events:
            for item in soup.css(".t-item"):
                league_el = item.css_first(".t-league")
                match_el = item.css_first(".t-match")
                watch_el = item.css_first("a.t-watch")

                if not (league_el and match_el and watch_el):
                    continue

                href = watch_el.attributes.get("href")
                if not href:
                    continue

                events.append({
                    "sport": fix_league(league_el.text(strip=True)),
                    "event": match_el.text(strip=True),
                    "link": urljoin(base, href),
                    "base": base,
                })

        if events:
            break  # stop after first working base

    return events

# --------------------------------------------------
def build_playlists(data: dict[str, dict]):
    # VLC
    vlc = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        vlc.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )
        vlc.append(f"#EXTVLCOPT:http-referrer={REFERER}")
        vlc.append(f"#EXTVLCOPT:http-origin={ORIGIN}")
        vlc.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        vlc.append(e["url"])
        ch += 1

    OUT_VLC.write_text("\n".join(vlc), encoding="utf-8")

    # TiviMate
    tm = ["#EXTM3U"]
    ch = 1

    for name, e in data.items():
        tm.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name}'
        )
        tm.append(
            f'{e["url"]}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}'
        )
        ch += 1

    OUT_TIVI.write_text("\n".join(tm), encoding="utf-8")

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
                "base": ev["base"],
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
