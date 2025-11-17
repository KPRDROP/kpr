import asyncio
from urllib.parse import quote, urlparse, urlunparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
import aiohttp
import requests
from collections import defaultdict, Counter
import re
import time

# ------------------------
# Configuration
# ------------------------
SCHEDULE_URL = "https://sportsonline.sn/prog.txt"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
ENCODED_USER_AGENT = quote(USER_AGENT, safe="")

VLC_HEADERS = [
    f'#EXTVLCOPT:http-user-agent={USER_AGENT}',
    '#EXTVLCOPT:http-referrer=https://sportsonline.sn/'
]

CHANNEL_LOGOS = {
    # add your known logos here
}

CATEGORY_KEYWORDS = {
    "NBA": "Basketball",
    "UFC": "Combat Sports",
    "Football": "Football",
    "Soccer": "Football",
    "x": "Football",
}

NAV_TIMEOUT = 60000  # ms
CONCURRENT_FETCHES = 8
RETRIES = 3
CLICK_WAIT = 4  # seconds after clicking to wait for requests
VALIDATE_TIMEOUT = 10  # seconds for aiohttp validation
MIN_TOKEN_PARAMS = ("?s=", "&e=")

# ------------------------
# Helpers
# ------------------------

def has_tokenized_query(url: str) -> bool:
    return ("?s=" in url) and ("&e=" in url or "&exp=" in url)


def hostname_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def replace_hostname(original_url: str, new_hostname: str) -> str:
    try:
        p = urlparse(original_url)
        new_netloc = new_hostname
        if p.port:
            new_netloc = f"{new_hostname}:{p.port}"
        new_p = p._replace(netloc=new_netloc)
        return urlunparse(new_p)
    except Exception:
        return original_url


async def http_check(url: str, session: aiohttp.ClientSession, timeout: int = VALIDATE_TIMEOUT) -> bool:
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT, "Referer": "https://sportsonline.sn/"}, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False

# ------------------------
# Schedule parsing
# ------------------------

