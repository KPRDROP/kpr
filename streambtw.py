#!/usr/bin/env python3
import asyncio
import re
import base64
from pathlib import Path
from urllib.parse import quote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

HOMEPAGE = "https://streambtw.com/"
OUTPUT_VLC = "Streambtw_VLC.m3u8"
OUTPUT_TIVIMATE = "Streambtw_TiviMate.m3u8"

TIMEOUT = 25000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)

# --------------------------------------------------
# Utilities
# --------------------------------------------------

def is_m3u8(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return ".m3u8" in u or "/playlist/" in u


def normalize_href(href: str) -> str:
    if not href:
        return ""
    href = href.strip().replace("streambtw.com", "streambtw.live")
    if href.startswith("/"):
        return "https://streambtw.live" + href
    if not href.startswith("http"):
        return "https://streambtw.live/" + href
    return href


# --------------------------------------------------
# Fetch events from homepage
# --------------------------------------------------

async def fetch_event_links():
    events = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        try:
            await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=TIMEOUT)
        except PlaywrightTimeoutError:
            print("⚠️ Homepage load timeout")
        except Exception as e:
            print("❌ Homepage error:", e)
            await browser.close()
            return events

        match_divs = await page.locator(".schedule .match").all()
        for div in match_divs:
            try:
                title_el = div.locator(".match-title")
                link_el = div.locator("a.watch-btn")

                if await title_el.count() == 0 or await link_el.count() == 0:
                    continue

                title = (await title_el.inner_text()).strip()
                href = await link_el.get_attribute("href")
                href = normalize_href(href)

                if title and href:
                    events.append({"title": title, "url": href})
            except Exception:
                continue

        await browser.close()

    return events


# --------------------------------------------------
# Stream extraction (PLAY + NETWORK SNIFF)
# --------------------------------------------------

async def extract_m3u8_from_event(page, url):
    streams = set()

    async def on_request(request):
        if is_m3u8(request.url):
            streams.add(request.url)

    page.on("request", on_request)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        page.off("request", on_request)
        return []

    # allow iframe/js bootstrap
    await page.wait_for_timeout(3000)

    # iterate frames + click play
    for frame in page.frames:
        try:
            for selector in (
                "button",
                ".play",
                ".vjs-big-play-button",
                "#play",
                "[aria-label='Play']",
                "div"
            ):
                buttons = await frame.locator(selector).all()
                for btn in buttons[:2]:
                    try:
                        await btn.click(force=True, timeout=1500)
                        await page.wait_for_timeout(4000)
                    except Exception:
                        pass
        except Exception:
            pass

    # final capture window
    await page.wait_for_timeout(6000)

    page.off("request", on_request)
    return list(streams)


# --------------------------------------------------
# Main
# --------------------------------------------------

async def main():
    events = await fetch_event_links()
    if not events:
        print("❌ No event links found.")
        return

    print(f"📌 Found {len(events)} events")

    collected = []  # (title, m3u8)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()

        for idx, event in enumerate(events, 1):
            print(f"🔎 [{idx}/{len(events)}] {event['title']}")

            streams = await extract_m3u8_from_event(page, event["url"])

            if streams:
                for s in streams:
                    print(f"  ✅ STREAM FOUND: {s}")
                    collected.append((event["title"], s))
            else:
                print(f"  ⚠️ No streams found")

        await browser.close()

    if not collected:
        print("❌ No streams captured.")
        return

    # --------------------------------------------------
    # Write VLC playlist
    # --------------------------------------------------

    vlc = ["#EXTM3U"]
    for title, url in collected:
        vlc.append(f"#EXTINF:-1,{title}")
        vlc.append(url)

    Path(OUTPUT_VLC).write_text("\n".join(vlc), encoding="utf-8")
    print(f"✅ Saved VLC playlist: {OUTPUT_VLC}")

    # --------------------------------------------------
    # Write TiviMate playlist (encoded UA)
    # --------------------------------------------------

    ua = quote(USER_AGENT)
    tm = ["#EXTM3U"]
    for title, url in collected:
        tm_url = (
            f"{url}"
            f"|referer=https://streambtw.live/"
            f"|origin=https://streambtw.live"
            f"|user-agent={ua}"
        )
        tm.append(f"#EXTINF:-1,{title}")
        tm.append(tm_url)

    Path(OUTPUT_TIVIMATE).write_text("\n".join(tm), encoding="utf-8")
    print(f"✅ Saved TiviMate playlist: {OUTPUT_TIVIMATE}")


if __name__ == "__main__":
    asyncio.run(main())
