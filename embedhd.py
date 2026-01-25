import os
import asyncio
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from utils import Cache, Time, get_logger, leagues

log = get_logger(__name__)

TAG = "EMBEDHD"

BASE_URL = os.getenv("EMBEDHD_API_URL")
if not BASE_URL:
    raise RuntimeError("EMBEDHD_API_URL secret is not set")

CACHE_FILE = Cache(TAG, exp=5_400)
API_CACHE = Cache(f"{TAG}-api", exp=28_800)

OUT_VLC = Path("embedhd_vlc.m3u8")
OUT_TIVI = Path("embedhd_tivimate.m3u8")

REFERER = "https://vividmosaica.com/"
ORIGIN = "https://vividmosaica.com/"

UA_RAW = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
UA_ENC = quote(UA_RAW)

urls: dict[str, dict] = {}


# ---------------------------
# API
# ---------------------------

async def get_events(cached_keys: list[str]) -> list[dict]:
    now = Time.clean(Time.now())

    if not (api := API_CACHE.load(per_entry=False)):
        log.info("Refreshing API cache")
        api = {"timestamp": now.timestamp()}
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(BASE_URL, timeout=30_000)
            api = await page.evaluate("() => window.__DATA__ || {}")
            await browser.close()
        api["timestamp"] = now.timestamp()
        API_CACHE.write(api)

    events = []
    start = now.delta(hours=-3)
    end = now.delta(minutes=30)

    for day in api.get("days", []):
        for ev in day.get("items", []):
            if ev.get("league") == "channel tv":
                continue

            dt = Time.from_str(ev["when_et"], timezone="ET")
            if not start <= dt <= end:
                continue

            streams = ev.get("streams") or []
            if not streams:
                continue

            key = f"[{ev['league']}] {ev['title']} ({TAG})"
            if key in cached_keys:
                continue

            events.append({
                "sport": ev["league"],
                "event": ev["title"],
                "link": streams[0]["link"],
                "timestamp": now.timestamp(),
            })

    return events


# ---------------------------
# PLAYWRIGHT STREAM RESOLVER
# ---------------------------

async def extract_m3u8(page, url: str, idx: int) -> str | None:
    m3u8_url = None

    def on_response(resp):
        nonlocal m3u8_url
        u = resp.url
        if ".m3u8" in u and "hls" in u:
            m3u8_url = u

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Wait iframe
        iframe = await page.wait_for_selector("iframe", timeout=10_000)
        frame = await iframe.content_frame()

        # Try clicking common play buttons
        for selector in [
            "button",
            ".vjs-big-play-button",
            ".jw-icon-playback",
            "div[role='button']",
        ]:
            try:
                await frame.click(selector, timeout=2_000)
                await asyncio.sleep(1)
                if m3u8_url:
                    break
            except Exception:
                pass

        # Final wait for network
        for _ in range(10):
            if m3u8_url:
                return m3u8_url
            await asyncio.sleep(1)

    except PWTimeout:
        log.warning(f"URL {idx}) Timed out after 10s, skipping event")
    except Exception as e:
        log.warning(f"URL {idx}) Failed: {e}")

    return None


# ---------------------------
# PLAYLIST BUILDERS
# ---------------------------

def build_vlc(data: dict) -> str:
    out = ["#EXTM3U"]
    ch = 1
    for name, e in data.items():
        out.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}'
        )
        out.append(f"#EXTVLCOPT:http-referrer={REFERER}")
        out.append(f"#EXTVLCOPT:http-origin={ORIGIN}")
        out.append(f"#EXTVLCOPT:http-user-agent={UA_RAW}")
        out.append(e["url"])
        ch += 1
    return "\n".join(out) + "\n"


def build_tivimate(data: dict) -> str:
    out = ["#EXTM3U"]
    ch = 1
    for name, e in data.items():
        out.append(
            f'#EXTINF:-1 tvg-chno="{ch}" tvg-id="{e["id"]}" '
            f'tvg-name="{name}" tvg-logo="{e["logo"]}" group-title="Live Events",{name}'
        )
        out.append(
            f'{e["url"]}|referer={REFERER}|origin={ORIGIN}'
            f'|user-agent={UA_ENC}|icy-metadata=1'
        )
        ch += 1
    return "\n".join(out) + "\n"


# ---------------------------
# MAIN SCRAPER
# ---------------------------

async def main():
    log.info("🚀 Starting EmbedHD scraper...")

    cached = CACHE_FILE.load() or {}
    urls.update(cached)

    events = await get_events(list(cached.keys()))
    log.info(f"Processing {len(events)} new URL(s)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=UA_RAW,
            referer=REFERER,
        )
        page = await context.new_page()

        for i, ev in enumerate(events, 1):
            stream = await extract_m3u8(page, ev["link"], i)
            if not stream:
                continue

            tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])
            key = f"[{ev['sport']}] {ev['event']} ({TAG})"

            urls[key] = {
                "url": stream,
                "logo": logo,
                "id": tvg_id or "Live.Event.us",
                "timestamp": ev["timestamp"],
            }

        await browser.close()

    CACHE_FILE.write(urls)
    OUT_VLC.write_text(build_vlc(urls), encoding="utf-8")
    OUT_TIVI.write_text(build_tivimate(urls), encoding="utf-8")

    log.info(f"Wrote {len(urls)} total events")


if __name__ == "__main__":
    asyncio.run(main())
