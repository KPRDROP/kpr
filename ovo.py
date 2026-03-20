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
def decode_obfuscated_string(encoded_str: str) -> str:
    """Decode obfuscated strings found in volokit embeds"""
    try:
        # Try base64 decode
        padding = 4 - (len(encoded_str) % 4)
        if padding != 4:
            encoded_str += '=' * padding
        decoded = base64.b64decode(encoded_str).decode('utf-8')
        return decoded
    except:
        pass
    
    # Try to reverse string (common obfuscation)
    try:
        if encoded_str.endswith('z') and len(encoded_str) > 10:
            # Reverse the string
            reversed_str = encoded_str[::-1]
            # Check if it looks like a URL
            if 'http' in reversed_str or '.m3u8' in reversed_str:
                return reversed_str
    except:
        pass
    
    return None


def extract_m3u8_from_obfuscated_js(content: str, logger) -> str:
    """Extract M3U8 from obfuscated JavaScript"""
    
    # Look for pattern where variables are defined with obfuscated strings
    var_patterns = [
        r'(?:var|const|let)\s+(\w+)\s*=\s*["\']([^"\']+)["\']',
        r'(\w+)\s*=\s*["\']([^"\']+)["\']',
    ]
    
    variables = {}
    for pattern in var_patterns:
        matches = re.findall(pattern, content)
        for var_name, var_value in matches:
            variables[var_name] = var_value
    
    # Look for concatenation patterns
    concat_pattern = r'(\w+)\s*\+\s*(\w+)'
    matches = re.findall(concat_pattern, content)
    
    for var1, var2 in matches:
        if var1 in variables and var2 in variables:
            combined = variables[var1] + variables[var2]
            if '.m3u8' in combined:
                logger.debug(f"[M3U8] Concatenated: {combined}")
                return combined
    
    # Look for split/join patterns
    split_pattern = r'split\(["\']([^"\']+)["\']\)\.join\(["\']([^"\']*)["\']\)'
    matches = re.findall(split_pattern, content)
    for separator, joiner in matches:
        # Find array definition
        array_pattern = r'=\s*\[([^\]]+)\]'
        array_matches = re.findall(array_pattern, content)
        for array_content in array_matches:
            parts = [p.strip().strip('"\'') for p in array_content.split(',')]
            if len(parts) > 1:
                joined = joiner.join(parts)
                if '.m3u8' in joined:
                    logger.debug(f"[M3U8] Split/Join: {joined}")
                    return joined
    
    # Look for string manipulation
    manip_patterns = [
        r'(\w+)\s*=\s*(\w+)\.split\(["\']([^"\']+)["\']\)\.reverse\(\)\.join\(["\']([^"\']*)["\']\)',
        r'(\w+)\s*=\s*(\w+)\.split\(["\']([^"\']+)["\']\)\.reduce\([^\)]+\)',
    ]
    
    for pattern in manip_patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            logger.debug(f"[M3U8] Found manipulation pattern: {match}")
    
    return None


