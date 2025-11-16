#!/usr/bin/env python3

import asyncio
from playwright.async_api import async_playwright
import aiohttp
from datetime import datetime
from urllib.parse import quote, urljoin
import platform
import re

API_URL = "https://ppv.to/api/streams"

# Custom headers for VLC/Kodi playlists
CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://ppvs.su',
    '#EXTVLCOPT:http-referrer=https://ppvs.su/',
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
]

# TiviMate encoded headers
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

ALLOWED_CATEGORIES = {
    "24/7 Streams", "Wrestling", "Football", "Basketball", "Baseball",
    "Combat Sports", "Motorsports", "Miscellaneous", "Boxing", "Darts",
    "American Football", "Ice Hockey"
}

CATEGORY_LOGOS = {
    "24/7 Streams": "https://i.postimg.cc/Nf04VJzs/24-7.png",
    "Wrestling": "https://i.postimg.cc/3JwB6ScC/wwe.png",
    "Football": "https://i.postimg.cc/mgkTPs69/football.png",
    "Basketball": "https://i.postimg.cc/DyzgDjMP/nba.png",
    "Baseball": "https://i.postimg.cc/28JKxNSR/Baseball3.png",
    "Combat Sports": "https://i.postimg.cc/B6crhYwg/Combat-Sports.png",
    "Motorsports": "https://i.postimg.cc/m2cdkpNp/f1.png",
    "Miscellaneous": "https://i.postimg.cc/Nf04VJzs/24-7.png",
    "Boxing": "https://i.postimg.cc/9FNpjP3h/boxing.png",
    "Darts": "https://i.postimg.cc/7YQVy1vq/darts.png",
    "Ice Hockey": "https://i.postimg.cc/9fx8z6Kj/hockey.png",
    "American Football": "https://i.postimg.cc/Kzw0Dnm6/nfl.png"
}

CATEGORY_TVG_IDS = {
    "24/7 Streams": "24.7.Dummy.us",
    "Football": "Soccer.Dummy.us",
    "Wrestling": "PPV.EVENTS.Dummy.us",
    "Combat Sports": "PPV.EVENTS.Dummy.us",
    "Baseball": "MLB.Baseball.Dummy.us",
    "Basketball": "Basketball.Dummy.us",
    "Motorsports": "Racing.Dummy.us",
    "Miscellaneous": "PPV.EVENTS.Dummy.us",
    "Boxing": "PPV.EVENTS.Dummy.us",
    "Ice Hockey": "NHL.Hockey.Dummy.us",
    "Darts": "Darts.Dummy.us",
    "American Football": "NFL.Dummy.us"
}

GROUP_RENAME_MAP = {
    "24/7 Streams": "PPVLand - Live Channels 24/7",
    "Wrestling": "PPVLand - Wrestling Events",
    "Football": "PPVLand - Global Football Streams",
    "Basketball": "PPVLand - Basketball Hub",
    "Baseball": "PPVLand - Baseball Action HD",
    "Combat Sports": "PPVLand - MMA & Fight Nights",
    "Motorsports": "PPVLand - Motorsport Live",
    "Miscellaneous": "PPVLand - Random Events",
    "Boxing": "PPVLand - Boxing",
    "Ice Hockey": "PPVLand - Ice Hockey",
    "Darts": "PPVLand - Darts",
    "American Football": "PPVLand - NFL Action"
}

# regex to detect .m3u8 (case-insensitive)
M3U8_RE = re.compile(r"\.m3u8(\?.*)?$", re.IGNORECASE)


async def check_m3u8_url(url):
    """
    Quickly HEAD/GET the URL to confirm it's accessible (status 200).
    Normalize protocol-relative URLs and ignore obvious bad URLs.
    """
    try:
        if not url or not isinstance(url, str):
            return False

        # strip whitespace
        url = url.strip()

        # ignore javascript: or data: etc
        if url.startswith("javascript:") or url.startswith("data:"):
            return False

        # protocol-relative
        if url.startswith("//"):
            url = "https:" + url

        # If it's a relative path, we can't check it reliably -> skip
        if url.startswith("/"):
            return False

        headers = {
            "User-Agent": USER_AGENT,
            "Referer": "https://ppvs.su",
            "Origin": "https://ppvs.su"
        }
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # attempt a HEAD first (faster), fall back to GET if HEAD not allowed
            try:
                async with session.head(url, headers=headers) as resp:
                    return resp.status == 200
            except Exception:
                async with session.get(url, headers=headers) as resp:
                    return resp.status == 200
    except Exception:
        return False


