#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

HOME_URL = "https://streambtw.live"
VLC_OUTPUT = "Streambtw_VLC.m3u8"
TIVIMATE_OUTPUT = "Streambtw_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

HEADERS = {
    "referer": HOME_URL,
    "origin": "https://streambtw.com",
    "user-agent": USER_AGENT,
}

def sanitize(title):
    return title.replace("@", " vs ").replace("_", " ").replace("-", " ").strip()

async def sniff_m3u8(page):
    """Capture ANY .m3u8 requested by the iframe page."""
    found = []

    def handle_request(req):
        url = req.url
        if ".m3u8" in url:
            if url not in found:
                found.append(url)

    page.on("request", handle_request)
    return found


async def process_iframe(browser, url):
    page = await browser.new_page(user_agent=USER_AGENT)

    # start sniffing
    m3u8_list = sniff_m3u8(page)

    try:
        await page.goto(url, timeout=20000, wait_until="networkidle")

        # also wait after load for dynamic JS calls
        await page.wait_for_timeout(8000)
    except:
        pass

    return (await m3u8_list)


async def main():
    print("🔍 Fetching StreamBTW homepage...")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])

        page = await browser.new_page(user_agent=USER_AGENT)
        await page.goto(HOME_URL, wait_until="domcontentloaded")

        # find ALL iframe links
        links = await page.eval_on_selector_all(
            "iframe",
            "nodes => nodes.map(n => n.src)"
        )

        # Also catch /iframe/*.php links
        more = re.findall(r'href="([^"]*iframe/[^"]+)"', await page.content())
        links += [page.urljoin(l) for l in more]

        # remove None and dedupe
        links = [l for l in links if l]
        links = list(dict.fromkeys(links))

        print(f"📡 Found {len(links)} iframe pages.")

        results = []

        for idx, link in enumerate(links, start=1):
            print(f"\n[{idx}/{len(links)}] Checking {link}")
            m3u8s = await process_iframe(browser, link)

            if not m3u8s:
                print("⚠️ No m3u8 found.")
                continue

            main_m3u8 = m3u8s[0]

            # build readable channel name
            name = sanitize(link.split("/")[-1].replace(".php", ""))

            results.append({
                "name": name,
                "url": main_m3u8,
                "ref": link,
            })

            print(f"✅ Captured stream: {main_m3u8}")

        await browser.close()

    if not results:
        print("❌ No streams found at all.")
        return

    # ───────────────────────────────────────────────
    # VLC output
    # ───────────────────────────────────────────────
    lines = ["#EXTM3U"]
    for r in results:
        lines.append(f'#EXTINF:-1 group-title="StreamBTW",{r["name"]}')
        lines.append(f'#EXTVLCOPT:http-user-agent={USER_AGENT}')
        lines.append(f'#EXTVLCOPT:http-referrer={r["ref"]}')
        lines.append(r["url"])
    Path(VLC_OUTPUT).write_text("\n".join(lines), encoding="utf-8")

    # ───────────────────────────────────────────────
    # TiviMate output
    # ───────────────────────────────────────────────
    tlines = ["#EXTM3U"]
    for r in results:
        encoded_ua = USER_AGENT.replace(" ", "%20")
        stream = f'{r["url"]}|referer={r["ref"]}|user-agent={encoded_ua}'
        tlines.append(f'#EXTINF:-1 group-title="StreamBTW",{r["name"]}')
        tlines.append(stream)
    Path(TIVIMATE_OUTPUT).write_text("\n".join(tlines), encoding="utf-8")

    print("\n🎉 DONE! All playable m3u8 streams captured.")


if __name__ == "__main__":
    asyncio.run(main())
