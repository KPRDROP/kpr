# webcast.py
import asyncio
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, quote
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, async_playwright

# === CONFIG ===
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")  # encoded UA for TiviMate
DYNAMIC_WAIT_TIMEOUT = 15000
GAME_TABLE_WAIT_TIMEOUT = 30000
STREAM_PATTERN = re.compile(r"\.m3u8($|\?)", re.IGNORECASE)
OUTPUT_FILE = "SportsWebcast_TiviMate.m3u8"

# Base URLs
NFL_BASE_URL = "https://nflwebcast.com/"
NFL_BASE_URL = "https://nflwebcast.com/sbl/"
NHL_BASE_URL = "https://slapstreams.com/"
MLB_BASE_URL = "https://mlbwebcast.com/"
MLS_BASE_URL = "https://mlswebcast.com/"
NBA_BASE_URL = "https://nbawebcast.top/"

# Channels
NFL_CHANNEL_URLS = [
    "http://nflwebcast.com/nflnetwork/",
    "https://nflwebcast.com/nflredzone/",
    "https://nflwebcast.com/espnusa/",
]
MLB_CHANNEL_URLS = []
NHL_CHANNEL_URLS = []
MLS_CHANNEL_URLS = []

CHANNEL_METADATA = {
    "nflnetwork": {
        "name": "NFL Network",
        "id": "NFL.Network.HD.us2",
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/nfl-network-hz-us.png?raw=true"
    },
    "nflredzone": {
        "name": "NFL RedZone",
        "id": "NFL.RedZone.HD.us2",
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/nfl-red-zone-hz-us.png?raw=true"
    },
    "espnusa": {
        "name": "ESPN",
        "id": "ESPN.HD.us2",
        "logo": "https://github.com/tv-logo/tv-logos/blob/main/countries/united-states/espn-us.png?raw=true"
    },
}

NBA_STREAM_URL_PATTERN = "https://gg.poocloud.in/{stream_key}/tracks-v1a1/mono.ts.m3u8"
NBA_CUSTOM_HEADERS = {
    "origin": "https.embednow.top",
    "referrer": "https.embednow.top/",
    "user_agent": USER_AGENT,
}


def normalize_game_name(original_name: str) -> str:
    cleaned_name = " ".join(original_name.splitlines()).strip()
    if "@" in cleaned_name:
        parts = cleaned_name.split("@")
        if len(parts) == 2:
            team1 = parts[0].strip().title()
            team2 = parts[1].strip().title()
            team2 = re.split(r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\b',
                             team2, 1)[0].strip()
            return f"{team1} @ {team2}"
    return " ".join(cleaned_name.strip().split()).title()


async def verify_stream_url(session: aiohttp.ClientSession, url: str, headers: Optional[Dict[str, str]] = None) -> bool:
    request_headers = headers or {}
    if "User-Agent" not in request_headers:
        request_headers["User-Agent"] = session.headers.get("User-Agent", USER_AGENT)
    try:
        async with session.get(url, timeout=10, allow_redirects=True, headers=request_headers) as response:
            if response.status == 200:
                print(f" ✔️ URL Verified (200 OK): {url}")
                return True
            else:
                print(f" ❌ URL Failed ({response.status}): {url}")
                return False
    except asyncio.TimeoutError:
        print(f" ❌ URL Timed Out: {url}")
        return False
    except aiohttp.ClientError as e:
        print(f" ❌ URL Client Error ({type(e).__name__}): {url}")
        return False


