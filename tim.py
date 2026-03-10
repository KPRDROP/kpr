import asyncio
from functools import partial
from urllib.parse import urljoin, quote
import os
import re

from playwright.async_api import Browser, Page, Response
from selectolax.parser import HTMLParser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIMSTRMS"

CACHE_FILE = Cache(TAG, exp=10_800)

# Base URL from environment variable
BASE_URL = os.environ.get("TIM_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing TIM_BASE_URL secret")

# Output files
VLC_OUTPUT = "tim_vlc.m3u8"
TIVIMATE_OUTPUT = "tim_tivimate.m3u8"

# User agent for Tivimate (encoded)
TIVIMATE_UA = quote("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

# Sport mapping based on common keywords
SPORT_KEYWORDS = {
    "Soccer": ["soccer", "football", "fútbol", "calcio", "bundesliga", "premier league", "la liga", "serie a", "champions league", "europa league", "galatasaray", "liverpool", "bayern", "atalanta", "newcastle", "barcelona", "atletico madrid", "tottenham", "real madrid", "manchester", "chelsea", "arsenal", "psg", "juventus", "milan", "inter"],
    "Basketball": ["basketball", "nba", "euroleague", "ncaa", "lakers", "celtics", "bulls", "warriors", "knicks", "heat", "mavericks", "sixers", "cavaliers"],
    "Hockey": ["hockey", "nhl", "khl", "kings", "blue jackets", "maple leafs", "canadiens", "bruins", "rangers", "flyers", "penguins", "blackhawks"],
    "Tennis": ["tennis", "atp", "wta", "grand slam", "us open", "wimbledon", "roland garros", "australian open", "djokovic", "nadal", "federer", "alcaraz"],
    "Baseball": ["baseball", "mlb", "yankees", "red sox", "dodgers", "cubs", "astros", "braves"],
    "American Football": ["nfl", "football", "super bowl", "ncaa football", "chiefs", "eagles", "49ers", "cowboys", "patriots", "packers", "steelers", "ravens"],
    "MMA": ["mma", "ufc", "bellator", "fame mma", "rizin", "dana white", "title fight", "octagon"],
    "Boxing": ["boxing", "boxe", "fight", "heavyweight", "lightweight", "welterweight", "canelo", "wildet", "joshua", "usyk", "fury"],
    "Motorsport": ["f1", "formula", "motogp", "nascar", "racing", "speedway", "millbridge", "grand prix", "verstappen", "hamilton", "leclerc"],
    "Rugby": ["rugby", "super rugby", "six nations", "premiership", "rugby world cup", "all blacks", "springboks"],
    "Cricket": ["cricket", "ipl", "ashes", "big bash", "world cup", "india", "australia", "england", "test match"],
    "Darts": ["darts", "pdc", "premier league darts", "world championship", "van gerwen", "price", "smith", "wade"],
    "Wrestling": ["wrestling", "aew", "wwe", "revolution", "raw", "smackdown", "dynamite", "collision", "royal rumble", "wrestlemania"],
    "Volleyball": ["volleyball", "v league", "fivb", "beach volleyball", "world championship"],
    "Handball": ["handball", "european championship", "world championship", "bundesliga"],
    "Golf": ["golf", "pga", "masters", "open championship", "ryder cup", "woods", "mcilroy"],
    "Cycling": ["cycling", "tour de france", "giro", "vuelta", "world championship"],
}


def detect_sport(event_name: str) -> str:
    """Detect sport from event name using keywords"""
    event_lower = event_name.lower()
    for sport, keywords in SPORT_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in event_lower:
                return sport
    return "Other"


def clean_event_name(raw_name: str) -> str:
    """Clean event name by removing extra text and formatting"""
    # Remove common patterns
    cleaned = re.sub(r'\s*(Soccer|Loading\.\.\.|Watch\s+(Soon|Now|Replay)|Replay\s*\d{2}/\d{2}/\d{4})\s*', ' ', raw_name, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Try to extract just the team names if it's a vs match
    vs_patterns = [
        r'([A-Za-z\s]+?)\s+vs\.?\s+([A-Za-z\s]+(?:[A-Za-z\s]+)?)',
        r'([A-Za-z\s]+?)\s+-\s+([A-Za-z\s]+)',
        r'([A-Za-z\s]+?)\s+@\s+([A-Za-z\s]+)',
    ]
    
    for pattern in vs_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            team1 = match.group(1).strip()
            team2 = match.group(2).strip()
            # Clean up team names
            team1 = re.sub(r'\s+', ' ', team1)
            team2 = re.sub(r'\s+', ' ', team2)
            if len(team1) < 40 and len(team2) < 40:
                return f"{team1} vs {team2}"
    
    return cleaned


def extract_embed_url_from_html(html: str) -> str | None:
    """Extract hmembeds.one embed URL from HTML"""
    patterns = [
        r'<iframe[^>]*src=["\'](https?://hmembeds\.one/embed/[^"\']+)["\'][^>]*>',
        r'<a[^>]*href=["\'](https?://hmembeds\.one/embed/[^"\']+)["\'][^>]*>',
        r'window\.location\s*=\s*["\'](https?://hmembeds\.one/embed/[^"\']+)["\']',
        r'https?://hmembeds\.one/embed/[a-zA-Z0-9_-]+',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            return matches[0]
    return None


async def capture_m3u8_from_page(page: Page, url_num: int) -> tuple[str | None, str | None]:
    """
    Main function to capture m3u8 from a page
    """
    captured = []
    got_one = asyncio.Event()
    embed_url = None
    
    def handle_request(request):
        url = request.url.lower()
        # Look for m3u8 in requests
        if '.m3u8' in url and not any(x in url for x in ['hmembeds.one', 'analytics', 'tracking', 'google', 'facebook']):
            captured.append(request.url)
            got_one.set()
            log.info(f"URL {url_num}) Captured m3u8 request: {request.url}")
    
    def handle_response(response):
        url = response.url.lower()
        # Check for m3u8 in responses
        if '.m3u8' in url and not any(x in url for x in ['hmembeds.one', 'analytics', 'tracking']):
            captured.append(response.url)
            got_one.set()
            log.info(f"URL {url_num}) Captured m3u8 response: {response.url}")
        
        # Check content-type
        try:
            content_type = response.headers.get('content-type', '').lower()
            if 'mpegurl' in content_type or 'application/vnd.apple.mpegurl' in content_type:
                if not any(x in url for x in ['hmembeds.one', 'analytics', 'tracking']):
                    captured.append(response.url)
                    got_one.set()
                    log.info(f"URL {url_num}) Found m3u8 by content-type: {response.url}")
        except:
            pass
        
        # Also capture embed URL if found in response
        nonlocal embed_url
        if 'hmembeds.one/embed' in url and not embed_url:
            embed_url = response.url
            log.info(f"URL {url_num}) Found embed URL in response: {embed_url}")
    
    page.on("request", handle_request)
    page.on("response", handle_response)
    
    try:
        # Wait for any activity that might trigger the stream
        await page.wait_for_timeout(5000)
        
        # Try multiple interaction methods
        interaction_methods = [
            # Method 1: Click common play button positions
            lambda: page.mouse.click(640, 360),
            lambda: page.mouse.click(500, 400),
            lambda: page.mouse.click(800, 450),
            
            # Method 2: Click any button elements
            lambda: page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button, [role="button"], .play, .play-button, .vjs-big-play-button, .jw-icon-play, .mejs-playpause-button');
                    buttons.forEach(b => { try { b.click(); } catch(e) {} });
                }
            """),
            
            # Method 3: Try to play video elements directly
            lambda: page.evaluate("""
                () => {
                    const videos = document.querySelectorAll('video');
                    videos.forEach(v => { try { v.play(); } catch(e) {} });
                }
            """),
            
            # Method 4: Check all iframes and try to click inside them
            lambda: page.evaluate("""
                () => {
                    const frames = document.querySelectorAll('iframe');
                    frames.forEach(frame => {
                        try {
                            const doc = frame.contentDocument || frame.contentWindow.document;
                            const buttons = doc.querySelectorAll('button, .play-button, .vjs-big-play-button');
                            buttons.forEach(b => { try { b.click(); } catch(e) {} });
                        } catch(e) {}
                    });
                }
            """),
        ]
        
        for method in interaction_methods:
            try:
                await method()
                await page.wait_for_timeout(1000)
                if got_one.is_set():
                    break
            except:
                continue
        
        # Wait for m3u8 (up to 25 seconds)
        try:
            await asyncio.wait_for(got_one.wait(), timeout=25)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8")
        
        if captured:
            return captured[0], embed_url
        
        return None, embed_url
        
    except Exception as e:
        log.error(f"URL {url_num}) Error: {e}")
        return None, embed_url
    finally:
        page.remove_listener("request", handle_request)
        page.remove_listener("response", handle_response)


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> tuple[str | None, str | None]:

    nones = None, None

    try:
        log.info(f"URL {url_num}) Navigating to event page: {url}")
        
        # Navigate to event page
        resp = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=15000,
        )

        if not resp or resp.status != 200:
            log.warning(f"URL {url_num}) Status Code: {resp.status if resp else 'None'}")
            return nones

        # Try to find embed URL in iframes
        embed_url = None
        frames = page.frames
        log.info(f"URL {url_num}) Found {len(frames)} frames")
        
        for frame in frames:
            try:
                frame_url = frame.url
                if "hmembeds.one/embed" in frame_url:
                    embed_url = frame_url
                    log.info(f"URL {url_num}) Found embed URL in iframe: {embed_url}")
                    # Switch to that frame
                    page = frame
                    break
            except:
                continue
        
        # If no iframe, check page content
        if not embed_url:
            content = await page.content()
            embed_url = extract_embed_url_from_html(content)
            if embed_url:
                log.info(f"URL {url_num}) Found embed URL in HTML: {embed_url}")
        
        # If we found an embed URL but we're not in that frame, navigate to it
        if embed_url and "hmembeds.one/embed" not in page.url:
            log.info(f"URL {url_num}) Navigating to embed URL: {embed_url}")
            await page.goto(embed_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
        
        # Capture m3u8 from the current page
        m3u8_url, found_embed = await capture_m3u8_from_page(page, url_num)
        
        if m3u8_url:
            return m3u8_url, embed_url or found_embed
        
        return nones

    except Exception as e:
        log.warning(f"URL {url_num}) Error: {e}")
        return nones


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []
    seen_events = set()

    log.info(f"Fetching events from {BASE_URL}")
    
    if not (html_data := await network.request(BASE_URL, log=log)):
        log.error("Failed to fetch HTML data")
        return events

    soup = HTMLParser(html_data.content)
    
    # METHOD 1: Find by common card containers
    log.info("Searching for event cards...")
    
    # Comprehensive list of possible card selectors
    card_selectors = [
        ".grid > div",
        ".cards > div",
        ".grid-cols-1 > div",
        ".space-y-4 > div",
        ".flex.flex-col > div",
        "main > div > div",
        "div[class*='grid'] > div",
        "div[class*='card']",
        "article",
        ".event-card",
        ".match-card",
        ".game-card",
        ".stream-card",
        "[class*='event']",
        "[class*='match']",
        "[class*='game']",
        ".bg-white",  # Common card background
        ".rounded-lg",  # Common card styling
        ".shadow",  # Common card styling
        ".border",  # Common card styling
        "a[href*='/event/']",
        "a[href*='/match/']",
        "a[href*='/game/']",
    ]
    
    cards = []
    for selector in card_selectors:
        found = soup.css(selector)
        if found:
            log.info(f"Found {len(found)} potential cards with selector: {selector}")
            cards.extend(found)
    
    # METHOD 2: If no cards found, look for elements containing "vs" pattern
    if not cards:
        log.info("No cards found with selectors, searching for 'vs' pattern...")
        for element in soup.css("div, span, p, h3, h4, h5, a"):
            text = element.text(strip=True)
            if text and re.search(r'\bvs\.?\b', text, re.IGNORECASE):
                # Found a potential event title
                parent = element.parent
                if parent and parent not in cards:
                    cards.append(parent)
    
    # METHOD 3: Look for the "8 events scheduled" section specifically
    if not cards:
        log.info("Searching for 'events scheduled' section...")
        for element in soup.css("div, p, span"):
            text = element.text(strip=True)
            if text and "events scheduled" in text.lower():
                parent = element.parent
                # Look for sibling elements that might contain events
                siblings = parent.parent.css("div") if parent.parent else []
                for sibling in siblings:
                    if sibling != parent:
                        cards.append(sibling)
    
    # METHOD 4: Last resort - look for any link that might be an event
    if not cards:
        log.info("Looking for event links...")
        for link in soup.css("a[href]"):
            href = link.attributes.get("href", "")
            text = link.text(strip=True)
            if text and len(text) > 10 and re.search(r'\bvs\.?\b', text, re.IGNORECASE):
                cards.append(link)
    
    if not cards:
        log.warning("No cards found with any method")
        return events
    
    log.info(f"Total cards to process: {len(cards)}")
    
    # Process each card
    for card in cards:
        try:
            # Try to extract event name
            event_name = None
            
            # Look for headings first
            for selector in ["h3", "h4", "h5", ".font-bold", ".font-semibold", ".text-lg", ".text-xl", ".title", ".event-title"]:
                if heading := card.css_first(selector):
                    event_name = heading.text(strip=True)
                    if event_name and len(event_name) > 5:
                        break
            
            # If no heading, look for text with "vs"
            if not event_name:
                text_content = card.text(strip=True)
                lines = text_content.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and len(line) > 10 and re.search(r'\bvs\.?\b', line, re.IGNORECASE):
                        # Check if this line contains multiple events
                        if "Watch Soon" in line or "Loading" in line:
                            # Try to split by these markers
                            parts = re.split(r'(?<=[a-z])(?=Soccer|Loading|Watch Soon)', line, flags=re.IGNORECASE)
                            for part in parts:
                                if part and len(part) > 10 and re.search(r'\bvs\.?\b', part, re.IGNORECASE):
                                    event_name = part.strip()
                                    break
                        else:
                            event_name = line
                            break
            
            # If still no name, use the card's text but clean it
            if not event_name:
                event_name = card.text(strip=True)
                # Remove common noise
                event_name = re.sub(r'\s*(Soccer|Loading\.\.\.|Watch\s+(Soon|Now|Replay)|Replay\s*\d{2}/\d{2}/\d{4})\s*', ' ', event_name, flags=re.IGNORECASE)
                event_name = re.sub(r'\s+', ' ', event_name).strip()
            
            if not event_name or len(event_name) < 10:
                continue
            
            # Skip if it's clearly a replay
            if "replay" in event_name.lower() or "watch replay" in event_name.lower():
                continue
            
            # Clean the event name
            cleaned_name = clean_event_name(event_name)
            
            # Check for duplicates
            if cleaned_name in seen_events:
                continue
            seen_events.add(cleaned_name)
            
            # Find link
            link = None
            if anchor := card.css_first("a[href]"):
                href = anchor.attributes.get("href")
            elif card.tag == "a" and card.attributes.get("href"):
                href = card.attributes.get("href")
            else:
                href = None
            
            if href:
                if href.startswith("/"):
                    link = urljoin(BASE_URL, href)
                elif href.startswith("http"):
                    link = href
            
            if not link:
                continue
            
            # Check if already cached
            sport = detect_sport(cleaned_name)
            key = f"[{sport}] {cleaned_name} ({TAG})"
            if key in cached_keys:
                continue
            
            # Find logo
            logo = None
            if img := card.css_first("img"):
                src = img.attributes.get("src") or img.attributes.get("data-src")
                if src:
                    if src.startswith("http"):
                        logo = src
                    else:
                        logo = urljoin(BASE_URL, src)
            
            events.append({
                "sport": sport,
                "event": cleaned_name,
                "link": link,
                "logo": logo,
            })
            
            log.info(f"Found event: {sport} - {cleaned_name}")

        except Exception as e:
            log.debug(f"Error processing card: {e}")
            continue

    log.info(f"Total unique events found: {len(events)}")
    return events


# ---------------------------------------------------------
# OUTPUT FILE GENERATORS
# ---------------------------------------------------------

def generate_vlc_output(entries: list[tuple], channel_number: int = 1) -> str:
    """Generate VLC format M3U8 with #EXTVLCOPT headers"""
    output = ["#EXTM3U"]
    
    for key, entry in entries:
        if not entry.get("url"):
            continue
            
        display_name = key
        tvg_id = entry.get("id", "Live.Event.us")
        logo = entry.get("logo", "")
        base_url = entry.get("base", "")
        
        output.append(f'#EXTINF:-1 tvg-chno="{channel_number}" tvg-id="{tvg_id}" tvg-name="{display_name}" tvg-logo="{logo}" group-title="Live Events",{display_name}')
        output.append(f'#EXTVLCOPT:http-referrer={base_url}')
        output.append(f'#EXTVLCOPT:http-origin={base_url}')
        output.append(f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0')
        output.append(entry["url"])
        output.append("")
        
        channel_number += 1
    
    return "\n".join(output)


def generate_tivimate_output(entries: list[tuple], channel_number: int = 1) -> str:
    """Generate Tivimate format M3U8 with pipe-separated headers"""
    output = ["#EXTM3U"]
    
    for key, entry in entries:
        if not entry.get("url"):
            continue
            
        display_name = key
        tvg_id = entry.get("id", "Live.Event.us")
        logo = entry.get("logo", "")
        base_url = entry.get("base", "")
        
        output.append(f'#EXTINF:-1 tvg-chno="{channel_number}" tvg-id="{tvg_id}" tvg-name="{display_name}" tvg-logo="{logo}" group-title="Live Events",{display_name}')
        tivimate_url = f'{entry["url"]}|referer={base_url}|origin={base_url}|user-agent={TIVIMATE_UA}'
        output.append(tivimate_url)
        output.append("")
        
        channel_number += 1
    
    return "\n".join(output)


def write_output_files():
    """Write both VLC and Tivimate output files"""
    if not urls:
        log.warning("No URLs to write to output files")
        return
    
    valid_entries = [(k, v) for k, v in urls.items() if v.get("url")]
    
    if not valid_entries:
        log.warning("No valid streams found to write to output files")
        return
    
    sorted_entries = sorted(valid_entries, key=lambda x: x[0])
    
    vlc_content = generate_vlc_output(sorted_entries)
    with open(VLC_OUTPUT, "w", encoding="utf-8") as f:
        f.write(vlc_content)
    log.info(f"Written {len(sorted_entries)} streams to {VLC_OUTPUT}")
    
    tivimate_content = generate_tivimate_output(sorted_entries)
    with open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(tivimate_content)
    log.info(f"Written {len(sorted_entries)} streams to {TIVIMATE_OUTPUT}")


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v.get("url")}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        async with network.event_context(browser, stealth=True) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    log.info(f"URL {i}) Processing: {ev['event']}")
                    
                    handler = partial(
                        process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                    )

                    url, iframe = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        timeout=60,
                        log=log,
                    )

                    sport, event, logo = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": iframe,
                        "timestamp": now.timestamp(),
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1
                        urls[key] = entry
                        log.info(f"URL {i}) Stream captured successfully: {url}")
                    else:
                        log.warning(f"URL {i}) No stream found")

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.warning("No new events found - check if website structure has changed")
        
        if urls:
            log.info(f"Writing {len(urls)} cached streams to output files")

    CACHE_FILE.write(cached_urls)
    write_output_files()


async def main():
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        
        await scrape(browser)
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
