import os
import asyncio
import time
import re
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright
from utils import Cache, Time, get_logger, leagues

log = get_logger(__name__)

TAG = "EMBEDHD"

BASE_URL = os.getenv("EMBEDHD_API_URL")
if not BASE_URL:
    raise RuntimeError("EMBEDHD_API_URL not set")

CACHE = Cache(TAG, exp=5400)
API_CACHE = Cache(f"{TAG}-api", exp=28800)

OUT_VLC = Path("embedhd_vlc.m3u8")
OUT_TIVI = Path("embedhd_tivimate.m3u8")

REFERER = "https://embedhd.org/"
ORIGIN = "https://embedhd.org/"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
UA_ENC = quote(UA)

M3U8_RE = re.compile(r"\.m3u8(\?|$)")

events_cache: dict[str, dict] = {}

# --------------------------------------------------
# API
# --------------------------------------------------
async def get_events(existing_keys):
    now = Time.clean(Time.now())

    if not (api := API_CACHE.load(per_entry=False)):
        log.info("Refreshing API cache")
        import requests
        api = requests.get(BASE_URL, timeout=15).json()
        api["timestamp"] = now.timestamp()
        API_CACHE.write(api)

    items = []
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
            if key in existing_keys:
                continue

            items.append({
                "sport": ev["league"],
                "event": ev["title"],
                "link": streams[0]["link"],
                "timestamp": now.timestamp(),
            })

    return items

# --------------------------------------------------
# M3U8 extractor (IFRAME SAFE)
# --------------------------------------------------
async def resolve_m3u8(page, url, idx):
    found = []

    def on_response(response):
        try:
            if M3U8_RE.search(response.url):
                found.append(response.url)
        except Exception:
            pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # wait for iframe injection
        await page.wait_for_selector("iframe", timeout=15000)

        # allow player boot + autoplay
        await asyncio.sleep(5)

        # force play inside iframe
        await page.evaluate("""
            document.querySelectorAll("iframe").forEach(f => {
                try {
                    const v = f.contentDocument?.querySelector("video");
                    if (v) {
                        v.muted = true;
                        v.play();
                    }
                } catch(e){}
            });
        """)

        start = time.time()
        while time.time() - start < 40:
            if found:
                return found[0]
            await asyncio.sleep(0.5)

        raise TimeoutError("m3u8 not detected")

    except Exception as e:
        log.warning(f"URL {idx}) Failed: {e}")
        return None

    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

# --------------------------------------------------
# Playlist builder
# --------------------------------------------------
def build_tivimate(data):
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

# --------------------------------------------------
# MAIN
# --------------------------------------------------
async def main():
    log.info("🚀 Starting EmbedHD scraper...")

    cached = CACHE.load() or {}
    events_cache.update(cached)

    new_events = await get_events(list(cached.keys()))
    log.info(f"Processing {len(new_events)} new URL(s)")

    if not new_events:
        log.info("No new events found")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--disable-features=MediaSessionService"
            ]
        )

        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 720},
            java_script_enabled=True,
        )

        page = await context.new_page()

        for i, ev in enumerate(new_events, 1):
            m3u8 = await resolve_m3u8(page, ev["link"], i)
            if not m3u8:
                continue

            tvg_id, logo = leagues.get_tvg_info(ev["sport"], ev["event"])
            key = f"[{ev['sport']}] {ev['event']} ({TAG})"

            events_cache[key] = {
                "url": m3u8,
                "logo": logo,
                "id": tvg_id or "Live.Event.us",
                "timestamp": ev["timestamp"],
            }

        await browser.close()

    CACHE.write(events_cache)
    OUT_TIVI.write_text(build_tivimate(events_cache), encoding="utf-8")

    log.info(f"Wrote {len(events_cache)} total events")

if __name__ == "__main__":
    asyncio.run(main())
