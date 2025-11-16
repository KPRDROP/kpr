import asyncio
import json
import os
from playwright.async_api import async_playwright

OUTPUT_FILE = "playlist.m3u"
LOGO_DIR = "logos"

# Custom realistic UA to bypass Cloudflare
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Sample PPVLand channel list JSON
CHANNELS_FILE = "ppvland.json"


# -------------------------------
# Load channels
# -------------------------------
def load_channels():
    if not os.path.exists(CHANNELS_FILE):
        print(f"❌ Missing {CHANNELS_FILE}")
        return []

    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# -------------------------------
# Save playlist
# -------------------------------
def save_playlist(channels):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            f.write(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-logo="{ch["logo"]}",{ch["name"]}\n')
            f.write(f'{ch["url"]}\n')

    print(f"✅ Playlist saved as {OUTPUT_FILE}")


# -------------------------------
# Grab M3U8 from iframe using Chromium
# -------------------------------
async def grab_m3u8_from_iframe(context, page, iframe_url):
    found_urls = set()

    def handle_response(response):
        url = response.url
        if ".m3u8" in url:
            print(f"🎯 Found stream: {url}")
            found_urls.add(url)

    context.on("response", handle_response)

    try:
        print(f"⏳ Loading iframe: {iframe_url}")
        await page.goto(
            iframe_url,
            timeout=0,               # allow Cloudflare delays
            wait_until="domcontentloaded"
        )

        # Give nested iframes time to load
        await asyncio.sleep(10)

        # Force execution of JS players
        await page.evaluate("window.scrollBy(0, 250)")
        await asyncio.sleep(3)

    except Exception as e:
        print(f"❌ iframe error: {e}")

    # Cleanup listener
    context.remove_listener("response", handle_response)

    return list(found_urls)


# -------------------------------
# Main workflow
# -------------------------------
async def main():
    channels = load_channels()
    if not channels:
        return

    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-web-security",
            ]
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            ignore_https_errors=True,
            java_script_enabled=True,
            bypass_csp=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
            }
        )

        page = await context.new_page()

        print("\n==============================")
        print("  🚀 Starting M3U8 extraction")
        print("==============================\n")

        for ch in channels:
            print(f"\n🔎 Processing: {ch['name']}")

            urls = await grab_m3u8_from_iframe(context, page, ch["iframe"])

            if not urls:
                print(f"⚠️ No streams found for {ch['name']}")
                continue

            # Grab first working stream
            ch["url"] = urls[0]
            results.append(ch)

        await browser.close()

    if results:
        save_playlist(results)
    else:
        print("❌ No valid streams collected.")


# -------------------------------
# Run
# -------------------------------
if __name__ == "__main__":
    asyncio.run(main())
