#!/usr/bin/env python3
"""
nba_webcast.py
Patched and complete script to scrape nbawebcast.top schedule table,
construct stream URLs using a pattern, verify playable m3u8s and export
two playlists: normal M3U and TiviMate-style M3U (pipe headers).

Usage: python nba_webcast.py
"""

import asyncio
import aiohttp
import sys
import time
import urllib.parse
from typing import List, Dict
from bs4 import BeautifulSoup
from datetime import datetime

# --- Configuration ---
NBA_BASE_URL = "https://nbawebcast.top/"
NBA_STREAM_URL_PATTERN = "https://gg.poocloud.in/{team_name}/index.m3u8"
# custom headers for verifying streams (server expects these origins/referrers)
NBA_CUSTOM_HEADERS = {
    "Origin": "https://embednow.top",
    "Referer": "https://embednow.top/",
}
# Default UA used for requests (also encoded for TiviMate header)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

# Output filenames
OUT_M3U = "nba_webcast.m3u8"
OUT_TIVIMATE = "nba_webcast_TiviMate.m3u8"

# Timeouts
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def verify_stream_url(session: aiohttp.ClientSession, url: str, extra_headers: Dict[str, str] = None) -> bool:
    """
    Verify that the given URL serves a valid m3u8 playlist.
    Strategy:
      - Send a GET with small timeout and check for status 200.
      - Read a small chunk of the response (first 1024 bytes) and check for '#EXTM3U' or '.m3u8' markers.
    Returns True if verified playable.
    """
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        # merge, but preserve primary User-Agent
        for k, v in extra_headers.items():
            if k.lower() != "user-agent":
                headers[k] = v
    try:
        async with session.get(url, headers=headers, timeout=HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                return False
            # read a small portion to check for playlist signature
            chunk = await resp.content.read(1024)
            if not chunk:
                return False
            text = chunk.decode(errors="ignore")
            if "#EXTM3U" in text or ".m3u8" in text or "EXT-X-STREAM-INF" in text:
                return True
            # Some servers respond with redirects or HTML; reject those
            return False
    except Exception:
        return False


async def scrape_nba_league(default_logo: str = "") -> List[Dict]:
    """
    Scrape NBA schedule table from NBA_BASE_URL, build candidate stream entries
    using the NBA_STREAM_URL_PATTERN and verify availability.

    Returns list of dicts with keys:
      name, url, tvg_id, tvg_logo, group, ref, custom_headers
    """
    print(f"Scraping NBAWebcast streams from {NBA_BASE_URL}...")
    results: List[Dict] = []

    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, timeout=HTTP_TIMEOUT) as session:
        try:
            async with session.get(NBA_BASE_URL) as resp:
                if resp.status != 200:
                    print(f"  ❌ Failed to fetch page: HTTP {resp.status}")
                    return []
                html = await resp.text()
        except Exception as e:
            print(f"  ❌ Error fetching NBA page: {e}")
            return []

        soup = BeautifulSoup(html, "lxml")
        schedule_table = soup.find("table", class_="NBA_schedule_container")
        if not schedule_table:
            print("  ❌ Could not find NBA schedule table (it may be loaded by JavaScript).")
            return []

        tbody = schedule_table.find("tbody")
        if not tbody:
            print("  ❌ Schedule table has no tbody.")
            return []

        game_rows = tbody.find_all("tr")
        print(f"  Found {len(game_rows)} potential NBA games (table).")

        # we'll collect candidates first, then verify in parallel
        candidates: List[Dict] = []
        for row in game_rows:
            try:
                # teams are in <td class="teamvs"><span>Team Name</span></td>
                team_cells = row.find_all("td", class_="teamvs")
                if len(team_cells) < 2:
                    continue
                away = team_cells[0].get_text(strip=True)
                home = team_cells[1].get_text(strip=True)
                # logo typically in teamlogo td second/first; prefer home logo
                logos = row.find_all("td", class_="teamlogo")
                logo_url = default_logo
                if logos:
                    # pick second logo (home) if available, else first
                    try:
                        img = logos[1].find("img")
                    except Exception:
                        img = logos[0].find("img") if logos else None
                    if img and img.has_attr("src"):
                        logo_url = img["src"]
                        if logo_url.startswith("//"):
                            logo_url = "https:" + logo_url
                        if logo_url.startswith("/"):
                            logo_url = NBA_BASE_URL.rstrip("/") + logo_url

                # watch button contains data-team attribute
                watch_btn = row.find("button", class_="watch_btn")
                if not watch_btn:
                    # some variations may use <a class="watch_btn" data-team="...">
                    watch_btn = row.find(attrs={"class": "watch_btn"})
                if not watch_btn:
                    # skip if no key
                    continue
                team_key = watch_btn.get("data-team") or watch_btn.get("data-team-id") or watch_btn.get("data-value")
                if not team_key:
                    # fallback: try reading inner text for possible key-like value (rare)
                    continue

                match_title = f"{away} vs {home}"
                stream_url = NBA_STREAM_URL_PATTERN.format(team_name=team_key)

                candidates.append({
                    "name": match_title,
                    "team_key": team_key,
                    "url": stream_url,
                    "tvg_id": "NBA.Basketball.Dummy.us",
                    "tvg_logo": logo_url,
                    "group": "NBAWebcast - Live Games",
                    "ref": NBA_BASE_URL,
                    "custom_headers": NBA_CUSTOM_HEADERS,
                })
            except Exception as e:
                # keep going
                print(f"  ⚠️ Could not parse an NBA game row, skipping. Error: {e}")
                continue

        print(f"Built {len(candidates)} candidate streams; verifying availability...")

        # verify candidates concurrently (bounded concurrency)
        sem = asyncio.Semaphore(8)

        async def verify_item(item):
            async with sem:
                ok = await verify_stream_url(session, item["url"], extra_headers=item.get("custom_headers"))
                if ok:
                    results.append(item)
                else:
                    print(f"⚠️ Skipping (no m3u8 / unreachable): {item['name']}")

        await asyncio.gather(*(verify_item(c) for c in candidates))

    return results


