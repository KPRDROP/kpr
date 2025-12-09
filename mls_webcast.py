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
    """Clean only the event title (NOT VLC metadata)."""
    if not title:
        return "MLS Game"

    t = title.strip()

    # Replace @ with vs ONLY IN TITLE
    t = t.replace("@", "vs")

    # Remove ONLY commas inside title
    t = t.replace(",", "")

    return t


async def extract_m3u8_with_playwright(url: str, browser):
    """Load event page in Playwright and capture streaming .m3u8 URLs."""
    page = await browser.new_page()

    m3u8_links = []

    try:
        await page.route("**/*", lambda route: route.continue_())

        page.on("response", lambda response: (
            m3u8_links.append(response.url)
            if ".m3u8" in response.url else None
        ))

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

        # GET HOMEPAGE HTML
        await page.goto(BASE_URL)
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Extract event links
        event_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(BASE_URL) and "live" in href:
                if href not in event_links:
                    event_links.append(href)

        print(f"📌 Found {len(event_links)} event page(s) from homepage.")

        results = []

        for link in event_links:
            try:
                # Extract event title using BeautifulSoup
                title = None
                card = soup.find("a", href=link)
                if card:
                    parent = card.find_parent("div")
                    if parent:
                        p = parent.find("p")
                        if p:
                            title = p.get_text(strip=True)

                if not title:
                    title = "MLS Game"

                clean_title = clean_event_title(title)

                print(f"🔎 Processing event: {clean_title} -> {link}")

                # Capture .m3u8 via Playwright
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

    # ------------------------------------------------------------
    #  WRITE PLAYLISTS
    # ------------------------------------------------------------
    print("💾 Writing playlists...")

    # VLC playlist
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

    # TiviMate playlist
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

    print("✅ Playlists created: MLSWebcast_VLC.m3u8 & MLSWebcast_TiviMate.m3u8")


if __name__ == "__main__":
    asyncio.run(main())
