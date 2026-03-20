#!/usr/bin/env python3
"""
OVO Scraper - Extracts M3U8 streams from volokit.xyz
"""

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
# M3U8 EXTRACTOR PATTERNS
# =========================
def extract_m3u8(content: str, embed_url: str, get_page, logger):
    """Extract M3U8 URL from embed page content"""
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
    # 2. JS variable extraction
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
    # 4. fetch.php handler
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

    # -------------------------
    # 6. Look for any URL with .m3u8
    # -------------------------
    url_pattern = r'(https?://[^\s"\']+\.m3u8[^\s"\']*)'
    matches = re.findall(url_pattern, content, re.IGNORECASE)
    for match in matches:
        if '.m3u8' in match:
            logger.debug(f"[M3U8] Generic URL: {match}")
            return match

    return None


# =========================
# SCRAPER
# =========================

def fix_event(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split())


async def process_event(url: str, url_num: int):
    """Process individual event page to extract M3U8 URL"""
    if not (res := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return None

    soup = HTMLParser(res.content)

    # Look for iframe with height 100% or any iframe containing embed
    iframe = soup.css_first('iframe[height="100%"]')
    if not iframe:
        # Try other iframe selectors
        iframe = soup.css_first('iframe[src*="embed"]')
    
    if not iframe:
        # Try to find any iframe
        iframes = soup.css('iframe')
        for ifr in iframes:
            src = ifr.attributes.get("src", "")
            if "embed" in src or "stream" in src or "player" in src:
                iframe = ifr
                break
    
    if not iframe:
        log.warning(f"URL {url_num}) No iframe element found.")
        return None

    if not (src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")
        return None

    # Make absolute URL if needed
    if not src.startswith("http"):
        src = urljoin(url, src)

    log.debug(f"URL {url_num}) Iframe src: {src}")

    iframe_data = await network.request(src, headers={"Referer": url}, log=log)
    if not iframe_data:
        log.warning(f"URL {url_num}) Failed iframe load.")
        return None

    # Define get_page function for extract_m3u8
    async def get_page(page_url):
        response = await network.request(page_url, headers={"Referer": src}, log=log)
        return response.text if response else None

    # Extract M3U8 URL
    m3u8_url = extract_m3u8(
        iframe_data.text,
        src,
        get_page,
        log
    )

    if m3u8_url:
        log.info(f"URL {url_num}) Captured M3U8")
        return m3u8_url

    log.warning(f"URL {url_num}) No M3U8 source found.")
    return None


async def get_events():
    """Get events from sport pages"""
    sport_urls = {
        sport: urljoin(BASE_URL, f"sport/{sport}")
        for sport in SPORT_ENDPOINTS
    }

    tasks = [network.request(url, log=log) for url in sport_urls.values()]
    pages = await asyncio.gather(*tasks)

    events = []

    for sport, page in zip(SPORT_ENDPOINTS.keys(), pages):
        if not page:
            continue

        soup = HTMLParser(page.content)

        # Look for event cards
        event_cards = soup.css("#events .table .vevent.theevent")
        if not event_cards:
            # Try alternative selectors
            event_cards = soup.css(".volo-schedule-card")
        
        if not event_cards:
            # Try to find any links to lives
            links = soup.css('a[href*="/lives/"]')
            for link in links:
                href = link.attributes.get("href")
                if href:
                    if not href.startswith("http"):
                        href = urljoin(BASE_URL, href)
                    
                    name = link.text(strip=True)
                    if name:
                        name = fix_event(name.replace("@", "vs"))
                    
                    events.append({
                        "sport": SPORT_ENDPOINTS[sport],
                        "event": name or f"Event {len(events) + 1}",
                        "link": href,
                    })
            continue

        for card in event_cards:
            href = None
            name = None
            
            # Try to find link in card
            link_elem = card.css_first("a")
            if link_elem:
                href = link_elem.attributes.get("href")
            
            if not href:
                continue

            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)

            # Try to get event name
            name_elem = card.css_first(".teamtd.event")
            if name_elem:
                name = name_elem.text(strip=True)
            else:
                name_elem = card.css_first(".event")
                if name_elem:
                    name = name_elem.text(strip=True)
            
            if not name:
                # Extract from URL
                name = href.split('/lives/')[-1].replace('/', ' ').replace('-', ' ').strip()
                name = re.sub(r'-(main|alt)$', '', name, flags=re.IGNORECASE)
            
            name = fix_event(name.replace("@", "vs"))

            events.append({
                "sport": SPORT_ENDPOINTS[sport],
                "event": name,
                "link": href,
            })

    log.info(f"Found {len(events)} events")
    return events


# =========================
# MAIN
# =========================

async def scrape():
    """Main scraping function"""
    cached_urls = CACHE_FILE.load() or {}

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}
    urls.update(valid_urls)

    log.info(f"Loaded {len(valid_urls)} cached events")

    events = await get_events()

    if not events:
        log.warning("No events found")
        # Still generate empty playlists
        generate_vlc_playlist({})
        generate_tivimate_playlist({})
        return

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
        urls[key] = entry

    CACHE_FILE.write(cached_urls)

    clean = {k: v for k, v in urls.items() if v.get("url")}

    vlc_count = generate_vlc_playlist(clean)
    tiv_count = generate_tivimate_playlist(clean)

    log.info(f"Final playlist size: {len(clean)} events")
    log.info(f"Total written: {vlc_count + tiv_count}")


async def main():
    """Main entry point"""
    log.info("Starting OVO scraper")
    await scrape()
    log.info("OVO scraper completed")


def run():
    """Run the scraper"""
    asyncio.run(main())


if __name__ == "__main__":
    run()
