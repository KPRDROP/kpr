import asyncio
import re
import requests
import logging
from datetime import datetime
from urllib.parse import quote
from playwright.async_api import async_playwright

# -------------------------------
# Logging
# -------------------------------
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

# -------------------------------
# Headers & Encoded User-Agent
# -------------------------------
CUSTOM_HEADERS = {
    "Origin": "https://embedsports.top",
    "Referer": "https://embedsports.top/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
}
ENCODED_USER_AGENT = quote(CUSTOM_HEADERS["User-Agent"], safe="")

# -------------------------------
# Logos and TV IDs
# -------------------------------
FALLBACK_LOGOS = {
    "american football": "http://drewlive24.duckdns.org:9000/Logos/Am-Football2.png",
    "football": "https://external-content.duckduckgo.com/iu/?u=https://i.imgur.com/RvN0XSF.png",
    "fight": "http://drewlive24.duckdns.org:9000/Logos/Combat-Sports.png",
    "basketball": "http://drewlive24.duckdns.org:9000/Logos/Basketball5.png",
    "motor sports": "http://drewlive24.duckdns.org:9000/Logos/Motorsports3.png",
    "darts": "http://drewlive24.duckdns.org:9000/Logos/Darts.png",
    "tennis": "http://drewlive24.duckdns.org:9000/Logos/Tennis-2.png",
    "rugby": "http://drewlive24.duckdns.org:9000/Logos/Rugby.png",
    "cricket": "http://drewlive24.duckdns.org:9000/Logos/Cricket.png",
    "golf": "http://drewlive24.duckdns.org:9000/Logos/Golf.png",
    "other": "http://drewlive24.duckdns.org:9000/Logos/DrewLiveSports.png"
}

TV_IDS = {
    "baseball": "MLB.Baseball.Dummy.us",
    "fight": "PPV.EVENTS.Dummy.us",
    "american football": "Football.Dummy.us",
    "afl": "AUS.Rules.Football.Dummy.us",
    "football": "Soccer.Dummy.us",
    "basketball": "Basketball.Dummy.us",
    "hockey": "NHL.Hockey.Dummy.us",
    "tennis": "Tennis.Dummy.us",
    "darts": "Darts.Dummy.us",
    "motor sports": "Racing.Dummy.us",
    "rugby": "Rugby.Dummy.us",
    "cricket": "Cricket.Dummy.us",
    "other": "Sports.Dummy.us"
}

# -------------------------------
# Globals
# -------------------------------
total_matches = 0
total_embeds = 0
total_streams = 0
total_failures = 0

# -------------------------------
# Fetch matches
# -------------------------------
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

# -------------------------------
# Fetch embed URLs
# -------------------------------
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

# -------------------------------
# Validate logo
# -------------------------------
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

