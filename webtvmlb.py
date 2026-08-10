#!/usr/bin/env python3

import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, quote_plus, urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from selectolax.parser import HTMLParser

# ================= CONFIG =================
BASE_URL = os.environ.get("WEBTV_MLB_BASE_URL")
if not BASE_URL:
    raise RuntimeError("Missing WEBTV_MLB_BASE_URL secret")

BASE_URL = BASE_URL.rstrip("/") + "/"
REFERER = BASE_URL
ORIGIN = BASE_URL.rstrip("/")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)
UA_ENC = quote_plus(USER_AGENT)

# Keep the two existing output files.
OUT_VLC = Path("webtvmlb_vlc.m3u8")
OUT_TIVI = Path("webtvmlb_tivimate.m3u8")

CACHE_FILE = Path("webtvmlb_cache.json")
CACHE_EXP = 3 * 60 * 60

TVG_ID = "MLB.Baseball.Dummy.us"
GROUP = "Live Events"
DEFAULT_LOGO = "https://a.espncdn.com/combiner/i?img=/i/teamlogos/leagues/500/mlb.png"
TAG = "EMELB"

PAGE_TIMEOUT_MS = 45_000
PLAYER_DISCOVERY_TIMEOUT_MS = 20_000
STREAM_TIMEOUT_MS = 45_000
POLL_MS = 500

M3U8_RE = re.compile(r"https?://[^'\"\s<>]+\.m3u8(?:\?[^'\"\s<>]*)?", re.I)
CHECK_STREAM_RE = re.compile(r"(?:^|/)check_stream\.php(?:\?|$)", re.I)

# ================= HELPERS =================
def log(msg: str) -> None:
    print(msg, flush=True)


def clean_event_name(text: str) -> str:
    text = text.replace("@", " vs ")
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_url(url: str, base: str = BASE_URL) -> str:
    return urljoin(base, (url or "").strip())


def is_m3u8(url: str) -> bool:
    return bool(re.search(r"\.m3u8(?:$|[?#])", url or "", re.I))


def looks_like_player_frame(url: str) -> bool:
    if not url:
        return False
    path = urlparse(url).path.lower()
    return "/stream/" in path and path.endswith(".html")


def extract_m3u8_from_text(text: str) -> list[str]:
    found = []
    for url in M3U8_RE.findall(text or ""):
        url = url.replace("\\/", "/").replace("\\u0026", "&")
        if url not in found:
            found.append(url)
    return found


def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log(f"Cache load warning: {e}")
        return {}


def save_cache(data: dict) -> None:
    try:
        with CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"Cache save warning: {e}")


def event_key(ev: dict) -> str:
    return f"[{ev['sport']}] {ev['event']} ({TAG})"


def extract_check_stream_data(html: str):
    """Extract the current player's [id, ts, pt] token array."""
    if not html:
        return None

    # Current player source looks like:
    # var _d=[125,'1786390659','97520663e1b424c0'];
    match = re.search(
        r"(?:var|let|const)\s+[_$A-Za-z][\w$]*\s*=\s*(\[\s*"
        r"(?:\d+|'[^']*'|\"[^\"]*\")\s*,\s*"
        r"(?:\d+|'[^']*'|\"[^\"]*\")\s*,\s*"
        r"(?:'[^']*'|\"[^\"]*\")\s*\])",
        html,
        re.I,
    )
    if not match:
        return None

    values = re.findall(r"'([^']*)'|\"([^\"]*)\"|(\d+)", match.group(1))
    parts = [a or b or c for a, b, c in values]
    return tuple(parts[:3]) if len(parts) >= 3 else None


def extract_stream_url_from_json(text: str) -> str | None:
    try:
        data = json.loads(text or "")
    except Exception:
        return None
    if isinstance(data, dict):
        url = data.get("url")
        if isinstance(url, str) and is_m3u8(url):
            return url.strip()
    return None


