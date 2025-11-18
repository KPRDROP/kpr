import asyncio
import re
import base64
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

BASE_URL = "https://nflwebcast.com/sbl/"
HEADERS = {
    "Referer": BASE_URL,
    "Origin": BASE_URL.rstrip("/")
}


# --------------------------------------------------------------------
# Navigation With Robust Retry
# --------------------------------------------------------------------
async def safe_goto(page, url, retries=5):
    for attempt in range(1, retries + 1):
        try:
            print(f"➡️ Navigating to {url} (attempt {attempt})")
            await page.goto(url, timeout=45000, wait_until="networkidle")
            await page.wait_for_timeout(1500)
            return True
        except Exception as e:
            print(f"⚠️ Navigation failed: {e}")
            await page.wait_for_timeout(1500)

    print("❌ Giving up navigation.")
    return False


# --------------------------------------------------------------------
# Extract Plain .m3u8 URLs
# --------------------------------------------------------------------
def extract_m3u8(html):
    return list(set(re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', html)))


# --------------------------------------------------------------------
# Deep Scan Mode: Extract streams from scripts, js, base64, json
# --------------------------------------------------------------------
def deep_scan_m3u8(html):
    streams = []

    # 1. Standard detection
    streams.extend(extract_m3u8(html))

    # 2. Decode base64 blobs
    base64_blobs = re.findall(rb"[A-Za-z0-9+/]{50,}={0,2}", html.encode())
    for blob in base64_blobs:
        try:
            decoded = base64.b64decode(blob).decode(errors="ignore")
            streams.extend(extract_m3u8(decoded))
        except Exception:
            continue

    # 3. JS variable assignments: var src="xxx.m3u8"
    var_matches = re.findall(r"['\"](https?://[^\"']+\.m3u8[^\"']*)['\"]", html)
    streams.extend(var_matches)

    # 4. JSON-like structures
    json_candidates = re.findall(r"{[^{}]+}", html)
    for block in json_candidates:
        streams.extend(extract_m3u8(block))

    # 5. Look inside escaped HTML
    escaped = html.replace("\\/", "/")
    streams.extend(extract_m3u8(escaped))

    return list(set(streams))



# --------------------------------------------------------------------
# Scraper Core Logic
# --------------------------------------------------------------------
async def scrape_nfl():
    print("🚀 Starting NFL Webcast Scraper...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(extra_http_headers=HEADERS)
        page = await context.new_page()

        # Load homepage
        ok = await safe_goto(page, BASE_URL)
        if not ok:
            return []

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Core selector for event links
        event_links = []
        for a in soup.select("div.match-block a"):
            href = a.get("href", "")
            if not href:
                continue

            if href.startswith("/"):
                href = BASE_URL.rstrip("/") + href
            if href.startswith(BASE_URL):
                event_links.append(href)

        event_links = list(set(event_links))
        print(f"📌 Found {len(event_links)} event links")

        all_streams = []

        # Visit each event page
        for link in event_links:
            print(f"\n📺 Event Page: {link}")

            ok = await safe_goto(page, link)
            if not ok:
                continue

            event_html = await page.content()
            soup2 = BeautifulSoup(event_html, "html.parser")

            # ------------------------------
            # Step 1: Extract from iframe tags
            # ------------------------------
            iframes = soup2.select("iframe, .iframe-container iframe, .video-container iframe")

            for iframe in iframes:
                src = iframe.get("src", "")
                if not src:
                    continue

                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = BASE_URL.rstrip("/") + src

                print(f"   ➡️ Found iframe: {src}")

                ok = await safe_goto(page, src)
                if not ok:
                    continue

                frame_html = await page.content()

                # Shallow extraction
                m3u8s = extract_m3u8(frame_html)
                for m in m3u8s:
                    print(f"   🎯 Shallow: {m}")
                    all_streams.append(m)

                # Deep scan extraction
                deep_results = deep_scan_m3u8(frame_html)
                for m in deep_results:
                    print(f"   🔍 Deep Scan: {m}")
                    all_streams.append(m)

            # ------------------------------
            # Step 2: Deep scan the event page HTML too
            # ------------------------------
            page_deep = deep_scan_m3u8(event_html)
            for m in page_deep:
                print(f"   🔍 Deep Scan (event page): {m}")
                all_streams.append(m)

        await browser.close()

        return list(set(all_streams))


# --------------------------------------------------------------------
# Write playlist
# --------------------------------------------------------------------
def write_playlist(streams):
    filename = "NFLWebcast.m3u8.m3u8"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, s in enumerate(streams, start=1):
            f.write(f"#EXTINF:-1,NFL Stream {i}\n{s}|Referer={BASE_URL}|Origin={BASE_URL}\n")
    print(f"✅ Playlist saved → {filename}")


# --------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------
async def main():
    streams = await scrape_nfl()
    if streams:
        print(f"🔥 Streams found: {len(streams)}")
        write_playlist(streams)
    else:
        print("❌ No streams found.")


if __name__ == "__main__":
    asyncio.run(main())
