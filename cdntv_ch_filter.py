import asyncio
import os
import urllib.parse
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser

from utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "CDNTV"

CACHE_FILE = Cache(TAG, exp=10_800)
API_CACHE = Cache(f"{TAG}-api", exp=19_800)

# COUNTRY FILTER
ALLOWED_COUNTRIES = {"us", "ca", "ar", "mx", "uy", "cl", "co"}

API_URL = os.environ.get("CDNTV_CH_API_URL")
if API_URL and not API_URL.startswith(('http://', 'https://')):
    API_URL = f"https://{API_URL}"

VLC_OUTPUT_FILE = "cdn_ch_filter_vlc.m3u8"
TIVIMATE_OUTPUT_FILE = "cdn_ch_filter_tivimate.m3u8"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REFERER = "https://cdnlivetv.tv/"
ORIGIN = "https://cdnlivetv.tv"


def encode_user_agent(user_agent: str) -> str:
    return urllib.parse.quote(user_agent)


def generate_output_files():
    if not urls:
        log.info("No URLs to write")
        return

    vlc_content = "#EXTM3U\n"
    tivimate_content = "#EXTM3U\n"

    sorted_urls = sorted(urls.items(), key=lambda x: x[1].get("timestamp", 0))

    chno = 1
    for key, data in sorted_urls:
        if not data.get("url"):
            continue

        sport = key.split("[")[1].split("]")[0] if "[" in key else "Live"
        event_name = key.split("]")[-1].replace(f"({TAG})", "").strip()
        logo = data.get("logo", "")
        tvg_id = data.get("id", "Live.Event")
        url = data.get("url", "")
        link = data.get("link", "")

        if not url:
            continue

        extinf = f'#EXTINF:-1 tvg-chno="{chno}" tvg-id="{tvg_id}" tvg-name="{key}" tvg-logo="{logo}" group-title="{sport}",{event_name}\n'

        # VLC
        vlc_content += extinf
        vlc_content += f"#EXTVLCOPT:http-referrer={link}\n"
        vlc_content += f"#EXTVLCOPT:http-origin={ORIGIN}\n"
        vlc_content += f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n"
        vlc_content += f"{url}\n\n"

        # TiviMate
        encoded_ua = encode_user_agent(USER_AGENT)
        tivimate_url = f"{url}|referer={REFERER}|origin={ORIGIN}|user-agent={encoded_ua}"

        tivimate_content += extinf
        tivimate_content += f"{tivimate_url}\n\n"

        chno += 1

    with open(VLC_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(vlc_content)

    with open(TIVIMATE_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(tivimate_content)

    log.info(f"Generated {chno-1} channels")


async def get_events(cached_keys: list[str]) -> list[dict]:
    now = Time.clean(Time.now())
    events = []

    api_data = API_CACHE.load(per_entry=False)

    if not api_data:
        log.info("Fetching API...")

        api_url = API_URL or "v1/channels/"

        if r := await network.request(
            api_url,
            log=log,
            headers={
                "Referer": REFERER,
                "Origin": ORIGIN,
                "User-Agent": USER_AGENT
            }
        ):
            try:
                api_data = r.json()
                if isinstance(api_data, dict):
                    api_data = api_data.get("channels", [])
            except Exception as e:
                log.error(f"API parse error: {e}")
                api_data = []

        API_CACHE.write(api_data)

    if not api_data:
        return events

    log.info(f"Processing {len(api_data)} channels")

    for channel in api_data:
        try:
            # COUNTRY FILTER
            country_code = channel.get("code", "").lower()

            if country_code not in ALLOWED_COUNTRIES:
                continue

            # Optional: skip offline
            if channel.get("status") != "online":
                continue

            name = channel.get("name")
            if not name:
                continue

            stream_url = channel.get("url")
            if not stream_url or not stream_url.startswith("http"):
                continue

            logo = channel.get("image", "")
            channel_id = f"{name}.{country_code}".replace(" ", ".").lower()

            key = f"[{country_code.upper()}] {name} ({TAG})"

            if key in cached_keys:
                continue

            events.append({
                "sport": country_code.upper(),
                "event": name,
                "link": stream_url,
                "timestamp": now.timestamp(),
                "logo": logo,
                "id": channel_id
            })

            log.info(f"Accepted: {name} ({country_code})")

        except Exception as e:
            log.error(f"Error: {e}")

    return events


async def scrape(browser: Browser):
    cached_urls = CACHE_FILE.load() or {}
    urls.update(cached_urls)

    if events := await get_events(list(cached_urls.keys())):
        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, 1):
                async with network.event_page(context) as page:

                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        log=log,
                        timeout=15,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    if url:
                        key = f"[{ev['sport']}] {ev['event']} ({TAG})"

                        urls[key] = cached_urls[key] = {
                            "url": url,
                            "logo": ev["logo"],
                            "timestamp": ev["timestamp"],
                            "id": ev["id"],
                            "link": link,
                        }

    CACHE_FILE.write(cached_urls)
    generate_output_files()


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        await scrape(browser)
        await browser.close()


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