# ================= EVENT DETECTION =================
async def get_events(page) -> list[dict]:
    """Extract team links and real game rows from the homepage."""
    log(f"Loading homepage: {BASE_URL}")

    try:
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await page.wait_for_timeout(3000)

        try:
            button = await page.query_selector("#show")
            if button:
                await button.click(timeout=3000)
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        html = await page.content()
    except PlaywrightTimeoutError:
        log("Homepage load timeout; using content available so far...")
        html = await page.content()
    except Exception as e:
        log(f"Error loading homepage: {e}")
        return []

    soup = HTMLParser(html)
    events_by_url: dict[str, dict] = {}

    # Team links.
    team_links = soup.css("li.team-logo a") or soup.css("#team-logo a")
    if not team_links:
        team_links = soup.css("a[href*='-live']")

    log(f"Found {len(team_links)} team links")

    for a in team_links:
        href = a.attributes.get("href")
        if not href:
            continue

        link = normalize_url(href)
        parsed = urlparse(link)
        if "mlbwebcast.com" not in parsed.netloc.lower():
            continue
        if not re.search(r"-live/?$", parsed.path, re.I):
            continue

        name = (a.attributes.get("title") or a.text(strip=True) or "").strip()
        name = re.sub(r"\s+Live\s+Stream.*$", "", name, flags=re.I)
        name = clean_event_name(name)
        if not name:
            name = parsed.path.strip("/").replace("-", " ")

        logo = DEFAULT_LOGO
        img = a.css_first("img")
        if img and img.attributes.get("src"):
            logo = normalize_url(img.attributes["src"])

        events_by_url[link] = {
            "sport": "MLB",
            "event": name,
            "link": link,
            "logo": logo,
        }

    # Real game rows. These are intentionally processed after team links so
    # the game title replaces the generic team title for the same URL.
    rows = soup.css("tr.singele_match_date")
    log(f"Found {len(rows)} match rows")

    game_count = 0
    for row in rows:
        if row.css_first(".mdatetitle"):
            continue

        vs_node = row.css_first("td.teamvs a")
        if not vs_node:
            continue

        href = vs_node.attributes.get("href")
        if not href:
            continue

        event_name = vs_node.text(strip=True)
        date_node = vs_node.css_first("span.mtdate")
        if date_node:
            event_name = event_name.replace(date_node.text(strip=True), "")
        event_name = clean_event_name(event_name)

        link = normalize_url(href)
        logo = DEFAULT_LOGO
        img = row.css_first("td.teamlogo img")
        if img and img.attributes.get("src"):
            logo = normalize_url(img.attributes["src"])

        events_by_url[link] = {
            "sport": "MLB",
            "event": event_name,
            "link": link,
            "logo": logo,
            "is_match": True,
        }
        game_count += 1

    log(f"Found {game_count} game rows")
    log(f"Total unique team/event URLs: {len(events_by_url)}")
    return list(events_by_url.values())


