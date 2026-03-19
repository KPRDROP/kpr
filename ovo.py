import asyncio
import re
import urllib.parse
from functools import partial
from urllib.parse import urljoin
import os

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "VOLOKIT"

CACHE_FILE = Cache(TAG, exp=10_800)
HTML_CACHE = Cache(f"{TAG}-html", exp=28_800)

BASE_URL = "http://volokit.xyz"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)

SPORT_ENDPOINTS = {
    "boxing": "BOXING",
    "mlb": "MLB",
    "nba": "NBA",
    "mls": "MLS",
    "nhl": "NHL",
    "race": "RACE",
    "ufc": "UFC",
}


# =========================
# PLAYLIST GENERATOR
# =========================

def _format_extinf(key: str, entry: dict) -> str:
    return (
        f'#EXTINF:-1 tvg-id="{entry.get("id","")}" '
        f'tvg-logo="{entry.get("logo","")}" '
        f'group-title="{entry.get("sport","Live")}",{key}'
    )


def generate_vlc_playlist(urls: dict, output_file="ovo_vlc.m3u8"):
    lines = ["#EXTM3U"]
    count = 0

    for key, entry in sorted(urls.items()):
        stream_url = entry.get("url")
        if not stream_url:
            continue

        lines.append(_format_extinf(key, entry))
        lines.append(f"#EXTVLCOPT:http-referrer={BASE_URL}/")
        lines.append(f"#EXTVLCOPT:http-origin={BASE_URL}")
        lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        lines.append(stream_url)
        lines.append("")
        count += 1

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info(f"Generated {output_file} with {count} events")
    return count


def generate_tivimate_playlist(urls: dict, output_file="ovo_tivimate.m3u8"):
    encoded_ua = urllib.parse.quote(USER_AGENT, safe="")
    lines = ["#EXTM3U"]
    count = 0

    for key, entry in sorted(urls.items()):
        stream_url = entry.get("url")
        if not stream_url:
            continue

        lines.append(_format_extinf(key, entry))

        lines.append(
            f"{stream_url}|referer={BASE_URL}/&origin={BASE_URL}&user-agent={encoded_ua}"
        )
        lines.append("")
        count += 1

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info(f"Generated {output_file} with {count} events")
    return count


def generate_all_playlists():
    vlc = generate_vlc_playlist(urls)
    tiv = generate_tivimate_playlist(urls)
    return vlc + tiv


# =========================
# CORE
# =========================

def fix_event(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split())


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        return None

    soup = HTMLParser(event_data.content)

    iframe = soup.css_first('iframe[height="100%"]')
    if not iframe:
        return None

    iframe_src = iframe.attributes.get("src")
    if not iframe_src:
        return None

    iframe_src_data = await network.request(
        iframe_src,
        headers={"Referer": url},
        log=log,
    )
    if not iframe_src_data:
        return None

    pattern = re.compile(r'(https?://[^"\']+\.m3u8[^"\']*)', re.I)

    match = pattern.search(iframe_src_data.text)
    if not match:
        return None

    log.info(f"URL {url_num}) Captured M3U8")
    return match.group(1)


async def scrape():
    cached_urls = CACHE_FILE.load() or {}

    log.info(f"Loaded {len(cached_urls)} cached events")

    now = Time.clean(Time.now())

    sport_urls = {
        sport: urljoin(BASE_URL, f"sport/{sport}")
        for sport in SPORT_ENDPOINTS
    }

    tasks = []
    for sport, url in sport_urls.items():
        tasks.append(network.request(url, log=log))

    pages = await asyncio.gather(*tasks)

    events = []

    for sport, page in zip(SPORT_ENDPOINTS, pages):
        if not page:
            continue

        soup = HTMLParser(page.content)

        for card in soup.css("#events .table .vevent.theevent"):
            href = card.css_first("a").attributes.get("href")
            if not href:
                continue

            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)

            name = card.css_first(".teamtd.event").text(strip=True)
            name = fix_event(name.replace("@", "vs"))

            sport_name = SPORT_ENDPOINTS[sport]

            events.append((sport_name, name, href))

    log.info(f"Processing {len(events)} events")

    for i, (sport, name, link) in enumerate(events, 1):
        url = await process_event(link, i)

        if not url:
            continue  # skip invalid

        key = f"[{sport}] {name} ({TAG})"

        tvg_id, logo = leagues.get_tvg_info(sport, name)

        entry = {
            "url": url,
            "logo": logo,
            "id": tvg_id or "Live.Event",
            "sport": sport,
        }

        urls[key] = entry
        cached_urls[key] = entry

    CACHE_FILE.write(cached_urls)

    total = generate_all_playlists()

    log.info(f"Final playlist size: {len(urls)} events")
    log.info(f"Total written: {total}")


async def main():
    await scrape()


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
