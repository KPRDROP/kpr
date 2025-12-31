#!/usr/bin/env python3
import asyncio
import re
import sys
from urllib.parse import urljoin, quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------
BASE_URL = "https://nflwebcast.com/"
OUTPUT_VLC = "NFLWebcast_VLC.m3u8"
OUTPUT_TIVI = "NFLWebcast_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

HEADERS = {
    "referer": BASE_URL,
    "origin": BASE_URL,
    "user-agent": USER_AGENT,
}

LOGO = "https://i.postimg.cc/5t5PgRdg/1000-F-431743763-in9BVVz-CI36X304St-R89pnxy-UYzj1dwa-1.jpg"

# -------------------------------------------------------------------
# UTILIDADES
# -------------------------------------------------------------------
def log(*a):
    print(*a)
    sys.stdout.flush()


def clean_title(title: str) -> str:
    if not title:
        return "NFL Game"
    t = title.replace("@", "vs")
    t = t.replace(",", "")
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip()


# -------------------------------------------------------------------
# PASO 1: DETECTAR SUBPÁGINAS DE EVENTOS (CLAVE)
# -------------------------------------------------------------------
async def find_event_pages(playwright):
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    log("🌐 Loading NFLWebcast homepage...")
    await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

    # Esperar JS dinámico
    await page.wait_for_timeout(6000)

    # Capturar TODOS los enlaces visibles
    hrefs = await page.eval_on_selector_all(
        "a",
        """
        els => els
            .map(e => e.href)
            .filter(h =>
                h &&
                h.startsWith("https://nflwebcast.com/") &&
                h.includes("live-stream")
            )
        """
    )

    await browser.close()

    # Deduplicar
    hrefs = sorted(set(hrefs))
    return hrefs


# -------------------------------------------------------------------
# PASO 2: CAPTURAR M3U8 DESDE CADA EVENTO
# -------------------------------------------------------------------
async def capture_m3u8(playwright, url):
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()

    found_m3u8 = None

    def on_response(resp):
        nonlocal found_m3u8
        try:
            if ".m3u8" in resp.url and not found_m3u8:
                found_m3u8 = resp.url
        except Exception:
            pass

    page.on("response", on_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Intentar activar el reproductor
        for sel in ["button", ".play", ".vjs-big-play-button", "video"]:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click(force=True, timeout=1500)
                    await asyncio.sleep(1.0)
            except Exception:
                pass

        # Esperar tráfico
        for _ in range(12):
            if found_m3u8:
                break
            await asyncio.sleep(0.8)

        # Fallback: buscar en HTML
        if not found_m3u8:
            html = await page.content()
            m = re.search(
                r'https?://[^"\'<>]+\.m3u8[^"\'<>]*',
                html
            )
            if m:
                found_m3u8 = m.group(0)

    except PlaywrightTimeoutError:
        log(f"⚠️ Timeout loading event: {url}")
    finally:
        await browser.close()

    return found_m3u8


# -------------------------------------------------------------------
# PASO 3: ESCRIBIR PLAYLISTS
# -------------------------------------------------------------------
def write_playlists(entries):
    if not entries:
        log("❌ No streams to write.")
        return

    # VLC
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(
                f'#EXTINF:-1 tvg-id="NFL.Dummy.us" '
                f'tvg-name="{title}" '
                f'tvg-logo="{LOGO}" '
                f'group-title="NFL Live",{title}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={BASE_URL}\n")
            f.write(f"#EXTVLCOPT:http-origin={BASE_URL}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(url + "\n\n")

    # TiviMate
    ua_enc = quote_plus(USER_AGENT)
    with open(OUTPUT_TIVI, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for title, url in entries:
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(
                f"{url}|referer={BASE_URL}|origin={BASE_URL}|user-agent={ua_enc}\n"
            )

    log(f"✅ Playlists saved:")
    log(f"   - {OUTPUT_VLC}")
    log(f"   - {OUTPUT_TIVI}")


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
async def main():
    log("🚀 Starting NFL Webcast scraper (FINAL)...")

    async with async_playwright() as p:
        event_pages = await find_event_pages(p)

        log(f"🔍 Found {len(event_pages)} event pages")

        if not event_pages:
            log("❌ No event pages found")
            return

        results = []

        for idx, url in enumerate(event_pages, 1):
            log(f"🔎 [{idx}/{len(event_pages)}] {url}")
            m3u8 = await capture_m3u8(p, url)

            if m3u8:
                title = clean_title(
                    url.split("/")[-1]
                    .replace("-", " ")
                    .replace("live stream online free", "")
                )
                log(f"✅ Found stream: {m3u8}")
                results.append((title, m3u8))
            else:
                log("⚠️ No m3u8 found")

        if not results:
            log("❌ No streams captured")
            return

        write_playlists(results)
        log("🎉 Done.")


if __name__ == "__main__":
    asyncio.run(main())
