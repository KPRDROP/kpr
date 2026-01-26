import os
import asyncio
import time
import re
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.async_api import async_playwright
from utils import Cache, Time, get_logger, leagues

log = get_logger(__name__)

TAG = "EMBEDHD"

BASE_URL = os.getenv("EMBEDHD_API_URL")
if not BASE_URL:
    raise RuntimeError("EMBEDHD_API_URL not set")

CACHE = Cache(TAG, exp=5400)
API_CACHE = Cache(f"{TAG}-api", exp=28800)

OUT_TIVI = Path("embedhd_tivimate.m3u8")

REFERER = "https://embedhd.org/"
ORIGIN = "https://embedhd.org/"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
UA_ENC = quote(UA)

M3U8_RE = re.compile(r"\.m3u8", re.I)

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
# REAL M3U8 RESOLVER (IFRAME FIRST)
# --------------------------------------------------
async def resolve_m3u8(page, fetch_url, idx):
    try:
        # 🔥 Inject JS hook BEFORE page loads
        await page.add_init_script("""
            (() => {
                window.__M3U8__ = null;

                const _fetch = window.fetch;
                window.fetch = async function(...args) {
                    const res = await _fetch.apply(this, args);
                    try {
                        const url = args[0]?.url || args[0];
                        if (url && url.includes(".m3u8")) {
                            window.__M3U8__ = url;
                        }
                    } catch (e) {}
                    return res;
                };

                const _open = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function(method, url) {
                    if (url && url.includes(".m3u8")) {
                        window.__M3U8__ = url;
                    }
                    return _open.apply(this, arguments);
                };
            })();
        """)

        await page.goto(fetch_url, wait_until="domcontentloaded", timeout=20000)

        # allow EmbedHD JS to resolve stream
        await asyncio.sleep(5)

        # poll JS memory (this is the KEY)
        start = time.time()
        while time.time() - start < 30:
            m3u8 = await page.evaluate("window.__M3U8__")
            if m3u8:
                log.info(f"URL {idx}) m3u8 captured")
                return m3u8
            await asyncio.sleep(0.5)

        raise TimeoutError("m3u8 not detected")

    except Exception as e:
        log.warning(f"URL {idx}) Failed: {e}")
        return None

    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass

# --------------------------------------------------
# PLAYLIST
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
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 720},
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
