import asyncio
import re
import base64
import urllib.parse
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "VOLOKIT"

CACHE_FILE = Cache(TAG, exp=10_800)

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
# PLAYLIST
# =========================

def _format_extinf(key: str, entry: dict) -> str:
    return (
        f'#EXTINF:-1 tvg-id="{entry.get("id","")}" '
        f'tvg-logo="{entry.get("logo","")}" '
        f'group-title="{entry.get("sport","Live")}",{key}'
    )


def generate_vlc_playlist(data: dict, output="ovo_vlc.m3u8"):
    lines = ["#EXTM3U"]
    count = 0

    for key, entry in sorted(data.items()):
        url = entry.get("url")
        if not url:
            continue

        lines.append(_format_extinf(key, entry))
        lines.append(f"#EXTVLCOPT:http-referrer={BASE_URL}/")
        lines.append(f"#EXTVLCOPT:http-origin={BASE_URL}")
        lines.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
        lines.append(url)
        lines.append("")
        count += 1

    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info(f"Generated {output} with {count} events")
    return count


def generate_tivimate_playlist(data: dict, output="ovo_tivimate.m3u8"):
    ua = urllib.parse.quote(USER_AGENT, safe="")
    lines = ["#EXTM3U"]
    count = 0

    for key, entry in sorted(data.items()):
        url = entry.get("url")
        if not url:
            continue

        lines.append(_format_extinf(key, entry))
        lines.append(f"{url}|referer={BASE_URL}/&origin={BASE_URL}&user-agent={ua}")
        lines.append("")
        count += 1

    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log.info(f"Generated {output} with {count} events")
    return count

# =========================
# M3U8 EXTRACTOR PATTENRS
# =========================
def extract_m3u8(content: str, embed_url: str, get_page, logger):
    if not content:
        return None

    # -------------------------
    # 1. Direct M3U8 patterns
    # -------------------------
    m3u8_patterns = [
        r'(https?://[^\s"\']+\.m3u8[^\s"\']*)',
        r'(https?://[^\s"\']+stream[^\s"\']*\.m3u8[^\s"\']*)',
        r'(https?://[^\s"\']+playlist[^\s"\']*\.m3u8[^\s"\']*)',
    ]

    for pattern in m3u8_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for m in matches:
            if '.m3u8' in m:
                logger.debug(f"[M3U8] Direct: {m}")
                return m

    # -------------------------
    # 2. JS variable extraction (FIXED)
    # -------------------------
    js_patterns = [
        r'(?:var|const|let)\s+(?:url|src|source|stream|file|video|hls)\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'(?:source|file|src|video)\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'playlist\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
    ]

    for pattern in js_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for m in matches:
            if '.m3u8' in m:
                logger.debug(f"[M3U8] JS: {m}")
                return m

    # -------------------------
    # 3. Base64 decode
    # -------------------------
    b64_patterns = [
        r'atob\(["\']([^"\']+)["\']\)',
        r'Base64\.decode\(["\']([^"\']+)["\']\)',
        r'decodeURIComponent\(["\']([^"\']+)["\']\)',
    ]

    for pattern in b64_patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            try:
                padding = 4 - (len(match) % 4)
                if padding != 4:
                    match += '=' * padding

                decoded = base64.b64decode(match).decode('utf-8')

                if '.m3u8' in decoded:
                    logger.debug(f"[M3U8] Base64: {decoded}")
                    return decoded

            except Exception:
                pass

    # -------------------------
    # 4. fetch.php handler (IMPORTANT FOR YOUR CASE)
    # -------------------------
    fetch_pattern = r'fetch\.php\?([^"\']+)'
    matches = re.findall(fetch_pattern, content)

    for match in matches:
        if 'hd=' in match or 'id=' in match:
            parsed = urllib.parse.urlparse(embed_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"

            new_url = f"{base_url}/source/fetch.php?{match}"
            logger.debug(f"[FETCH] Trying: {new_url}")

            stream_content = get_page(new_url)

            if stream_content:
                for pattern in m3u8_patterns:
                    stream_matches = re.findall(pattern, stream_content, re.IGNORECASE)

                    for sm in stream_matches:
                        if '.m3u8' in sm:
                            logger.debug(f"[M3U8] Fetch: {sm}")
                            return sm

    # -------------------------
    # 5. HLS.js detection
    # -------------------------
    hls_patterns = [
        r'loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'videojs\([^)]*\)\.src\(["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'player\.load\(["\']([^"\']+\.m3u8[^"\']*)["\']',
    ]

    for pattern in hls_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for m in matches:
            if '.m3u8' in m:
                logger.debug(f"[M3U8] HLS: {m}")
                return m

    return None


# =========================
# SCRAPER
# =========================

def fix_event(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split())


async def process_event(url: str, url_num: int):
    if not (res := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return None

    soup = HTMLParser(res.content)

    if not (iframe := soup.css_first('iframe[height="100%"]')):
        log.warning(f"URL {url_num}) No iframe element found.")
        return None

    if not (src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")
        return None

    iframe_data = await network.request(src, headers={"Referer": url}, log=log)
    if not iframe_data:
        log.warning(f"URL {url_num}) Failed iframe load.")
        return None

    # WORKING REGEX
    m3u8_url = extract_m3u8(
    iframe_data.text,
    embed_url,
    self.get_page,
    logger
)

if m3u8_url:
    return m3u8_url

    if not match:
        log.warning(f"URL {url_num}) No Clappr source found.")
        return None

    log.info(f"URL {url_num}) Captured M3U8")
    return match[1]


async def get_events():
    sport_urls = {
        sport: urljoin(BASE_URL, f"sport/{sport}")
        for sport in SPORT_ENDPOINTS
    }

    tasks = [network.request(url, log=log) for url in sport_urls.values()]
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

            events.append({
                "sport": SPORT_ENDPOINTS[sport],
                "event": name,
                "link": href,
            })

    log.info(f"Processing {len(events)} events")
    return events


# =========================
# MAIN
# =========================

async def scrape():
    cached_urls = CACHE_FILE.load() or {}

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}
    urls.update(valid_urls)

    log.info(f"Loaded {len(valid_urls)} cached events")

    events = await get_events()

    for i, ev in enumerate(events, 1):
        url = await network.safe_process(
            partial(process_event, ev["link"], i),
            url_num=i,
            semaphore=network.HTTP_S,
            log=log,
        )

        if not url:
            continue

        key = f"[{ev['sport']}] {ev['event']} ({TAG})"

        tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])

        entry = {
            "url": url,
            "logo": logo,
            "id": tvg_id or "Live.Event",
            "sport": ev["sport"],
        }

        cached_urls[key] = entry
        urls[key] = entry  # CRITICAL

    CACHE_FILE.write(cached_urls)

    clean = {k: v for k, v in urls.items() if v.get("url")}

    vlc = generate_vlc_playlist(clean)
    tiv = generate_tivimate_playlist(clean)

    log.info(f"Final playlist size: {len(clean)} events")
    log.info(f"Total written: {vlc + tiv}")


async def main():
    log.info("Starting OVO scraper")
    await scrape()
    log.info("OVO scraper completed")


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