def build_m3u(streams: List[Dict]) -> str:
    """Build standard M3U (VLC-style) playlist."""
    lines = ['#EXTM3U']
    for s in streams:
        name = s.get("name", "Unnamed")
        logo = s.get("tvg_logo", "")
        tvg_id = s.get("tvg_id", "")
        group = s.get("group", "")
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines.append(s["url"])
    return "\n".join(lines)


def build_m3u_tivimate(streams: List[Dict]) -> str:
    """
    Build TiviMate-compatible playlist: append pipe headers to each URL:
      |referer=<ref>|origin=<origin>|user-agent=<url-encoded-ua>
    """
    lines = ['#EXTM3U']
    encoded_ua = urllib.parse.quote(USER_AGENT, safe="")
    for s in streams:
        name = s.get("name", "Unnamed")
        logo = s.get("tvg_logo", "")
        tvg_id = s.get("tvg_id", "")
        group = s.get("group", "")
        ref = s.get("ref", "") or NBA_BASE_URL
        # derive origin from ref
        origin = ""
        try:
            origin = "https://" + urllib.parse.urlparse(ref).netloc
        except Exception:
            origin = "https://nbawebcast.top"
        # build url with pipe headers
        url_with_headers = (
            f'{s["url"]}'
            f'|referer={ref}'
            f'|origin={origin}'
            f'|user-agent={encoded_ua}'
        )
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}')
        lines.append(url_with_headers)
    return "\n".join(lines)


async def main():
    start = time.time()
    print("🚀 Starting NBA Webcast Scraper...")
    streams = await scrape_nba_league(default_logo="")

    if not streams:
        print("❌ No playable streams were verified.")
        sys.exit(1)

    # Write normal playlist
    print(f"💾 Writing {OUT_M3U} ...")
    m3u_text = build_m3u(streams)
    with open(OUT_M3U, "w", encoding="utf-8") as f:
        f.write(m3u_text)
    print(f"✅ Saved {OUT_M3U}")

    # Write TiviMate playlist
    print(f"💾 Writing {OUT_TIVIMATE} ...")
    tivi_text = build_m3u_tivimate(streams)
    with open(OUT_TIVIMATE, "w", encoding="utf-8") as f:
        f.write(tivi_text)
    print(f"✅ Saved {OUT_TIVIMATE}")

    elapsed = time.time() - start
    print(f"🎯 Done — {len(streams)} stream(s) saved. Elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted")
        raise
