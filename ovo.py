#!/usr/bin/env python3
"""
OVO Scraper - Extracts M3U8 streams from volokit.xyz
"""

import asyncio
import re
import base64
import urllib.parse
import json
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

def extract_m3u8_from_string(text: str, base_url: str = None) -> str:
    """Extract M3U8 URL from text using multiple patterns"""
    
    # Clean up the text
    text = text.replace('\\', '')
    
    # Pattern 1: Direct full M3U8 URLs with port and parameters
    patterns = [
        # Full URL with port and parameters
        r'(https?://[^\s"\']+:\d+/[^\s"\']+\.m3u8[^\s"\']*)',
        # Full URL without port
        r'(https?://[^\s"\']+\.m3u8[^\s"\']*)',
        # Full URL with stream path
        r'(https?://[^\s"\']+/hls/[^\s"\']+\.m3u8[^\s"\']*)',
        # Full URL with playlist path
        r'(https?://[^\s"\']+/playlist[^\s"\']*\.m3u8[^\s"\']*)',
        # URL with md5 and expires parameters
        r'(https?://[^\s"\']+\.m3u8\?md5=[^&\s"\']+&expires=\d+[^\s"\']*)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # Filter out wikisport URLs (they are just redirects)
            if 'wikisport.club' not in match and 'stream.m3u8?ch=spn' not in match:
                log.debug(f"[M3U8] Found: {match}")
                return match
    
    # Pattern 2: Look for JavaScript variables containing M3U8
    js_patterns = [
        r'(?:var|const|let)\s+(?:url|src|source|stream|file|video|hls|m3u8)\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'(?:source|file|src|video)\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'playlist\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'loadSource\(["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'src\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
    ]
    
    for pattern in js_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            if 'wikisport.club' not in match and 'stream.m3u8?ch=spn' not in match:
                # Make absolute URL if relative
                if not match.startswith('http') and base_url:
                    match = urljoin(base_url, match)
                log.debug(f"[M3U8] JS var: {match}")
                return match
    
    return None


async def fetch_with_redirects(url: str, headers: dict = None, max_redirects: int = 5) -> tuple:
    """Fetch URL following redirects and return final URL and content"""
    current_url = url
    redirect_count = 0
    
    while redirect_count < max_redirects:
        if not headers:
            headers = {"User-Agent": USER_AGENT}
        
        response = await network.request(current_url, headers=headers, log=log)
        if not response:
            return None, None
        
        # Check if it's a redirect (3xx status)
        if hasattr(response, 'status') and 300 <= response.status < 400:
            location = response.headers.get('Location', response.headers.get('location'))
            if location:
                current_url = urljoin(current_url, location)
                redirect_count += 1
                log.debug(f"[REDIRECT] {current_url}")
                continue
        
        # Return final URL and content
        return current_url, response.text
    
    return current_url, response.text if response else None


async def process_event(url: str, url_num: int):
    """Process individual event page to extract M3U8 URL"""
    log.debug(f"Processing event {url_num}: {url}")
    
    # Step 1: Fetch the event page
    event_response = await network.request(url, log=log)
    if not event_response:
        log.warning(f"URL {url_num}) Failed to load event page")
        return None
    
    soup = HTMLParser(event_response.content)
    
    # Step 2: Find all iframes and script tags
    iframes = soup.css('iframe')
    scripts = soup.css('script')
    
    # Step 3: Look for iframe with embed or stream
    iframe_url = None
    for iframe in iframes:
        src = iframe.attributes.get("src", "")
        if src and ('embed' in src or 'stream' in src or 'player' in src):
            if not src.startswith('http'):
                src = urljoin(url, src)
            iframe_url = src
            break
    
    # Step 4: If no iframe, look in scripts for dynamically created iframe
    if not iframe_url:
        for script in scripts:
            script_text = script.text()
            if script_text and ('iframe' in script_text or 'embed' in script_text):
                # Look for src attribute
                src_match = re.search(r'src\s*[:=]\s*["\']([^"\']+)["\']', script_text)
                if src_match:
                    src = src_match.group(1)
                    if not src.startswith('http'):
                        src = urljoin(url, src)
                    iframe_url = src
                    break
    
    if not iframe_url:
        log.warning(f"URL {url_num}) No iframe found")
        return None
    
    log.debug(f"URL {url_num}) Iframe URL: {iframe_url}")
    
    # Step 5: Fetch iframe content with proper referer
    iframe_response = await network.request(iframe_url, headers={"Referer": url}, log=log)
    if not iframe_response:
        log.warning(f"URL {url_num}) Failed to load iframe")
        return None
    
    iframe_content = iframe_response.text
    
    # Step 6: Look for direct M3U8 in iframe content
    m3u8_url = extract_m3u8_from_string(iframe_content, iframe_url)
    if m3u8_url:
        log.info(f"URL {url_num}) Captured M3U8")
        return m3u8_url
    
    # Step 7: Look for fetch.php or similar endpoints
    fetch_patterns = [
        r'(https?://[^"\']+fetch\.php[^"\']+)',
        r'(https?://[^"\']+source\.php[^"\']+)',
        r'(https?://[^"\']+stream\.php[^"\']+)',
        r'fetch\.php\?([^"\']+)',
    ]
    
    for pattern in fetch_patterns:
        matches = re.findall(pattern, iframe_content, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                fetch_url = match[0] if match[0].startswith('http') else f"{iframe_url.split('/source')[0]}/source/fetch.php?{match[0]}"
            else:
                if match.startswith('http'):
                    fetch_url = match
                else:
                    # Construct fetch URL
                    parsed = urllib.parse.urlparse(iframe_url)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    fetch_url = f"{base}/source/fetch.php?{match}"
            
            log.debug(f"URL {url_num}) Trying fetch: {fetch_url}")
            fetch_response = await network.request(fetch_url, headers={"Referer": iframe_url}, log=log)
            if fetch_response:
                # Check for M3U8 in fetch response
                m3u8 = extract_m3u8_from_string(fetch_response.text, fetch_url)
                if m3u8:
                    log.info(f"URL {url_num}) Captured M3U8 from fetch")
                    return m3u8
    
    # Step 8: Look for JavaScript that might contain the M3U8
    for script in scripts:
        script_text = script.text()
        if script_text:
            # Look for variables that might contain URLs
            var_patterns = [
                r'(?:var|const|let)\s+(\w+)\s*=\s*["\']([^"\']+)["\']',
                r'(\w+)\s*=\s*["\']([^"\']+)["\']',
            ]
            
            variables = {}
            for pattern in var_patterns:
                matches = re.findall(pattern, script_text)
                for var_name, var_value in matches:
                    variables[var_name] = var_value
            
            # Check if any variable contains .m3u8
            for var_name, var_value in variables.items():
                if '.m3u8' in var_value and 'wikisport.club' not in var_value:
                    if not var_value.startswith('http'):
                        var_value = urljoin(iframe_url, var_value)
                    log.info(f"URL {url_num}) Captured M3U8 from script variable")
                    return var_value
    
    # Step 9: Try to find any URL in the iframe content that looks like a stream
    all_urls = re.findall(r'(https?://[^\s"\']+)', iframe_content)
    for url_candidate in all_urls:
        if ('.m3u8' in url_candidate and 
            'wikisport.club' not in url_candidate and 
            'stream.m3u8?ch=spn' not in url_candidate):
            log.info(f"URL {url_num}) Captured M3U8 from URL list")
            return url_candidate
    
    log.warning(f"URL {url_num}) No M3U8 source found")
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

        # Look for event rows
        event_rows = soup.css("#events .vevent.theevent")
        
        if not event_rows:
            # Try alternative selectors
            event_rows = soup.css(".vevent")
        
        for row in event_rows:
            # Find the watch link (btn-gray)
            watch_link = row.css_first('a.btn-gray')
            if not watch_link:
                # Try any link with /lives/
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


def fix_event(s: str) -> str:
    """Fix event name formatting"""
    # Remove extra spaces
    s = " ".join(s.split())
    # Capitalize each word
    return " ".join(x.capitalize() for x in s.split())


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