async def find_stream_from_servers_on_page(p: async_playwright, context: BrowserContext, page_url: str, base_url: str,
                                          session: aiohttp.ClientSession) -> Optional[str]:
    """
    p: the Playwright object from async_playwright() context
    context: Playwright BrowserContext
    """
    verification_headers = {
        "Origin": base_url.rstrip('/'),
        "Referer": base_url
    }
    page = await context.new_page()
    candidate_urls: List[str] = []

    def handle_request(request):
        try:
            url = request.url
            if STREAM_PATTERN.search(url) and url not in candidate_urls:
                print(f" ✅ Captured potential stream: {url}")
                candidate_urls.append(url)
        except Exception:
            pass

    page.on("request", handle_request)
    try:
        print(f" ↳ Navigating to content page: {page_url}")
        await page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        # let page finish network-heavy tasks
        await page.wait_for_load_state('networkidle', timeout=DYNAMIC_WAIT_TIMEOUT)

        # try candidate urls captured during load
        for stream_url in reversed(candidate_urls):
            if await verify_stream_url(session, stream_url, headers=verification_headers):
                print(" ✔️ Found valid stream on initial page load.")
                return stream_url

        # Main page server links (slapstreams style)
        server_links_main = page.locator("#multistmb a")
        count_main = await server_links_main.count()
        if count_main > 0:
            print(f"  found {count_main} server links on main page")
            for i in range(count_main):
                link = server_links_main.nth(i)
                link_text = (await link.inner_text() or "Unknown Link").strip()
                urls_before_click = set(candidate_urls)
                try:
                    await link.click(timeout=5000)
                    await page.wait_for_load_state('networkidle', timeout=DYNAMIC_WAIT_TIMEOUT)
                except Exception as e:
                    print(f"   - Error clicking main page link '{link_text}': {e}")
                    continue

                urls_after_click = set(candidate_urls)
                new_urls = list(urls_after_click - urls_before_click)

                for stream_url in reversed(new_urls):
                    if await verify_stream_url(session, stream_url, headers=verification_headers):
                        print(f" ✔️ Found valid stream after clicking main page link '{link_text}'.")
                        return stream_url

        # Check iframe content for server links
        iframe_locator = page.locator("div#player iframe, div.vplayer iframe, iframe.responsive-iframe").first
        if not await iframe_locator.count():
            print("   - No iframe found.")
            return None

        frame_content = await iframe_locator.content_frame()
        if not frame_content:
            print("   - Could not get frame content.")
            return None

        server_links_iframe = frame_content.locator("#multistmb a")
        count_iframe = await server_links_iframe.count()

        if count_iframe == 0:
            server_links_iframe = frame_content.locator("a:has-text('Server'), a:has-text('HD')")
            count_iframe = await server_links_iframe.count()

        if count_iframe > 0:
            print(f"   Found {count_iframe} server links inside iframe.")
            for i in range(count_iframe):
                link = server_links_iframe.nth(i)
                link_text = (await link.inner_text() or "Unknown Link").strip()
                urls_before_click = set(candidate_urls)
                try:
                    await link.click(timeout=5000)
                    await page.wait_for_load_state('networkidle', timeout=DYNAMIC_WAIT_TIMEOUT)
                except Exception as e:
                    print(f"   - Error clicking iframe link '{link_text}': {e}")
                    continue

                urls_after_click = set(candidate_urls)
                new_urls = list(urls_after_click - urls_before_click)

                for stream_url in reversed(new_urls):
                    if await verify_stream_url(session, stream_url, headers=verification_headers):
                        print(f" ✔️ Found valid stream after clicking iframe link '{link_text}'.")
                        return stream_url
        else:
            print("   - No server links found inside iframe.")

    except Exception as e:
        print(f" ❌ Error processing page {page_url}: {e}")
    finally:
        try:
            page.remove_listener("request", handle_request)
        except Exception:
            pass
        await page.close()

    print(f" ❌ No valid stream found for {page_url}")
    return None


