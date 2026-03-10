import asyncio
from functools import partial
from urllib.parse import urljoin, quote, urlparse
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

# Sport mapping based on common categories
SPORT_KEYWORDS = {
    "Soccer": ["soccer", "football", "fútbol", "calcio", "bundesliga", "premier league", "la liga", "serie a", "champions league", "europa league", "galatasaray", "liverpool", "bayern", "atalanta", "newcastle", "barcelona", "atletico madrid", "tottenham"],
    "Basketball": ["basketball", "nba", "euroleague", "ncaa", "lakers", "celtics", "bulls", "warriors"],
    "Hockey": ["hockey", "nhl", "khl", "kings", "blue jackets"],
    "Tennis": ["tennis", "atp", "wta", "grand slam", "us open", "wimbledon", "roland garros", "australian open"],
    "Baseball": ["baseball", "mlb", "yankees", "red sox", "dodgers"],
    "American Football": ["nfl", "football", "super bowl", "ncaa football", "chiefs", "eagles", "49ers"],
    "MMA": ["mma", "ufc", "bellator", "fame mma", "rizin"],
    "Boxing": ["boxing", "boxe", "fight", "heavyweight"],
    "Motorsport": ["f1", "formula", "motogp", "nascar", "racing", "speedway", "millbridge"],
    "Rugby": ["rugby", "super rugby", "six nations", "premiership"],
    "Cricket": ["cricket", "ipl", "ashes", "big bash"],
    "Darts": ["darts", "pdc", "premier league darts"],
    "Wrestling": ["wrestling", "aew", "wwe", "revolution"],
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
    """Clean event name by removing extra text and loading indicators"""
    # Remove common loading text and extra whitespace
    cleaned = re.sub(r'(Soccer|Loading|Watch Soon|Watch Replay|Replay\d{2}/\d{2}/\d{4})\s*', ' ', raw_name)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # If name is too long, try to extract just the team names
    if len(cleaned) > 50:
        # Look for pattern "Team vs Team"
        vs_match = re.search(r'([A-Za-z\s]+?)\s+vs\.?\s+([A-Za-z\s]+)', cleaned)
        if vs_match:
            team1 = vs_match.group(1).strip()
            team2 = vs_match.group(2).strip()
            if len(team1) < 30 and len(team2) < 30:
                return f"{team1} vs {team2}"
    
    return cleaned


def sift_xhr(resp: Response) -> bool:
    """Check if response is the embed URL we're looking for"""
    resp_url = resp.url
    return "hmembeds.one/embed" in resp_url and resp.status == 200


async def capture_m3u8_from_embed(page: Page, embed_url: str, url_num: int) -> str | None:
    """
    Navigate to embed URL and capture m3u8 stream
    """
    captured = []
    got_one = asyncio.Event()
    
    def handle_request(request):
        url = request.url.lower()
        # Look for m3u8 in requests
        if '.m3u8' in url and 'hmembeds.one' not in url:
            captured.append(request.url)
            got_one.set()
            log.info(f"URL {url_num}) Captured m3u8 request: {request.url}")
    
    def handle_response(response):
        url = response.url.lower()
        # Check for m3u8 in responses
        if '.m3u8' in url and 'hmembeds.one' not in url:
            captured.append(response.url)
            got_one.set()
            log.info(f"URL {url_num}) Captured m3u8 response: {response.url}")
        
        # Check content-type
        try:
            content_type = response.headers.get('content-type', '').lower()
            if 'mpegurl' in content_type or 'application/vnd.apple.mpegurl' in content_type:
                if 'hmembeds.one' not in url:
                    captured.append(response.url)
                    got_one.set()
                    log.info(f"URL {url_num}) Found m3u8 by content-type: {response.url}")
        except:
            pass
    
    page.on("request", handle_request)
    page.on("response", handle_response)
    
    try:
        # Navigate to embed URL
        log.info(f"URL {url_num}) Loading embed URL: {embed_url}")
        await page.goto(embed_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)
        
        # Try to click any play button in the embed
        click_selectors = [
            "button",
            ".play-button",
            ".vjs-big-play-button",
            ".jw-icon-play",
            ".mejs-playpause-button",
            "[aria-label='Play']",
            ".fp-playbtn",
            "video",
        ]
        
        for selector in click_selectors:
            try:
                element = await page.wait_for_selector(selector, timeout=2000)
                if element:
                    log.info(f"URL {url_num}) Clicking play button: {selector}")
                    await element.click()
                    await page.wait_for_timeout(2000)
                    break
            except:
                continue
        
        # Try clicking at center of player
        try:
            await page.mouse.click(640, 360)
            await page.wait_for_timeout(2000)
        except:
            pass
        
        # Execute JavaScript to trigger play
        try:
            await page.evaluate("""
                () => {
                    // Try to play any video elements
                    const videos = document.querySelectorAll('video');
                    videos.forEach(v => {
                        try { v.play(); } catch(e) {}
                    });
                    
                    // Try to find and click play buttons by any means
                    const buttons = document.querySelectorAll('button, [role="button"], .play, .play-button, .vjs-big-play-button');
                    buttons.forEach(b => {
                        try { b.click(); } catch(e) {}
                    });
                    
                    // Try to trigger player APIs
                    if (typeof jwplayer !== 'undefined') {
                        try { jwplayer().play(); } catch(e) {}
                    }
                    if (typeof videojs !== 'undefined') {
                        try { 
                            const players = videojs.getAllPlayers();
                            players.forEach(p => { try { p.play(); } catch(e) {} });
                        } catch(e) {}
                    }
                }
            """)
            log.info(f"URL {url_num}) Executed JavaScript play attempts")
        except:
            pass
        
        # Wait for m3u8 (up to 20 seconds)
        try:
            await asyncio.wait_for(got_one.wait(), timeout=20)
            if captured:
                return captured[0]
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8 in embed")
            
    except Exception as e:
        log.error(f"URL {url_num}) Error in embed capture: {e}")
    finally:
        page.remove_listener("request", handle_request)
        page.remove_listener("response", handle_response)
    
    return None


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> tuple[str | None, str | None]:

    nones = None, None

    try:
        # Navigate to event page
        resp = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=10000,
        )

        if not resp or resp.status != 200:
            log.warning(f"URL {url_num}) Status Code: {resp.status if resp else 'None'}")
            return nones

        # Wait for embed URL to appear
        await page.wait_for_timeout(3000)
        
        # Look for iframe with embed URL
        embed_url = None
        frames = page.frames
        for frame in frames:
            try:
                frame_url = frame.url
                if "hmembeds.one/embed" in frame_url:
                    embed_url = frame_url
                    log.info(f"URL {url_num}) Found embed URL in iframe: {embed_url}")
                    break
            except:
                continue
        
        # If no iframe, check for redirect or direct embed
        if not embed_url:
            current_url = page.url
            if "hmembeds.one/embed" in current_url:
                embed_url = current_url
                log.info(f"URL {url_num}) Page redirected to embed: {embed_url}")
        
        # If still no embed URL, try to find it in the page HTML
        if not embed_url:
            content = await page.content()
            embed_pattern = r'https?://hmembeds\.one/embed/[^"\']+'
            matches = re.findall(embed_pattern, content)
            if matches:
                embed_url = matches[0]
                log.info(f"URL {url_num}) Found embed URL in HTML: {embed_url}")

        if not embed_url:
            log.warning(f"URL {url_num}) No embed URL found")
            return nones

        # Now capture m3u8 from the embed URL
        m3u8_url = await capture_m3u8_from_embed(page, embed_url, url_num)
        
        if m3u8_url:
            return m3u8_url, embed_url
        
        return nones

    except Exception as e:
        log.warning(f"URL {url_num}) Error: {e}")
        return nones


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []
    seen_events = set()  # Track unique events to avoid duplicates

    log.info(f"Fetching events from {BASE_URL}")
    
    if not (html_data := await network.request(BASE_URL, log=log)):
        log.error("Failed to fetch HTML data")
        return events

    soup = HTMLParser(html_data.content)
    
    # Find the Live & Upcoming Events section
    events_section = None
    for heading in soup.css("h2, h3, h4"):
        if heading.text() and "Live & Upcoming Events" in heading.text():
            events_section = heading.parent
            log.info("Found Live & Upcoming Events section")
            break
    
    if not events_section:
        events_section = soup
    
    # Find all event cards
    cards = events_section.css(".grid > div, .cards > div, div[class*='grid'] > div, .space-y-4 > div")
    
    if not cards:
        log.warning("No cards found with grid selectors")
        return events

    log.info(f"Found {len(cards)} potential event cards")

    for card in cards:
        try:
            # Extract event name - look for headings first
            event_name = None
            
            # Try to find heading
            for selector in ["h3", "h4", "h5", ".font-bold", "p.font-semibold", ".text-lg"]:
                if heading := card.css_first(selector):
                    event_name = heading.text(strip=True)
                    if event_name and len(event_name) > 5:
                        break
            
            # If no heading, look for text with "vs" pattern
            if not event_name:
                text_content = card.text(strip=True)
                lines = text_content.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and (" vs " in line.lower() or " vs. " in line.lower()) and len(line) < 100:
                        event_name = line
                        break
            
            # If still no name, use first non-empty line
            if not event_name:
                text_content = card.text(strip=True)
                lines = text_content.split('\n')
                for line in lines:
                    if line.strip() and len(line.strip()) > 10:
                        event_name = line.strip()
                        break
            
            if not event_name:
                continue

            # Clean the event name
            event_name = clean_event_name(event_name)
            
            # Skip if too short or looks like replay
            if len(event_name) < 10 or "Replay" in event_name:
                continue

            # Check for duplicates
            if event_name in seen_events:
                continue
            seen_events.add(event_name)

            # Find link
            link = None
            if anchor := card.css_first("a[href]"):
                href = anchor.attributes.get("href")
                if href:
                    if href.startswith("/"):
                        link = urljoin(BASE_URL, href)
                    elif href.startswith("http"):
                        link = href
            
            if not link:
                continue

            # Check if already cached
            sport = detect_sport(event_name)
            key = f"[{sport}] {event_name} ({TAG})"
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
                "event": event_name,
                "link": link,
                "logo": logo,
            })
            
            log.info(f"Found event: {sport} - {event_name}")

        except Exception as e:
            log.debug(f"Error processing card: {e}")
            continue

    log.info(f"Total unique events found: {len(events)}")
    return events


# ---------------------------------------------------------
# OUTPUT FILE GENERATORS
# ---------------------------------------------------------

def generate_vlc_output(entries: list[tuple], channel_number: int = 1) -> str:
    """
    Generate VLC format M3U8 with #EXTVLCOPT headers
    """
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
    """
    Generate Tivimate format M3U8 with pipe-separated headers
    """
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
    """
    Write both VLC and Tivimate output files
    """
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

        async with network.event_context(browser, stealth=False) as context:
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
                        timeout=45,
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
