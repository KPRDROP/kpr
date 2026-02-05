import asyncio
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import async_playwright

from utils import Cache, Time, get_logger

log = get_logger(__name__)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

BASE_URL = "https://streamtp10.com/"
TAG = "STR"

OUT_FILE = Path("str_tivimate.m3u8")
CACHE = Cache("str_channels", exp=6 * 60 * 60)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) "
    "Gecko/20100101 Firefox/147.0"
)
UA_ENC = quote_plus(USER_AGENT)

# --------------------------------------------------
def build_playlist(data: dict) -> None:
    lines = ["#EXTM3U"]
    chno = 1

    for name, e in data.items():
        lines.append(
            f'#EXTINF:-1 tvg-chno="{chno}" '
            f'tvg-id="Live.Event.us" '
            f'tvg-name="{name}" '
            f'tvg-logo="{e["logo"]}" '
            f'group-title="Live Events",{name} --- (ACTIVO)'
        )
        lines.append(
            f'{e["m3u8"]}'
            f'|referer={BASE_URL}'
            f'|origin={BASE_URL}'
            f'|user-agent={UA_ENC}'
        )
        chno += 1

    OUT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"✅ Wrote {len(data)} entries to str_tivimate.m3u8")


# --------------------------------------------------
async def scrape():
    cached = CACHE.load() or {}
    log.info(f"Loaded {len(cached)} cached channel(s)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
        )

        page = await context.new_page()

        # ---------------------------
        # STEP 1: Load homepage
        # ---------------------------
        await page.goto(
            BASE_URL,
            wait_until="networkidle",
            timeout=60_000,
            referer=BASE_URL,
        )

        links = await page.eval_on_selector_all(
            "a[href*='global'][href*='stream=']",
            "els => els.map(e => e.href)"
        )

        links = list(dict.fromkeys(links))
        log.info(f"Discovered {len(links)} channel link(s)")

        # ---------------------------
        # STEP 2: Visit each channel
        # ---------------------------
        for url in links:
            name = url.split("stream=")[-1].upper()

            if name in cached:
                continue

            log.info(f"▶ Opening channel: {name}")

            m3u8_url = None

            async def on_request(req):
                nonlocal m3u8_url
                if ".m3u8" in req.url and not m3u8_url:
                    m3u8_url = req.url
                    log.info(f"🎯 Captured m3u8 → {m3u8_url}")

            page.on("request", on_request)

            try:
                await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=60_000,
                    referer=BASE_URL,
                )
                await page.wait_for_timeout(5_000)
            except Exception as e:
                log.warning(f"Failed loading {url}: {e}")

            page.remove_listener("request", on_request)

            if not m3u8_url:
                log.warning(f"No m3u8 found for {name}")
                continue

            cached[name] = {
                "m3u8": m3u8_url,
                "logo": "https://i.postimg.cc/tgrdPjjC/live-icon-streaming.png",
                "timestamp": Time.clean(Time.now()).timestamp(),
            }

        await browser.close()

    CACHE.write(cached)
    build_playlist(cached)


# --------------------------------------------------
if __name__ == "__main__":
    asyncio.run(scrape())
