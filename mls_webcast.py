import asyncio
import re
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://mlswebcast.com/"
HEADERS = {
    "referer": "https://mlswebcast.com/",
    "origin": "https://mlswebcast.com",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    )
}

VLC_LOGO = "https://i.postimg.cc/nrPfn86k/Football.png"


def clean_event_title(title: str) -> str:
    """Clean only the event title (NOT metadata)."""
    if not title:
        return "MLS Game"

    t = title.strip()

    # Replace '@' → 'vs'
    t = t.replace("@", "vs")

    # Remove commas ONLY INSIDE TITLE
    t = t.replace(",", "")

    # Clean double spaces
    t = re.sub(r"\s{2,}", " ", t).strip()

    return t


async def extract_m3u8_with_playwright(url: str, browser):
    """Load event page and capture m3u8 request URLs."""
    page = await browser.new_page()
    m3u8_links = []

    def handle_response(response):
        if ".m3u8" in response.url:
            m3u8_links.append(response.url)

    page.on("response", handle_response)

    try:
        await page.goto(url, timeout=45000)
        await page.wait_for_timeout(6000)
    except Exception:
        pass
    finally:
        await page.close()

    return m3u8_links


async def main():
    print(f"🔍 Fetching homepage: {BASE_URL}")

    async with async_playwright() as pw:
        browser = await pw.firefox.launch(headless=True)
        page = await browser.new_page()

        await page.goto(BASE_URL)
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Extract event page links
        event_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(BASE_URL) and "live" in href:
                if href not in event_links:
                    event_links.append(href)

        print(f"📌 Found {len(event_links)} event page(s) from homepage.")

        results = []

        # Process each event page
        for link in event_links:
            try:
                print(f"🔎 Processing event page: {link}")

                # Request event page HTML
                await page.goto(link)
                event_html = await page.content()
                event_soup = BeautifulSoup(event_html, "html.parser")

                # Extract title from og:title
                meta = event_soup.find("meta", property="og:title")
                if meta and meta.get("content"):
                    raw_title = meta["content"].strip()
                else:
                    # fallback to <title>
                    raw_title = event_soup.title.string.strip() if event_soup.title else "MLS Game"

                clean_title = clean_event_title(raw_title)

                # Capture the stream
                m3u8_candidates = await extract_m3u8_with_playwright(link, browser)

                if not m3u8_candidates:
                    print(f"⚠️ No m3u8 found for {link}")
                    continue

                final_stream = m3u8_candidates[-1]
                print(f"   ✔ Found stream: {final_stream}")

                results.append((clean_title, final_stream))

            except Exception as e:
                print(f"❌ Error processing {link}: {e}")

        await browser.close()

    if not results:
        print("❌ No streams captured.")
        return

    print("💾 Writing playlists...")

    # -----------------------------
    # VLC PLAYLIST
    # -----------------------------
    with open("MLSWebcast_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in results:
            f.write(
                f'#EXTINF:-1 tvg-id="MLS.Soccer.Dummy.us" '
                f'tvg-name="MLS" tvg-logo="{VLC_LOGO}" '
                f'group-title="MLS GAME",{title}\n'
            )
            f.write("#EXTVLCOPT:http-referrer=https://mlswebcast.com/\n")
            f.write("#EXTVLCOPT:http-origin=https://mlswebcast.com\n")
            f.write(
                "#EXTVLCOPT:http-user-agent="
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36\n"
            )
            f.write(f"{url}\n\n")

    # -----------------------------
    # TIVIMATE PLAYLIST
    # -----------------------------
    with open("MLSWebcast_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in results:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(
                f"{url}"
                f"|referer=https://mlswebcast.com/"
                f"|origin=https://mlswebcast.com"
                f"|user-agent=Mozilla%2F5.0%20(Windows%20NT%2010.0%3B%20Win64"
                f"%3B%20x64)%20AppleWebKit%2F537.36%20(KHTML%2C%20like%20Gecko)"
                f"%20Chrome%2F142.0.0.0%20Safari%2F537.36\n\n"
            )

    print("✅ Playlists created successfully!")


if __name__ == "__main__":
    asyncio.run(main())
