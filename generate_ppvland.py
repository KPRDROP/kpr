import json
import asyncio
import requests
from playwright.async_api import async_playwright

API_URL = "https://ppv.to/api/streams"


async def fetch_streams():
    print("🌐 Fetching streams from", API_URL)
    r = requests.get(API_URL, timeout=10)
    r.raise_for_status()
    data = r.json()

    # If the API returns ["8757","9981",...]
    if data and isinstance(data[0], str):
        print("🔄 Detected string-only stream list format.")
        return [{"stream_id": x, "name": x, "category": "PPV"} for x in data]

    # Standard format: [{"stream_id":123, "name":...}]
    print("🔄 Detected full JSON object format.")
    return data


async def safe_goto(page, url):
    for attempt in range(1, 3):
        try:
            print(f"🌐 Opening: {url} (attempt {attempt})")
            await page.goto(url, timeout=8000, wait_until="domcontentloaded")
            return True
        except Exception:
            print(f"⚠️ Load fail attempt {attempt}: {url}")
            await asyncio.sleep(1)

    print(f"⏭️ Skipping dead link: {url}")
    return False


async def run():
    print("🚀 PPVLand Chromium Scraper Starting...")
    streams = await fetch_streams()
    print(f"📺 {len(streams)} streams found in API")

    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        for item in streams:
            stream_id = item["stream_id"]
            name = item.get("name", stream_id)
            category = item.get("category", "PPV")

            url = f"https://embednow.top/embed/cfb/{stream_id}-{stream_id}"

            ok = await safe_goto(page, url)
            if not ok:
                continue

            m3u8_links = await page.eval_on_selector_all(
                "video, source, script",
                "elements => elements.map(e => e.src).filter(x => x && x.includes('.m3u8'))"
            )

            if m3u8_links:
                print(f"🎯 Found stream {stream_id}: {m3u8_links[0]}")
                results.append({
                    "id": stream_id,
                    "name": name,
                    "category": category,
                    "m3u8": m3u8_links[0]
                })

        await browser.close()

    print(f"💾 Saving {len(results)} working streams…")
    with open("ppvland.m3u", "w", encoding="utf-8") as f:
        for x in results:
            f.write(f"#EXTINF:-1 group-title=\"{x['category']}\",{x['name']}\n")
            f.write(f"{x['m3u8']}\n\n")

    print("✅ Finished.")


if __name__ == "__main__":
    asyncio.run(run())