# -------------------------------
# Extract M3U8 (Optimized Version)
# -------------------------------
async def extract_m3u8(page, embed_url):
    global total_failures
    found = None

    try:
        async def capture_stream(req_or_resp):
            nonlocal found
            url = getattr(req_or_resp, "url", lambda: None)()
            if url and ".m3u8" in url and not found:
                if "prd.jwpltx.com" in url or "blank" in url:
                    return
                found = url
                log.info(f"  ⚡ (detected) Stream: {found}")

        page.on("request", capture_stream)
        page.on("response", capture_stream)

        await page.goto(embed_url, wait_until="domcontentloaded", timeout=8000)
        log.info(f"  🌐 Loaded embed: {embed_url}")

        await page.bring_to_front()

        # Try multiple selectors
        selectors = [
            "div.jw-icon-display[role='button']",
            ".jw-icon-playback",
            ".vjs-big-play-button",
            ".plyr__control",
            "div[class*='play']",
            "div[role='button']",
            "button",
            "canvas"
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    await asyncio.sleep(1.0)
                    await el.click(timeout=300)
                    log.info(f"  🎯 Clicked selector: {sel}")
                    break
            except Exception:
                continue

        # Simulate clicks and close ads
        try:
            await page.mouse.click(200, 200)
            log.info("  👆 First click triggered ad")

            pages_before = page.context.pages
            new_tab = None
            for _ in range(12):
                pages_now = page.context.pages
                if len(pages_now) > len(pages_before):
                    new_tab = [p for p in pages_now if p not in pages_before][0]
                    break
                await asyncio.sleep(0.25)

            if new_tab:
                try:
                    await asyncio.sleep(0.5)
                    url = (new_tab.url or "").lower()
                    log.info(f"  🚫 Closing ad tab: {url if url else '(blank/new)'}")
                    await new_tab.close()
                except Exception:
                    log.info("  ⚠️ Ad tab close failed")

            await asyncio.sleep(1)
            await page.mouse.click(200, 200)
            log.info("  ▶️ Second click started player")

        except Exception as e:
            log.warning(f"⚠️ Momentum click sequence failed: {e}")

        # Wait up to 10 seconds for stream requests
        for _ in range(40):
            if found:
                break
            await asyncio.sleep(0.25)

        # Fallback search inside HTML
        if not found:
            html = await page.content()
            matches = re.findall(r'https?://[^\s"\'<>]+\.m3u8(?:\?[^"\'<>]*)?', html)
            if matches:
                found = matches[0]
                log.info(f"  🕵️ Fallback (HTML): {found}")

        return found

    except Exception as e:
        total_failures += 1
        log.warning(f"⚠️ {embed_url} failed: {e}")
        return None
e

# -------------------------------
# Process match
# -------------------------------
async def process_match(index, match, total, ctx):
    global total_embeds, total_streams, total_failures
    title = match.get("title", "Unknown Match")
    log.info(f"\n🎯 [{index}/{total}] {title}")
    sources = match.get("sources", [])
    match_embeds = 0

    page = await ctx.new_page()

    try:
        for s in sources:
            embed_urls = get_embed_urls_from_api(s)
            total_embeds += len(embed_urls)
            match_embeds += len(embed_urls)
            if not embed_urls:
                continue

            log.info(f"  ↳ {len(embed_urls)} embed URLs")
            for i, embed in enumerate(embed_urls, start=1):
                log.info(f"     • ({i}/{len(embed_urls)}) {embed}")

                try:
                    # ⚡ Timeout protection for extract_m3u8
                    m3u8 = await asyncio.wait_for(extract_m3u8(page, embed), timeout=25)
                except asyncio.TimeoutError:
                    log.warning(f"  ⏱️ Timeout on embed {embed}")
                    continue
                except Exception as e:
                    log.warning(f"  ⚠️ Error on embed {embed}: {e}")
                    continue

                if m3u8:
                    total_streams += 1
                    log.info(f"     ✅ Stream OK for {title}")
                    await page.close()
                    return match, m3u8

        log.info(f"     ❌ No working streams ({match_embeds} embeds)")

    except Exception as e:
        total_failures += 1
        log.warning(f"⚠️ Match '{title}' failed: {e}")
    finally:
        try:
            if not page.is_closed():
                await page.close()
        except Exception:
            pass

    return match, None

# -------------------------------
# Generate VLC + TiviMate playlists
# -------------------------------
async def generate_playlist():
    global total_matches
    matches = get_all_matches()
    total_matches = len(matches)
    if not matches:
        log.warning("❌ No matches found.")
        return "", ""

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
            logo, raw_cat = build_logo_url(match)
            base_cat = (raw_cat or "other").strip().replace("-", " ").lower()
            display_cat = base_cat.title()
            tv_id = TV_IDS.get(base_cat, TV_IDS["other"])
            title = match.get("title", "Untitled")

            # VLC playlist
            vlc_content.append(f'#EXTINF:-1 tvg-id="{tv_id}" tvg-name="{title}" tvg-logo="{logo}" group-title="StreamedSU - {display_cat}",{title}')
            vlc_content.append(f'#EXTVLCOPT:http-origin={CUSTOM_HEADERS["Origin"]}')
            vlc_content.append(f'#EXTVLCOPT:http-referrer={CUSTOM_HEADERS["Referer"]}')
            vlc_content.append(f'#EXTVLCOPT:user-agent={CUSTOM_HEADERS["User-Agent"]}')
            vlc_content.append(url)

            # TiviMate playlist (headers + encoded UA)
            tivimate_content.append(f'#EXTINF:-1 tvg-id="{tv_id}" tvg-name="{title}" tvg-logo="{logo}" group-title="StreamedSU - {display_cat}",{title}')
            tivimate_content.append(f'{url}|referer={CUSTOM_HEADERS["Referer"]}|origin={CUSTOM_HEADERS["Origin"]}|user-agent={ENCODED_USER_AGENT}')

            success += 1

        await browser.close()

    log.info(f"\n🎉 {success} working streams written to playlists.")
    return "\n".join(vlc_content), "\n".join(tivimate_content)

# ---------------------------
# 🚀 Main execution
# ---------------------------
if __name__ == "__main__":
    import sys
    import asyncio

    try:
        log.info("🚀 Starting StreamedSU scrape run (LIVE only)...")
        asyncio.run(main())
        log.info("✅ StreamedSU scrape completed successfully.")
    except KeyboardInterrupt:
        log.warning("🧹 Script interrupted manually. Exiting cleanly...")
        sys.exit(0)
    except Exception as e:
        log.error(f"❌ Unexpected fatal error: {e}", exc_info=True)
        sys.exit(1)
		
    end = datetime.now()
    duration = (end - start).total_seconds()
    log.info("\n📊 FINAL SUMMARY ------------------------------")
    log.info(f"🕓 Duration: {duration:.2f} sec")
    log.info(f"📺 Matches:  {total_matches}")
    log.info(f"🔗 Embeds:   {total_embeds}")
    log.info(f"✅ Streams:  {total_streams}")
    log.info(f"❌ Failures: {total_failures}")
    log.info("------------------------------------------------")