async def scrape_league(p: async_playwright, base_url: str, channel_urls: List[str], group_prefix: str,
                        default_id: str, default_logo: str) -> List[Dict]:
    """Main scraper per league. Receives the Playwright object 'p' to avoid multiple initializations."""
    print(f"\nScraping {group_prefix} streams from {base_url}...")
    found_streams: Dict[str, Tuple[str, str, Optional[str]]] = {}
    results: List[Dict] = []

    # create an aiohttp session per scrape (with proper UA)
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        # launch browser via the shared Playwright instance `p`
        browser = await p.chromium.launch(headless=True,
                                          args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=USER_AGENT)
        try:
            page = await context.new_page()
            await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)

            game_row_selector = "#mtable tr.singele_match_date:not(.mdatetitle), .match-row.clearfix"

            try:
                await page.wait_for_selector(game_row_selector, timeout=GAME_TABLE_WAIT_TIMEOUT)
            except Exception:
                game_row_selector = "h1.gametitle a"
                await page.wait_for_selector(game_row_selector, timeout=GAME_TABLE_WAIT_TIMEOUT)

            event_rows = page.locator(game_row_selector)
            count = await event_rows.count()
            print(f"  Found {count} event rows using selector '{game_row_selector}'.")

            game_links_info = []

            if "mtable" in game_row_selector:
                for i in range(count):
                    row = event_rows.nth(i)
                    link_locator = row.locator("td.teamvs a")
                    name = await link_locator.inner_text()
                    href = await link_locator.get_attribute("href")
                    logo_url = None
                    logo_locators = row.locator("td.teamlogo img")
                    if await logo_locators.count() > 1:
                        logo_url = await logo_locators.nth(1).get_attribute("src")
                    elif await logo_locators.count() > 0:
                        logo_url = await logo_locators.nth(0).get_attribute("src")
                    if name and href:
                        full_url = urljoin(base_url, href)
                        game_links_info.append({"name": name.strip(), "url": full_url, "logo": logo_url or default_logo})

            elif "h1.gametitle" in game_row_selector:
                for i in range(count):
                    link_locator = event_rows.nth(i)
                    href = await link_locator.get_attribute("href")
                    name_parts = await link_locator.locator("span.tm").all_inner_texts()
                    name = " @ ".join([part.strip() for part in name_parts]) if len(name_parts) == 2 else "Unknown Game"
                    logo_url = None
                    logo_locators = link_locator.locator("img.headimg")
                    if await logo_locators.count() > 1:
                        logo_url = await logo_locators.nth(1).get_attribute("src")
                    elif await logo_locators.count() > 0:
                        logo_url = await logo_locators.nth(0).get_attribute("src")
                    if href:
                        full_url = urljoin(base_url, href)
                        game_links_info.append({"name": name, "url": full_url, "logo": logo_url or default_logo})

            await page.close()

            for game in game_links_info:
                stream_url = await find_stream_from_servers_on_page(p, context, game["url"], base_url, session)
                if stream_url:
                    found_streams[game["name"]] = (stream_url, "Live Games", game["logo"])

            # 24/7 channel pages
            for url in channel_urls:
                slug = url.strip("/").split("/")[-1]
                stream_url = await find_stream_from_servers_on_page(p, context, url, base_url, session)
                if stream_url:
                    found_streams[slug] = (stream_url, "24/7 Channels", None)
        except Exception as e:
            print(f" ❌ Error scraping {group_prefix}: {e}")
        finally:
            await context.close()
            await browser.close()

    for slug, data_tuple in sorted(found_streams.items()):
        stream_url, category, scraped_logo = data_tuple
        info = CHANNEL_METADATA.get(slug, {})
        pretty_name = info.get("name", normalize_game_name(slug))
        tvg_id = info.get("id", default_id)
        tvg_logo = info.get("logo") or scraped_logo or default_logo
        results.append({
            "name": pretty_name,
            "url": stream_url,
            "tvg_id": tvg_id,
            "tvg_logo": tvg_logo,
            "group": f"{group_prefix} - {category}",
            "ref": base_url,
        })
    return results


