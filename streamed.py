import asyncio
import re
import requests
import logging
from datetime import datetime
from urllib.parse import quote
from playwright.async_api import async_playwright

logging.basicConfig(
    filename="scrape.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S"))
logging.getLogger("").addHandler(console)
log = logging.getLogger("scraper")

CUSTOM_HEADERS = {
    "Origin": "https://embedsports.top",
    "Referer": "https://embedsports.top/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
}

# Encode the User-Agent for TiviMate format
ENCODED_USER_AGENT = quote(CUSTOM_HEADERS["User-Agent"], safe="")

FALLBACK_LOGOS = {
    "american-football": "http://drewlive24.duckdns.org:9000/Logos/Am-Football2.png",
    "football": "https://external-content.duckduckgo.com/iu/?u=https://i.imgur.com/RvN0XSF.png",
    "fight": "http://drewlive24.duckdns.org:9000/Logos/Combat-Sports.png",
    "basketball": "http://drewlive24.duckdns.org:9000/Logos/Basketball5.png",
    "motor sports": "http://drewlive24.duckdns.org:9000/Logos/Motorsports3.png",
    "darts": "http://drewlive24.duckdns.org:9000/Logos/Darts.png",
    "tennis": "http://drewlive24.duckdns.org:9000/Logos/Tennis-2.png",
    "rugby": "http://drewlive24.duckdns.org:9000/Logos/Rugby.png"
}

TV_IDS = {
    "Baseball": "MLB.Baseball.Dummy.us",
    "Fight": "PPV.EVENTS.Dummy.us",
    "American Football": "Football.Dummy.us",
    "Afl": "AUS.Rules.Football.Dummy.us",
    "Football": "Soccer.Dummy.us",
    "Basketball": "Basketball.Dummy.us",
    "Hockey": "NHL.Hockey.Dummy.us",
    "Tennis": "Tennis.Dummy.us",
    "Darts": "Darts.Dummy.us",
    "Motor Sports": "Racing.Dummy.us",
    "Rugby": "Rugby.Dummy.us"
}

total_matches = 0
total_embeds = 0
total_streams = 0
total_failures = 0

# --- Your existing functions get_all_matches, get_embed_urls_from_api, extract_m3u8, validate_logo, build_logo_url, process_match remain unchanged --- #

# Main generate_playlist function with VLC + TiviMate
async def generate_playlist():
    global total_matches
    matches = get_all_matches()
    total_matches = len(matches)
    if not matches:
        log.warning("❌ No matches found.")
        return "#EXTM3U\n", "#EXTM3U\n"

    vlc_content = ["#EXTM3U"]
    tivimate_content = ["#EXTM3U"]
    success = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome-beta")
        ctx = await browser.new_context(extra_http_headers=CUSTOM_HEADERS)
        sem = asyncio.Semaphore(2)

        async def worker(idx, m):
            async with sem:
                return await process_match(idx, m, total_matches, ctx)

        for i, m in enumerate(matches, 1):
            match, url = await worker(i, m)
            if not url:
                continue
            logo, cat = build_logo_url(match)
            title = match.get("title", "Untitled")
            display_cat = cat.replace("-", " ").title() if cat else "General"
            tv_id = TV_IDS.get(display_cat, "General.Dummy.us")

            # VLC entry
            vlc_content.append(
                f'#EXTINF:-1 tvg-id="{tv_id}" tvg-name="{title}" '
                f'tvg-logo="{logo}" group-title="StreamedSU - {display_cat}",{title}'
            )
            vlc_content.append(f'#EXTVLCOPT:http-origin={CUSTOM_HEADERS["Origin"]}')
            vlc_content.append(f'#EXTVLCOPT:http-referrer={CUSTOM_HEADERS["Referer"]}')
            vlc_content.append(f'#EXTVLCOPT:user-agent={CUSTOM_HEADERS["User-Agent"]}')
            vlc_content.append(url)

            # TiviMate entry (pipe-delimited headers, encoded user-agent)
            tivimate_headers = f"referer={CUSTOM_HEADERS['Referer']}|origin={CUSTOM_HEADERS['Origin']}|user-agent={ENCODED_USER_AGENT}|icy-metadata=1"
            tivimate_content.append(
                f'#EXTINF:-1 tvg-id="{tv_id}" tvg-name="{title}" '
                f'tvg-logo="{logo}" group-title="StreamedSU - {display_cat}",{title}'
            )
            tivimate_content.append(f'{url}|{tivimate_headers}')

            success += 1

        await browser.close()

    log.info(f"\n🎉 {success} working streams written to playlists.")
    return "\n".join(vlc_content), "\n".join(tivimate_content)


if __name__ == "__main__":
    start = datetime.now()
    log.info("🚀 Starting StreamedSU scrape run (LIVE only)...")
    vlc_playlist, tivimate_playlist = asyncio.run(generate_playlist())
    
    with open("StreamedSU_VLC.m3u8", "w", encoding="utf-8") as f:
        f.write(vlc_playlist)

    with open("StreamedSU_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write(tivimate_playlist)

    end = datetime.now()
    duration = (end - start).total_seconds()
    log.info("\n📊 FINAL SUMMARY ------------------------------")
    log.info(f"🕓 Duration: {duration:.2f} sec")
    log.info(f"📺 Matches:  {total_matches}")
    log.info(f"🔗 Embeds:   {total_embeds}")
    log.info(f"✅ Streams:  {total_streams}")
    log.info(f"❌ Failures: {total_failures}")
    log.info("------------------------------------------------")