# ================= PLAYER / STREAM CAPTURE =================
async def wait_for_player_frame(page, timeout_ms: int):
    """Find the actual /stream/*.html iframe, not a generic iframe."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        frames = [f for f in page.frames if looks_like_player_frame(f.url)]
        if frames:
            # Prefer the team-specific player over the generic mlbhd player.
            specific = [f for f in frames if "/stream/mlbhd.html" not in f.url.lower()]
            return specific[0] if specific else frames[0]
        await page.wait_for_timeout(POLL_MS)
    return None


async def resolve_check_stream(page, player_frame, player_html: str) -> str | None:
    """Call the same check_stream.php endpoint used by the player."""
    token = extract_check_stream_data(player_html)
    if not token:
        log("  No player [id,ts,pt] token found.")
        return None

    ev_id, ev_ts, ev_pt = token
    player_url = player_frame.url
    check_url = urljoin(player_url, "check_stream.php")

    log(f"  Player token found: id={ev_id}, ts={ev_ts}")
    log(f"  Resolving player API: {check_url}")

    try:
        response = await page.request.get(
            check_url,
            params={"id": ev_id, "ts": ev_ts, "pt": ev_pt},
            headers={
                "Referer": player_url,
                "Origin": ORIGIN,
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
            },
            timeout=20_000,
        )
        if not response.ok:
            log(f"  check_stream.php HTTP {response.status}")
            return None

        body = await response.text()
        stream = extract_stream_url_from_json(body)
        if stream:
            log(f"  ✓ check_stream returned HLS: {stream[:160]}...")
            return stream

        try:
            data = json.loads(body)
            if isinstance(data, dict) and data.get("error"):
                log(f"  check_stream error: {data.get('error')}")
            else:
                log("  check_stream response contains no HLS URL.")
        except Exception:
            log("  check_stream response was not valid JSON.")
    except Exception as e:
        log(f"  check_stream request failed: {e}")

    return None


async def capture_m3u8_from_team(page, team_url: str, index: int) -> str | None:
    """
    Open the team URL and capture its actual player stream.

    Important: the old implementation created separate Playwright sessions for
    the team page, iframe page and check_stream.php. That loses the player
    session/cookies and also makes it easy to inspect the wrong iframe.
    Everything below stays inside ONE browser context/page.
    """
    candidates: list[str] = []

    def remember(url: str, source: str) -> None:
        if url and is_m3u8(url) and url not in candidates:
            candidates.append(url)
            log(f"  ✓ M3U8 candidate ({source}): {url[:180]}...")

    async def on_response(response) -> None:
        try:
            url = response.url
            if is_m3u8(url):
                remember(url, "network")
                return

            if CHECK_STREAM_RE.search(url):
                try:
                    body = await response.text()
                    stream = extract_stream_url_from_json(body)
                    if stream:
                        remember(stream, "check_stream.php")
                except Exception:
                    pass
        except Exception:
            pass

    page.on("response", on_response)

    try:
        log(f"  Opening team page: {team_url}")
        try:
            await page.goto(team_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            log("  Team page navigation timed out; continuing...")

        await page.wait_for_timeout(2000)

        player_frame = await wait_for_player_frame(page, PLAYER_DISCOVERY_TIMEOUT_MS)
        if player_frame is None:
            log("  ✗ Player frame not discovered.")
            return None

        log(f"  ✓ Player frame found: {player_frame.url}")

        # Give the iframe document a moment to finish its own JavaScript.
        await page.wait_for_timeout(500)

        try:
            player_html = await player_frame.content()
        except Exception as e:
            log(f"  Could not read player frame: {e}")
            player_html = ""

        log(f"  Player HTML size: {len(player_html)} bytes")

        # Deterministic path: parse the exact [id,ts,pt] values and call the
        # exact relative check_stream.php endpoint used by the player source.
        stream = await resolve_check_stream(page, player_frame, player_html)
        if stream:
            return stream

        if candidates:
            return candidates[0]

        log(f"  Waiting up to {STREAM_TIMEOUT_MS // 1000}s for HLS stream...")
        deadline = time.monotonic() + STREAM_TIMEOUT_MS / 1000
        last_log = -10

        while time.monotonic() < deadline:
            if candidates:
                return candidates[0]

            # Player frame can reload and receive a fresh token. Re-discover it.
            current_frame = await wait_for_player_frame(page, 1000)
            if current_frame:
                player_frame = current_frame
                try:
                    current_html = await player_frame.content()
                    for url in extract_m3u8_from_text(current_html):
                        remember(url, "player HTML")
                    if candidates:
                        return candidates[0]

                    stream = await resolve_check_stream(page, player_frame, current_html)
                    if stream:
                        return stream
                except Exception:
                    pass

            elapsed = int((STREAM_TIMEOUT_MS / 1000) - max(0, deadline - time.monotonic()))
            if elapsed >= last_log + 10:
                last_log = elapsed
                log(f"  Still waiting... {elapsed}/{STREAM_TIMEOUT_MS // 1000}s")

            await page.wait_for_timeout(POLL_MS)

        return candidates[0] if candidates else None

    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass


# ================= WRITE OUTPUT =================
def write_outputs(entries: list[dict]) -> None:
    if not entries:
        log("No URLs to write")
        return

    with OUT_VLC.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, e in enumerate(entries, 1):
            safe_name = e["name"].replace(",", "").strip()
            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{TVG_ID}" '
                f'tvg-name="{safe_name}" tvg-logo="{e.get("logo", DEFAULT_LOGO)}" '
                f'group-title="{GROUP}",{safe_name}\n'
            )
            f.write(f"#EXTVLCOPT:http-referrer={REFERER}\n")
            f.write(f"#EXTVLCOPT:http-origin={ORIGIN}\n")
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(f"{e['url']}\n\n")

    with OUT_TIVI.open("w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, e in enumerate(entries, 1):
            safe_name = e["name"].replace(",", "").strip()
            f.write(
                f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{TVG_ID}" '
                f'tvg-name="{safe_name}" tvg-logo="{e.get("logo", DEFAULT_LOGO)}" '
                f'group-title="{GROUP}",{safe_name}\n'
            )
            f.write(
                f"{e['url']}|referer={REFERER}|origin={ORIGIN}|user-agent={UA_ENC}\n\n"
            )

    log(f"Playlists generated: {OUT_VLC} / {OUT_TIVI}")


# ================= MAIN =================
async def main() -> None:
    log("Starting MLB Webcast Updater...")
    cache = load_cache()
    now = int(time.time())

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
            extra_http_headers={
                "Referer": REFERER,
                "Origin": ORIGIN,
            },
        )

        homepage = await context.new_page()
        try:
            events = await get_events(homepage)
        finally:
            await homepage.close()

        log(f"Found {len(events)} total events")
        if not events:
            log("No events found - please check if website structure changed")
            await context.close()
            await browser.close()
            return

        log("")
        log("Discovered team/event URLs:")
        for i, ev in enumerate(events, 1):
            log(f"  {i:02d}. {ev['event']} -> {ev['link']}")

        collected: list[dict] = []

        for i, ev in enumerate(events, 1):
            key = event_key(ev)
            log("")
            log("=" * 70)
            log(f"PROCESSING: {ev['event']}")
            log(f"URL: {ev['link']}")

            # Preserve the existing cache functionality, but never reuse an
            # entry that is not an HLS URL. Fresh captures replace stale cache.
            cached = cache.get(key)
            if (
                isinstance(cached, dict)
                and isinstance(cached.get("data"), dict)
                and isinstance(cached["data"].get("url"), str)
                and is_m3u8(cached["data"]["url"])
                and now - int(cached.get("ts", 0)) < CACHE_EXP
            ):
                log("  ✓ Using cached HLS stream")
                collected.append(cached["data"])
                continue

            event_page = await context.new_page()
            try:
                stream = await capture_m3u8_from_team(event_page, ev["link"], i)
            except Exception as e:
                log(f"  ✗ Capture error: {e}")
                stream = None
            finally:
                await event_page.close()

            if stream:
                log("  ✓ STREAM CAPTURED")
                entry = {
                    "name": f"[MLB] {ev['event']}",
                    "url": stream,
                    "logo": ev.get("logo", DEFAULT_LOGO),
                }
                cache[key] = {"ts": now, "data": entry}
                collected.append(entry)
            else:
                log(f"  ✗ NO STREAM: {ev['event']}")

        await context.close()
        await browser.close()

    save_cache(cache)
    log("")
    log(f"Captured {len(collected)}/{len(events)} streams")

    # Keep the old behavior of not destroying good playlist files when a run
    # temporarily gets zero streams.
    if collected:
        write_outputs(collected)
    else:
        log("No streams captured. Existing playlists were not replaced.")


if __name__ == "__main__":
    asyncio.run(main())