async def scrape_nba_league(p: async_playwright, default_logo: str) -> List[Dict]:
    """Special-case NBA that scrapes HTML and builds known stream URLs."""
    print(f"\nScraping NBAWebcast streams from {NBA_BASE_URL}...")
    results: List[Dict] = []

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        try:
            async with session.get(NBA_BASE_URL, timeout=25) as response:
                response.raise_for_status()
                html_content = await response.text()
        except Exception as e:
            print(f" ❌ Error fetching NBA page: {e}")
            return []

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            schedule_table = soup.find("table", class_="NBA_schedule_container")
            if not schedule_table:
                print(" ❌ Could not find NBA schedule table.")
                return []

            game_rows = schedule_table.find("tbody").find_all("tr")
            if not game_rows:
                print(" ❌ Found table but no game rows.")
                return []

            print(f" 🏀 Found {len(game_rows)} potential NBA games in the schedule.")

            for row in game_rows:
                watch_button = None
                buttons = row.find_all("button", class_="watch_btn")
                for btn in buttons:
                    if "bakup_btn" not in btn.get("class", []):
                        watch_button = btn
                        break

                if not watch_button:
                    continue

                team_name_tags = row.find_all("td", class_="teamvs")
                if len(team_name_tags) < 2:
                    continue

                away_team_tag = team_name_tags[0].find("span")
                home_team_tag = team_name_tags[1].find("span")

                if not away_team_tag or not home_team_tag:
                    continue

                away_team = away_team_tag.get_text(strip=True)
                home_team = home_team_tag.get_text(strip=True)
                game_name = f"{away_team} @ {home_team}"

                logo_tags = row.find_all("td", class_="teamlogo")
                logo_to_use = default_logo
                stream_key = None

                if len(logo_tags) == 2:
                    home_logo_img = logo_tags[1].find("img")
                    if home_logo_img and home_logo_img.get("src"):
                        logo_to_use = home_logo_img["src"]
                        match = re.search(r'/scoreboard/([a-z0-9]+)\.png', logo_to_use, re.I)
                        if match:
                            abbr = match.group(1).lower()
                            stream_key = f"nba_{abbr}"

                if not stream_key:
                    print(f" ⚠️ Could not find stream abbreviation for {game_name}. Skipping.")
                    continue

                stream_url = NBA_STREAM_URL_PATTERN.format(stream_key=stream_key)

                if await verify_stream_url(session, stream_url, headers=NBA_CUSTOM_HEADERS):
                    results.append({
                        "name": game_name,
                        "url": stream_url,
                        "tvg_id": "NBA.Basketball.Dummy.us",
                        "tvg_logo": logo_to_use,
                        "group": "NBAWebcast - Live Games",
                        "ref": NBA_BASE_URL,
                        "custom_headers": NBA_CUSTOM_HEADERS,
                    })

        except Exception as e:
            print(f" ❌ Error parsing NBA HTML or processing rows: {e}")

    return results


def write_playlist(streams: List[Dict], filename: str):
    """Write TiviMate-only playlist (pipe | headers) with encoded UA."""
    if not streams:
        print("⏹️ No streams found.")
        return
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for entry in streams:
            f.write(
                f'#EXTINF:-1 tvg-id="{entry["tvg_id"]}" tvg-name="{entry["name"]}" '
                f'tvg-logo="{entry["tvg_logo"]}" group-title="{entry["group"]}",{entry["name"]}\n'
            )
            # build TiviMate appended headers (pipe |) and ensure UA is encoded
            origin = entry.get("custom_headers", {}).get("origin", entry.get("ref", ""))
            referrer = entry.get("custom_headers", {}).get("referrer", entry.get("ref", ""))
            user_agent = entry.get("custom_headers", {}).get("user_agent", USER_AGENT)
            ua_encoded = quote(user_agent, safe="")
            url_with_headers = f'{entry["url"]}|referer={referrer}|origin={origin}|user-agent={ua_encoded}|icy-metadata=1'
            f.write(url_with_headers + "\n")
    print(f"✅ TiviMate playlist saved to {filename} ({len(streams)} streams).")


async def main():
    print("🚀 Starting Sports Webcast Scraper...")

    # single Playwright instance used across tasks
    async with async_playwright() as p:
        NBA_DEFAULT_LOGO = "http://drewlive24.duckdns.org:9000/Logos/Basketball.png"
        # schedule scraping tasks sequentially but launched concurrently by gather
        tasks = [
            scrape_league(p, NFL_BASE_URL, NFL_CHANNEL_URLS, "NFLWebcast", "NFL.Dummy.us",
                          "http://drewlive24.duckdns.org:9000/Logos/Maxx.png"),
            scrape_league(p, NHL_BASE_URL, NHL_CHANNEL_URLS, "NHLWebcast", "NHL.Hockey.Dummy.us",
                          "http://drewlive24.duckdns.org:9000/Logos/Hockey.png"),
            scrape_league(p, MLB_BASE_URL, MLB_CHANNEL_URLS, "MLBWebcast", "MLB.Baseball.Dummy.us",
                          "http://drewlive24.duckdns.org:9000/Logos/MLB.png"),
            scrape_league(p, MLS_BASE_URL, MLS_CHANNEL_URLS, "MLSWebcast", "MLS.Soccer.Dummy.us",
                          "http://drewlive24.duckdns.org:9000/Logos/Football2.png"),
            scrape_nba_league(p, NBA_DEFAULT_LOGO),
        ]

        results = await asyncio.gather(*tasks)
        all_streams = [s for league in results for s in league]
        write_playlist(all_streams, OUTPUT_FILE)


if __name__ == "__main__":
    asyncio.run(main())