async def get_streams():
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {
            'User-Agent': USER_AGENT
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            print(f"🌐 Fetching streams from {API_URL}")
            async with session.get(API_URL) as resp:
                if resp.status != 200:
                    print(f"❌ Failed to fetch, status {resp.status}")
                    return None
                return await resp.json()
    except Exception as e:
        print(f"❌ Error fetching streams: {e}")
        return None


async def grab_m3u8_from_iframe(context, iframe_url):
    """
    Loads the iframe_url in a fresh page in the provided context, listens to
    context responses (captures nested iframes), and also scans frames' DOM
    for video/source/iframe src attributes as a fallback.
    Returns a set of validated .m3u8 URLs.
    """
    found_streams = set()

    def handle_response(response):
        try:
            url = response.url
        except Exception:
            return
        if not url:
            return
        if M3U8_RE.search(url):
            # normalize protocol-relative
            if url.startswith("//"):
                url = "https:" + url
            print("📡 Network-found .m3u8:", url)
            found_streams.add(url)

    # attach to context so we capture requests from nested frames
    context.on("response", handle_response)

    page = await context.new_page()
    try:
        print(f"🌐 Navigating to iframe: {iframe_url}")
        # use domcontentloaded (more reliable with JS-challenges), longer timeout
        await page.goto(iframe_url, wait_until="domcontentloaded", timeout=30000)
        # wait additional time for nested frames and player JS to run
        await asyncio.sleep(10)

        # scan all frames for video/source/iframe src attributes
        for frame in page.frames:
            try:
                srcs = await frame.evaluate("""() => {
                    const urls = [];
                    const selectors = ['video', 'source', 'iframe', 'script'];
                    selectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => {
                            if (el.src) urls.push(el.src);
                            if (el.currentSrc) urls.push(el.currentSrc);
                            if (el.getAttribute && el.getAttribute('src')) urls.push(el.getAttribute('src'));
                            // some players keep URLs in data-* attributes
                            Array.from(el.attributes || []).forEach(attr=>{
                                if (attr && attr.value && (attr.value.includes('.m3u8') || attr.value.includes('m3u8'))) {
                                    urls.push(attr.value);
                                }
                            });
                        });
                    });
                    // also check page HTML for m3u8 occurrences
                    if (document.documentElement && document.documentElement.innerHTML) {
                        const html = document.documentElement.innerHTML;
                        const re = /https?:[^"'\\s>]*\\.m3u8[^"'\\s>]*/gi;
                        let match;
                        while ((match = re.exec(html)) !== null) {
                            urls.push(match[0]);
                        }
                    }
                    return Array.from(new Set(urls));
                }""")
                for u in srcs:
                    if not u:
                        continue
                    if u.startswith("//"):
                        u = "https:" + u
                    if M3U8_RE.search(u):
                        print("🔍 DOM-found .m3u8:", u)
                        found_streams.add(u)
            except Exception:
                # best-effort; ignore frame evaluation failures
                pass

    except Exception as e:
        print(f"❌ Failed to load iframe/page: {e}")
    finally:
        try:
            await page.close()
        except Exception:
            pass
        # detach listener
        try:
            context.remove_listener("response", handle_response)
        except Exception:
            # older playwright versions may not have remove_listener; ignore
            pass

    # validate found streams (fast checks)
    valid_urls = set()
    for url in found_streams:
        try:
            ok = await check_m3u8_url(url)
            if ok:
                valid_urls.add(url)
        except Exception:
            continue

    return valid_urls


def write_playlists(streams, url_map):
    """Writes both VLC and TiviMate playlists."""
    if not streams:
        print("⚠️ No streams to write.")
        return

    # --- VLC/Kodi version ---
    lines_vlc = ['#EXTM3U']
    seen_names = set()
    entries_written = 0

    for s in streams:
        name = s["name"].strip()
        name_lower = name.lower()
        if name_lower in seen_names:
            continue

        urls = url_map.get(f"{s['name']}::{s['category']}::{s['iframe']}", [])
        if not urls:
            continue

        url = next(iter(urls))
        category = s["category"]
        group = GROUP_RENAME_MAP.get(category, category)
        logo = CATEGORY_LOGOS.get(category, "")
        tvg_id = CATEGORY_TVG_IDS.get(category, "Sports.Dummy.us")

        lines_vlc.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines_vlc.extend(CUSTOM_HEADERS)
        lines_vlc.append(url)

        seen_names.add(name_lower)
        entries_written += 1

    with open("PPVLand.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(lines_vlc))
    print(f"✅ Wrote VLC/Kodi playlist: PPVLand.m3u8 ({entries_written} entries)")

    # --- TiviMate version ---
    lines_tivi = ['#EXTM3U']
    seen_names.clear()
    entries_written = 0

    for s in streams:
        name = s["name"].strip()
        name_lower = name.lower()
        if name_lower in seen_names:
            continue

        urls = url_map.get(f"{s['name']}::{s['category']}::{s['iframe']}", [])
        if not urls:
            continue

        url = next(iter(urls))
        category = s["category"]
        group = GROUP_RENAME_MAP.get(category, category)
        logo = CATEGORY_LOGOS.get(category, "")
        tvg_id = CATEGORY_TVG_IDS.get(category, "Sports.Dummy.us")

        headers = f"referer=https://ppvs.su/|origin=https://ppvs.su|user-agent={ENCODED_USER_AGENT}"
        lines_tivi.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines_tivi.append(f"{url}|{headers}")

        seen_names.add(name_lower)
        entries_written += 1

    with open("PPVLand_TiviMate.m3u8", "w", encoding="utf-8") as f:
        f.write("\n".join(lines_tivi))
    print(f"✅ Wrote TiviMate playlist: PPVLand_TiviMate.m3u8 ({entries_written} entries)")


async def main():
    print("🚀 Starting PPVLand Stream Fetcher...")
    data = await get_streams()

    if not data or 'streams' not in data:
        print("❌ No valid data received.")
        return

    streams = []
    for category in data.get("streams", []):
        cat = category.get("category", "").strip()
        if cat not in ALLOWED_CATEGORIES:
            continue
        for stream in category.get("streams", []):
            iframe = stream.get("iframe")
            name = stream.get("name", "Unnamed Event")
            if iframe:
                streams.append({"name": name, "iframe": iframe, "category": cat})

    # Deduplicate by name
    seen = set()
    streams = [s for s in streams if not (s["name"].lower() in seen or seen.add(s["name"].lower()))]

    # Playwright: use Chromium (recommended)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            java_script_enabled=True,
            bypass_csp=True,
            ignore_https_errors=True,
            viewport={"width": 1280, "height": 720},
        )

        # Optionally give some extra stealth prefs (best-effort)
        try:
            # for chromium, add some online-able stealth flags via evaluate on new page
            pass
        except Exception:
            pass

        url_map = {}
        for s in streams:
            key = f"{s['name']}::{s['category']}::{s['iframe']}"
            try:
                urls = await grab_m3u8_from_iframe(context, s["iframe"])
                url_map[key] = urls
                print(f"➡ Found {len(urls)} valid .m3u8 for {s['name']}")
            except Exception as e:
                print(f"❌ Error grabbing streams for {s['name']}: {e}")
                url_map[key] = set()

        try:
            await browser.close()
        except Exception:
            pass

    write_playlists(streams, url_map)
    print(f"🎉 Done at {datetime.utcnow().isoformat()} UTC")


if __name__ == "__main__":
    asyncio.run(main())