def extract_m3u8(content: str, embed_url: str, get_page, logger):
    """Extract M3U8 URL from embed page content"""
    if not content:
        return None

    logger.debug(f"[DEBUG] Analyzing embed content length: {len(content)}")
    
    # -------------------------
    # 1. Look for direct M3U8 URLs (including relative paths)
    # -------------------------
    m3u8_patterns = [
        r'(https?://[^\s"\']+\.m3u8[^\s"\']*)',
        r'(https?://[^\s"\']+\.m3u8(?:\?[^\s"\']*)?)',
        r'(https?://[^\s"\']+stream[^\s"\']*\.m3u8[^\s"\']*)',
        r'(https?://[^\s"\']+playlist[^\s"\']*\.m3u8[^\s"\']*)',
        r'([/\.][^\s"\']*\.m3u8[^\s"\']*)',  # Relative paths
        r'(/hls/[^\s"\']+\.m3u8[^\s"\']*)',  # Common hls path
    ]

    for pattern in m3u8_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for m in matches:
            if '.m3u8' in m:
                # Make absolute URL if relative
                if not m.startswith('http'):
                    parsed = urllib.parse.urlparse(embed_url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}"
                    m = urllib.parse.urljoin(base_url, m)
                logger.debug(f"[M3U8] Direct: {m}")
                return m

    # -------------------------
    # 2. Look for fetch.php and similar endpoints
    # -------------------------
    fetch_patterns = [
        r'fetch\.php\?([^"\']+)',
        r'source\.php\?([^"\']+)',
        r'stream\.php\?([^"\']+)',
    ]
    
    for fetch_pattern in fetch_patterns:
        matches = re.findall(fetch_pattern, content)
        for match in matches:
            if 'hd=' in match or 'id=' in match or 'ch=' in match:
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
                                if not sm.startswith('http'):
                                    sm = urllib.parse.urljoin(base_url, sm)
                                logger.debug(f"[M3U8] Fetch: {sm}")
                                return sm

    # -------------------------
    # 3. Extract from obfuscated JavaScript
    # -------------------------
    js_blocks = re.findall(r'<script[^>]*>([\s\S]*?)</script>', content, re.IGNORECASE)
    for js_block in js_blocks:
        # Look for .m3u8 in the JS
        if '.m3u8' in js_block:
            # Try to find URL patterns
            url_patterns = [
                r'["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'https?://[^\s"\']+\.m3u8[^\s"\']*',
            ]
            for pattern in url_patterns:
                matches = re.findall(pattern, js_block, re.IGNORECASE)
                for match in matches:
                    if '.m3u8' in match:
                        if not match.startswith('http'):
                            parsed = urllib.parse.urlparse(embed_url)
                            base_url = f"{parsed.scheme}://{parsed.netloc}"
                            match = urllib.parse.urljoin(base_url, match)
                        logger.debug(f"[M3U8] From script: {match}")
                        return match
        
        # Try to decode obfuscated strings
        obfuscated_match = extract_m3u8_from_obfuscated_js(js_block, logger)
        if obfuscated_match and '.m3u8' in obfuscated_match:
            if not obfuscated_match.startswith('http'):
                parsed = urllib.parse.urlparse(embed_url)
                base_url = f"{parsed.scheme}://{parsed.netloc}"
                obfuscated_match = urllib.parse.urljoin(base_url, obfuscated_match)
            logger.debug(f"[M3U8] From obfuscated JS: {obfuscated_match}")
            return obfuscated_match

    # -------------------------
    # 4. Look for HLS.js initialization
    # -------------------------
    hls_patterns = [
        r'loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'videojs\([^)]*\)\.src\(["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'player\.load\(["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'plyr\.setup\([^)]*source[^)]*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'new\s+Hls\([^)]*\)[^;]*loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
    ]

    for pattern in hls_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for m in matches:
            if '.m3u8' in m:
                if not m.startswith('http'):
                    parsed = urllib.parse.urlparse(embed_url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}"
                    m = urllib.parse.urljoin(base_url, m)
                logger.debug(f"[M3U8] HLS: {m}")
                return m

    # -------------------------
    # 5. Look for base64 encoded data
    # -------------------------
    b64_patterns = [
        r'atob\(["\']([^"\']+)["\']\)',
        r'Base64\.decode\(["\']([^"\']+)["\']\)',
        r'decodeURIComponent\(["\']([^"\']+)["\']\)',
        r'btoa\(["\']([^"\']+)["\']\)',
    ]

    for pattern in b64_patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            decoded = decode_obfuscated_string(match)
            if decoded and '.m3u8' in decoded:
                logger.debug(f"[M3U8] Base64 decoded: {decoded}")
                if not decoded.startswith('http'):
                    parsed = urllib.parse.urlparse(embed_url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}"
                    decoded = urllib.parse.urljoin(base_url, decoded)
                return decoded

    # -------------------------
    # 6. Look for any URL that might be a stream
    # -------------------------
    generic_patterns = [
        r'(https?://[^\s"\']+stream[^\s"\']+)',
        r'(https?://[^\s"\']+play[^\s"\']+)',
        r'(https?://[^\s"\']+watch[^\s"\']+)',
        r'(https?://[^\s"\']+video[^\s"\']+)',
    ]
    
    for pattern in generic_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for match in matches:
            if '.m3u8' in match or 'stream' in match:
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

    # Look for the main iframe (usually has height="100%")
    iframe = soup.css_first('iframe[height="100%"]')
    if not iframe:
        # Try to find any iframe that contains embed or stream
        iframes = soup.css('iframe')
        for ifr in iframes:
            src = ifr.attributes.get("src", "")
            if "embed" in src or "stream" in src or "player" in src:
                iframe = ifr
                break
    
    if not iframe:
        # Look for script that creates iframe
        scripts = soup.css('script')
        for script in scripts:
            script_text = script.text()
            if script_text and 'iframe' in script_text and 'src' in script_text:
                src_match = re.search(r'src\s*[:=]\s*["\']([^"\']+)["\']', script_text)
                if src_match:
                    src = src_match.group(1)
                    if not src.startswith('http'):
                        src = urljoin(url, src)
                    iframe = type('obj', (object,), {'attributes': {'src': src}})()
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

    # Fetch the iframe content
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

        # Look for event tables
        event_rows = soup.css("#events .vevent.theevent")
        
        if not event_rows:
            # Try alternative selector
            event_rows = soup.css(".vevent")
        
        if not event_rows:
            # Try to find any links with btn-gray class
            watch_links = soup.css('a.btn-gray[href*="/lives/"]')
            for link in watch_links:
                href = link.attributes.get("href")
                if href:
                    if not href.startswith("http"):
                        href = urljoin(BASE_URL, href)
                    
                    # Try to get event name from nearby element
                    parent = link.parent
                    event_elem = parent.css_first(".event, .teamtd")
                    if event_elem:
                        name = event_elem.text(strip=True)
                    else:
                        # Extract from URL
                        name = href.split('/lives/')[-1].replace('/', ' ').replace('-', ' ').strip()
                        name = re.sub(r'-(main|alt)$', '', name, flags=re.IGNORECASE)
                    
                    name = fix_event(name.replace("@", "vs"))
                    
                    events.append({
                        "sport": SPORT_ENDPOINTS[sport],
                        "event": name,
                        "link": href,
                    })
            continue

        for row in event_rows:
            # Find the watch link
            watch_link = row.css_first('a.btn-gray')
            if not watch_link:
                # Try to find any link in the row
                watch_link = row.css_first('a[href*="/lives/"]')
            
            if not watch_link:
                continue
            
            href = watch_link.attributes.get("href")
            if not href:
                continue

            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)

            # Get event name
            name_elem = row.css_first(".teamtd.event, .event")
            if name_elem:
                name = name_elem.text(strip=True)
            else:
                # Extract from URL
                name = href.split('/lives/')[-1].replace('/', ' ').replace('-', ' ').strip()
                name = re.sub(r'-(main|alt)$', '', name, flags=re.IGNORECASE)
            
            name = fix_event(name.replace("@", "vs"))

            events.append({
                "sport": SPORT_ENDPOINTS[sport],
                "event": name,
                "link": href,
            })

    # Remove duplicates based on link
    seen = set()
    unique_events = []
    for event in events:
        if event["link"] not in seen:
            seen.add(event["link"])
            unique_events.append(event)
    
    log.info(f"Found {len(unique_events)} events")
    return unique_events


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