def fetch_schedule():
    print(f"🌐 Fetching schedule from {SCHEDULE_URL}")
    r = requests.get(SCHEDULE_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
    r.raise_for_status()
    return r.text


def parse_schedule(raw):
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # tolerate multiple whitespace separators
        # expected format: "00:00   Title | http..."
        try:
            time_part, rest = line.split("   ", 1)
            if " | " in rest:
                title, link = rest.rsplit(" | ", 1)
            else:
                parts = rest.rsplit(" ", 1)
                title, link = parts[0], parts[-1]
            title = title.strip()
            link = link.strip()
            category = "Miscellaneous"
            for keyword, cat in CATEGORY_KEYWORDS.items():
                if keyword.lower() in title.lower():
                    category = cat
                    break
            events.append({"time": time_part, "title": title, "link": link, "category": category})
        except ValueError:
            continue
    print(f"📺 Parsed {len(events)} events")
    return events

# ------------------------
# Core extraction logic
# ------------------------

async def extract_from_clappr(page):
    # Try to read Clappr player sources or container player instances
    try:
        # Many embeds attach Clappr instances to window or configure via data attributes.
        sources = await page.evaluate("() => {
  try {
    const out = [];
    if (window.Clappr && window.Clappr._players) {
      for (const p of Object.values(window.Clappr._players)) {
        if (p && p.core && p.core.activePlayback) {
          const src = p.core.activePlayback.options && p.core.activePlayback.options.src;
          if (src) out.push(src);
        }
      }
    }
    // some players store in window.player or jwplayer
    if (window.player && window.player.play && window.player.getPlaylist) {
      try {
        const p = window.player.getPlaylist();
        if (p && p.length) out.push(p[0].file || p[0].sources && p[0].sources[0].file);
      } catch(e){}
    }
    return out;
  } catch(e){ return []; }
}")
        if sources and isinstance(sources, list):
            # normalize and return only strings
            return [s for s in sources if isinstance(s, str) and s]
    except Exception:
        return []

# ------------------------
# Fetch m3u8 with momentum clicks and advanced prioritization
# ------------------------

async def fetch_m3u8_from_php(page, php_url):
    found_m3u8 = set()
    found_ts = []  # keep ordered list of ts hosts seen

    def on_response(response):
        try:
            url = response.url
            if url and ".m3u8" in url:
                found_m3u8.add(url)
            if url and url.endswith('.ts'):
                found_ts.append(url)
        except Exception:
            pass

    page.on('response', on_response)

    for attempt in range(1, RETRIES + 1):
        try:
            print(f"⏳ Loading PHP page (attempt {attempt}): {php_url}")
            # use 'load' because many embeds keep connections open
            await page.goto(php_url, timeout=NAV_TIMEOUT, wait_until='load')

            # Momentum click sequence with ad-tab handling
            try:
                await page.mouse.click(200, 200)
                print("  👆 First click (may open ads)")

                pages_before = list(page.context.pages)
                new_tab = None
                for _ in range(12):
                    pages_now = list(page.context.pages)
                    if len(pages_now) > len(pages_before):
                        new_tab = [p for p in pages_now if p not in pages_before][0]
                        break
                    await asyncio.sleep(0.25)

                if new_tab:
                    try:
                        await asyncio.sleep(0.5)
                        new_url = new_tab.url or ''
                        print(f"  🚫 Closing ad tab: {new_url}")
                        await new_tab.close()
                    except Exception:
                        print("  ⚠️ Failed to close ad tab")

                await asyncio.sleep(1)
                await page.mouse.click(200, 200)
                print("  ▶️ Second click triggered player")
            except Exception as e:
                print(f"  ⚠️ Momentum clicks failed: {e}")

            # also attempt to trigger play buttons if present
            try:
                await page.click("button[class*=play], .vjs-big-play-button, .jw-display-icon-display", timeout=2000)
            except Exception:
                pass

            # If Clappr exists, try to pull sources directly
            clappr_sources = []
            try:
                clappr_sources = await extract_from_clappr(page)
                if clappr_sources:
                    for s in clappr_sources:
                        if ".m3u8" in s:
                            found_m3u8.add(s)
            except Exception:
                pass

            # wait briefly for network requests to populate
            await asyncio.sleep(CLICK_WAIT)

            # if we already collected valid ts hosts and m3u8s, break
            if found_m3u8 or found_ts:
                break

        except PlaywrightTimeout:
            print(f"⚠️ Timeout loading {php_url} (attempt {attempt})")
            await asyncio.sleep(1 + attempt)
            continue
        except Exception as e:
            print(f"⚠️ Error loading {php_url} (attempt {attempt}): {e}")
            await asyncio.sleep(1 + attempt)
            continue

    # stop listening
    page.remove_listener('response', on_response)

    # Prioritization logic
    # 1) prefer m3u8 that have token (s= and e=)
    tokenized = [u for u in found_m3u8 if has_tokenized_query(u)]
    if tokenized:
        candidates = tokenized
    else:
        candidates = list(found_m3u8)

    # 2) if ts host observed, prefer m3u8 on same host (ts-domain anchoring)
    ts_hosts = [hostname_of(u) for u in found_ts if hostname_of(u)]
    host_counts = Counter(ts_hosts)
    preferred_host = None
    if host_counts:
        preferred_host = host_counts.most_common(1)[0][0]

    # normalize candidates: prefer https, with token, longer token etc.
    def score_url(u):
        score = 0
        if u.startswith('https://'):
            score += 10
        if has_tokenized_query(u):
            score += 20
            # longer token param adds marginal score
            score += len(u.split('?')[1]) // 10
        if preferred_host and hostname_of(u) == preferred_host:
            score += 50  # strong preference for ts domain
        return score

    scored = sorted(candidates, key=lambda x: score_url(x), reverse=True)

    # Validation: test first N candidates via HTTP GET
    async with aiohttp.ClientSession() as session:
        for u in scored:
            try:
                ok = await http_check(u, session)
                print(f"🔹 Validated {u}: {ok}")
                if ok:
                    # double-check that segments come from same host as m3u8 (optional)
                    return u
            except Exception:
                continue

    # fallback: try any m3u8 observed (non-tokenized)
    async with aiohttp.ClientSession() as session:
        for u in found_m3u8:
            try:
                ok = await http_check(u, session)
                if ok:
                    print(f"🔸 Fallback valid {u}")
                    return u
            except Exception:
                continue

    return None

# ------------------------
# Main routine
# ------------------------

async def main():
    raw = fetch_schedule()
    events = parse_schedule(raw)
    categorized = defaultdict(list)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        semaphore = asyncio.Semaphore(CONCURRENT_FETCHES)

        async def worker(event):
            async with semaphore:
                page = await context.new_page()
                url = None
                for attempt in range(1, RETRIES + 1):
                    url = await fetch_m3u8_from_php(page, event['link'])
                    if url:
                        print(f"✅ Got m3u8 for {event['title']}: {url}")
                        break
                    else:
                        print(f"⚠️ No m3u8 for {event['title']} on attempt {attempt}")
                        await asyncio.sleep(1 + attempt)
                await page.close()
                if url:
                    categorized[event['category']].append({
                        'title': event['title'],
                        'url': url,
                        'logo': CHANNEL_LOGOS.get(event['title'], '')
                    })

        tasks = [worker(e) for e in events]
        await asyncio.gather(*tasks)
        await browser.close()

    # write playlists by category
    for category, items in categorized.items():
        safe = category.replace(' ', '_').lower()
        vlc = f"sportsonline_{safe}.m3u"
        tiv = f"sportsonline_{safe}_tivimate.m3u"

        with open(vlc, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U
')
            for it in items:
                f.write(f'#EXTINF:-1 tvg-logo="{it['logo']}" group-title="{category}",{it['title']}
')
                for h in VLC_HEADERS:
                    f.write(h + '
')
                f.write(it['url'] + '

')

        with open(tiv, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U
')
            for it in items:
                headers = f"referer=https://sportsonline.sn/|origin=https://sportsonline.sn|user-agent={ENCODED_USER_AGENT}"
                f.write(f'#EXTINF:-1 tvg-logo="{it['logo']}" group-title="{category}",{it['title']}
')
                f.write(it['url'] + '|' + headers + '

')

        print(f"✅ Wrote playlists: {vlc}, {tiv}")

if __name__ == '__main__':
    asyncio.run(main())
