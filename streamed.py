import asyncio
import re
import requests
import logging
from urllib.parse import quote
from datetime import datetime
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

ENCODED_UA = quote(CUSTOM_HEADERS["User-Agent"], safe="")

FALLBACK_LOGOS = {
    "american-football": "http://drewlive24.duckdns.org:9000/Logos/Am-Football2.png",
    "football": "https://i.imgur.com/RvN0XSF.png",
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

TOTAL_MATCHES = 0
TOTAL_EMBEDS = 0
TOTAL_STREAMS = 0
TOTAL_FAILURES = 0

STREAM_PATTERN = re.compile(r"\.m3u8($|\?)", re.IGNORECASE)


# ------------------------- DATA FETCH -------------------------
def get_all_matches():
    endpoints = ["live"]
    all_matches = []
    for ep in endpoints:
        try:
            log.info(f"📡 Fetching {ep} matches...")
            res = requests.get(f"https://streami.su/api/matches/{ep}", timeout=10)
            res.raise_for_status()
            data = res.json()
            log.info(f"✅ {ep}: {len(data)} matches")
            all_matches.extend(data)
        except Exception as e:
            log.warning(f"⚠️ Failed fetching {ep}: {e}")
    log.info(f"🎯 Total matches collected: {len(all_matches)}")
    return all_matches


def get_embed_urls_from_api(source):
    try:
        s_name, s_id = source.get("source"), source.get("id")
        if not s_name or not s_id:
            return []
        res = requests.get(f"https://streamed.pk/api/stream/{s_name}/{s_id}", timeout=6)
        res.raise_for_status()
        data = res.json()
        return [d.get("embedUrl") for d in data if d.get("embedUrl")]
    except Exception:
        return []


def validate_logo(url, category):
    cat = (category or "").lower().replace("-", " ").strip()
    fallback = FALLBACK_LOGOS.get(cat)
    if url:
        try:
            res = requests.head(url, timeout=2)
            if res.status_code in (200, 302):
                return url
        except Exception:
            pass
    return fallback


def build_logo_url(match):
    cat = (match.get("category") or "").strip()
    teams = match.get("teams") or {}
    for side in ["away", "home"]:
        badge = teams.get(side, {}).get("badge")
        if badge:
            url = f"https://streamed.pk/api/images/badge/{badge}.webp"
            return validate_logo(url, cat), cat
    if match.get("poster"):
        url = f"https://streamed.pk/api/images/proxy/{match['poster']}.webp"
        return validate_logo(url, cat), cat
    return validate_logo(None, cat), cat


# ------------------------- PLAYLIST SCRAPE -------------------------
async def extract_m3u8(page, embed_url):
    global TOTAL_FAILURES
    found = None
    try:
        async def on_request(request):
            nonlocal found
            if STREAM_PATTERN.search(request.url) and not found:
                if "prd.jwpltx.com" in request.url:
                    return
                found = request.url
                log.info(f"  ⚡ Stream: {found}")

        page.on("request", on_request)
        await page.goto(embed_url, wait_until="domcontentloaded", timeout=5000)
        await page.bring_to_front()

        # CLICK ALL BUTTONS / PLAYER ELEMENTS (no fragile mouse clicks)
        button_selectors = [
            "button", "div[role='button']", "canvas",
            ".jw-icon-display", ".vjs-big-play-button", ".plyr__control"
        ]
        frames_to_check = [page] + page.frames
        for frame in frames_to_check:
            for sel in button_selectors:
                elements = await frame.query_selector_all(sel)
                for el in elements:
                    try:
                        await el.click(timeout=200)
                        await asyncio.sleep(0.2)
                    except:
                        continue

        # wait shortly for requests
        for _ in range(5):
            if found:
                break
            await asyncio.sleep(0.25)

        if not found:
            html = await page.content()
            matches = re.findall(r'https?://[^\s\"\'<>]+\.m3u8(?:\?[^\"\'<>]*)?', html)
            if matches:
                found = matches[0]
                log.info(f"  🕵️ Fallback: {found}")

        return found

    except Exception as e:
        TOTAL_FAILURES += 1
        log.warning(f"⚠️ {embed_url} failed: {e}")
        return None


async def process_match(index, match, total, ctx):
    global TOTAL_EMBEDS, TOTAL_STREAMS
    title = match.get("title", "Unknown Match")
    log.info(f"\n🎯 [{index}/{total}] {title}")
    sources = match.get("sources", [])
    match_embeds = 0

    page = await ctx.new_page()

    for s in sources:
        embed_urls = get_embed_urls_from_api(s)
        TOTAL_EMBEDS += len(embed_urls)
        match_embeds += len(embed_urls)
        if not embed_urls:
            continue

        log.info(f"  ↳ {len(embed_urls)} embed URLs")
        for i, embed in enumerate(embed_urls, start=1):
            log.info(f"     • ({i}/{len(embed_urls)}) {embed}")
            m3u8 = await extract_m3u8(page, embed)
            if m3u8:
                TOTAL_STREAMS += 1
                log.info(f"     ✅ Stream OK for {title}")
                await page.close()
                return match, m3u8

    await page.close()
    log.info(f"     ❌ No working streams ({match_embeds} embeds)")
    return match, None


async def generate_playlist():
    global TOTAL_MATCHES
    matches = get_all_matches()
    TOTAL_MATCHES = len(matches)
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
                return await process_match(idx, m, TOTAL_MATCHES, ctx)

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
            vlc_content.append(f'#EXTVLCOPT:http-user-agent={CUSTOM_HEADERS["User-Agent"]}')
            vlc_content.append(url)

            # TiviMate entry (pipe headers + encoded user-agent)
            tivimate_content.append(
                f'#EXTINF:-1 tvg-id="{tv_id}" tvg-name="{title}" '
                f'tvg-logo="{logo}" group-title="StreamedSU - {display_cat}",{title}'
            )
            tivimate_content.append(
                f'{url}|referer={CUSTOM_HEADERS["Referer"]}|origin={CUSTOM_HEADERS["Origin"]}|user-agent={ENCODED_UA}'
            )

            success += 1

        await browser.close()

    log.info(f"\n🎉 {success} working streams written to playlists.")
    return "\n".join(vlc_content), "\n".join(tivimate_content)


# ------------------------- MAIN -------------------------
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
    log.info(f"🕓 Duration:  {duration:.2f} sec")
    log.info(f"📺 Matches:   {TOTAL_MATCHES}")
    log.info(f"🔗 Embeds:    {TOTAL_EMBEDS}")
    log.info(f"✅ Streams:   {TOTAL_STREAMS}")
    log.info(f"❌ Failures:  {TOTAL_FAILURES}")
    log.info("------------------------------------------------")
