import asyncio
import re
import sys
from playwright.async_api import async_playwright
import aiohttp
from bs4 import BeautifulSoup

BASE_URL = "https://mlswebcast.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) "
    "Gecko/20100101 Firefox/145.0"
)
HEADERS = {"User-Agent": USER_AGENT}


# ------------------------------------------------------
# Clean metadata titles
# ------------------------------------------------------
def clean_title(title: str) -> str:
    """
    Remove SEO garbage like:
    | MLS Live Stream Free Online No Sign-up | MLSStreams - MLS WebCast
    """
    if not title:
        return "Untitled Event"

    if "|" in title:
        title = title.split("|")[0].strip()

    return title.strip()


# ------------------------------------------------------
# Write VLC + TiviMate playlists
# ------------------------------------------------------
def write_playlists(streams):
    if not streams:
        print("❌ No streams captured, skipping playlist write.")
        return

    # VLC playlist
    with open("Webcast_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            title = clean_title(s["title"])
            f.write(f"#EXTINF:-1,{title}\n{s['url']}\n")

    # TiviMate playlist
    with open("Webcast_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for s in streams:
            title = clean_title(s["title"])
            headers = (
                f"|referer=https://mlswebcast.com/"
                f"|origin=https://mlswebcast.com"
                f"|user-agent={USER_AGENT.replace(' ', '%20')}"
            )
            f.write(f"#EXTINF:-1,{title}\n{s['url']}{headers}\n")

    print("✅ Playlists written:")
    print("   - Webcast_VLC.m3u8")
    print("   - Webcast_TiviMate.m3u8")


# ------------------------------------------------------
# Extract event pages from homepage
# ------------------------------------------------------
async def fetch_event_links():
    print(f"🔍 Fetching homepage: {BASE_URL}")
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(BASE_URL) as resp:
            html = await resp.text()

    soup = BeautifulSoup(html, "html.parser")
    links = []

    # Event pages come from <a href="...">Watch...</a>
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "mlswebcast.com" in href and "/live" in href:
            if href not in links:
                links.append(href)

    print(f"📌 Found {len(links)} event page(s) from homepage.")
    return links


# ------------------------------------------------------
# Extract m3u8 from a single event page
# ------------------------------------------------------
async def scrape_event(playwright, url, title_guess=None):
    browser = await playwright.firefox.launch(headless=True)
    context = await browser.new_context(
        user_agent=USER_AGENT,
        extra_http_headers={
            "referer": BASE_URL,
            "origin": BASE_URL,
        },
    )
    page = await context.new_page()

    title_guess = clean_title(title_guess or "MLS Stream")

    print(f"🔎 Processing event: {title_guess} -> {url}")
    print(f" ↳ Playwright navigating to {url}")

    captured_m3u8 = None

    async def on_response(response):
        nonlocal captured_m3u8
        try:
            url = response.url
            if ".m3u8" in url and "webcast" in url:
                print(f" ↳ network captured candidate: {url}")
                captured_m3u8 = url
        except:
            pass

    page.on("response", on_response)

    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
    except Exception as e:
        print(f"⚠️ Navigation error: {e}")

    await page.wait_for_timeout(2000)

    await browser.close()

    if captured_m3u8:
        return {"title": title_guess, "url": captured_m3u8}

    print(f"⚠️ No m3u8 found for {url}")
    return None


# ------------------------------------------------------
# Main asynchronous controller
# ------------------------------------------------------
async def main():
    event_pages = await fetch_event_links()
    streams = []

    async with async_playwright() as playwright:
        for event_url in event_pages:
            # Extract readable title from URL slug (best-effort)
            slug = event_url.rstrip("/").split("/")[-1].replace("-", " ").title()

            stream = await scrape_event(playwright, event_url, slug)
            if stream:
                streams.append(stream)

    if streams:
        write_playlists(streams)
    else:
        print("❌ No streams captured.")


# ------------------------------------------------------
# Run script
# ------------------------------------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
