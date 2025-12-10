#!/usr/bin/env python3
"""
nba_webcast.py
Option A - pattern-based NBA scraper (user chose Option A).

Behavior:
 - Scrapes https://nbawebcast.top/ for a table of games (server-side HTML).
 - For each game row that contains a data-team key, builds a stream URL using:
       https://gg.poocloud.in/{team_key}/index.m3u8
 - Verifies each candidate stream by issuing a lightweight GET request and
   checking for a m3u8-like response (status 200 and content-type or body).
 - Produces two output playlists:
     1) nba_webcast.m3u8                (plain m3u8 entries)
     2) nba_webcast_tivimate.m3u8      (TiviMate-style entries with pipe headers)
 - All network requests use aiohttp and a configurable USER_AGENT.
 - Safe, concurrent, and verbose logging for debugging.

Requirements:
  pip install aiohttp beautifulsoup4 lxml

Run:
  python3 nba_webcast.py
"""

from __future__ import annotations
import asyncio
import aiohttp
import sys
import time
import html
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import quote

# --- Config ---
NBA_BASE_URL = "https://nbawebcast.top/"
# Option A pattern chosen by user
NBA_STREAM_URL_PATTERN = "https://gg.poocloud.in/{team_key}/index.m3u8"

# Headers for the poocloud host (kept conservative)
NBA_CUSTOM_HEADERS = {
    "Origin": "https://embednow.top",
    "Referer": "https://embednow.top/",
    # Real UA provided below via USER_AGENT constant
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

# Output files
OUT_PLAIN = "nba_webcast.m3u8"
OUT_TIVIMATE = "nba_webcast_tivimate.m3u8"

# Concurrency / timeouts
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=18)  # overall timeout
CONCURRENT_TASKS = 8

# --- Utility functions ---


def build_stream_url(team_key: str) -> str:
    """Return the pattern-based stream URL for a given team key."""
    return NBA_STREAM_URL_PATTERN.format(team_key=team_key)


async def verify_stream_url(
    session: aiohttp.ClientSession,
    url: str,
    headers: Optional[Dict[str, str]] = None,
) -> bool:
    """
    Verify the candidate m3u8 is reachable and looks like an HLS playlist.

    Strategy:
      - Do a GET (stream) request but only read a small chunk of the body (<= 64KB)
      - Consider valid if:
          * HTTP 200, and
          * 'm3u8' appears in content-type or
          * response body contains '#EXTM3U' or '.m3u8'
    """
    headers = headers or {}
    try:
        async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                # not OK
                return False
            ctype = resp.headers.get("Content-Type", "").lower()
            # Accept typical HLS content-type hints
            if "application/vnd.apple.mpegurl" in ctype or "vnd.apple.mpegurl" in ctype or "application/x-mpegurl" in ctype:
                return True
            # Read a small portion of the body and inspect
            chunk = await resp.content.read(65536)  # 64 KB
            if not chunk:
                return False
            txt = chunk.decode(errors="ignore").lower()
            if "#extm3u" in txt or ".m3u8" in txt:
                return True
            # Some domains redirect or return TS segments; still accept if URL itself contains .m3u8
            if ".m3u8" in url:
                # last resort — treat as valid if we got 200 and a non-empty body
                return True
            return False
    except asyncio.TimeoutError:
        return False
    except aiohttp.ClientError:
        return False
    except Exception:
        # tolerate unexpected exceptions as "not verified"
        return False


def make_tivimate_url(m3u8_url: str, referrer: str, origin: Optional[str], user_agent: str) -> str:
    """
    Compose a TiviMate-compatible URL with pipe headers.
    Note: user_agent must be URL-encoded when appended.
    """
    # Encode user-agent
    ua_enc = quote(user_agent, safe="")
    parts = [m3u8_url, f"referer={referrer}"]
    if origin:
        parts.append(f"origin={origin}")
    parts.append(f"user-agent=={ua_enc}")  # double-equals preserved intentionally (user requested)
    return "|".join(parts)


# --- Scraping logic ---


async def scrape_nba_page() -> List[Dict]:
    """
    Fetch NBA_BASE_URL, parse the table of games, and build candidate stream dicts.

    Expected HTML pattern:
      <table class="NBA_schedule_container"> ... <tbody> <tr> ... 
    The function extracts:
      - away / home team names from td.teamvs (two per row)
      - optional logo from td.teamlogo img (fallback to a default)
      - a watch button with data-team attribute providing the key used by the pattern
    """
    results: List[Dict] = []
    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, timeout=REQUEST_TIMEOUT) as session:
        try:
            async with session.get(NBA_BASE_URL) as resp:
                html_text = await resp.text()
        except Exception as e:
            print(f"❌ Failed to fetch NBA page {NBA_BASE_URL}: {e}")
            return []

    soup = BeautifulSoup(html_text, "lxml")

    # Attempt to find schedule table
    schedule_table = soup.find("table", class_="NBA_schedule_container")
    if not schedule_table:
        # Fallback: try searching for rows that look like games
        possible_rows = soup.find_all("tr")
        print("  ⚠️ Could not find 'NBA_schedule_container' table. Falling back to scanning <tr> elements.")
        rows = possible_rows
    else:
        tbody = schedule_table.find("tbody") or schedule_table
        rows = tbody.find_all("tr")

    print(f"  Found {len(rows)} potential game rows (scanned).")

    for row in rows:
        try:
            # Teams: look for tds with class teamvs (may be two)
            team_tds = row.find_all("td", class_="teamvs")
            if len(team_tds) >= 2:
                away_team = team_tds[0].get_text(strip=True)
                home_team = team_tds[1].get_text(strip=True)
            else:
                # fallback: maybe the row contains spans or text like "Away vs Home"
                txt = row.get_text(" ", strip=True)
                if "vs" in txt:
                    parts = txt.split("vs", 1)
                    away_team = parts[0].strip()
                    home_team = parts[1].strip().split()[0] if parts[1].strip() else ""
                else:
                    continue  # cannot parse
            # logos: prefer second logo cell if available
            logo_url = None
            logos = row.find_all("td", class_="teamlogo")
            if logos and len(logos) >= 2:
                img = logos[1].find("img")
                if img and img.get("src"):
                    logo_url = img["src"]
            if not logo_url:
                # fallback to any image in the row
                img = row.find("img")
                logo_url = img["src"] if img and img.get("src") else ""

            # watch button / data-team
            button = row.find("button", class_="watch_btn")
            team_key = None
            if button and button.has_attr("data-team"):
                team_key = button["data-team"]
            else:
                # attempt to find data-team in any element attributes
                any_el = row.find(attrs={"data-team": True})
                if any_el:
                    team_key = any_el["data-team"]

            if not team_key:
                # Try to infer team_key from team names: common slug style
                # e.g., "san-antonio-spurs" or "spurs" — user provided mapping unknown,
                # we avoid guessing too aggressively; skip if not explicit.
                continue

            title = f"{away_team} vs {home_team}"
            stream_url = build_stream_url(team_key)

            results.append(
                {
                    "name": title,
                    "url": stream_url,
                    "tvg_id": "NBA.Basketball.Dummy.us",
                    "tvg_logo": logo_url or "",
                    "group": "NBAWebcast - Live Games",
                    "ref": NBA_BASE_URL,
                    "custom_headers": {**NBA_CUSTOM_HEADERS, "User-Agent": USER_AGENT},
                }
            )
        except Exception:
            # be resilient to unexpected markup per-row
            continue

    print(f"  Built {len(results)} candidate streams from the page.")
    return results


async def verify_candidates(candidates: List[Dict]) -> List[Dict]:
    """Verify candidate stream URLs concurrently and return only verified ones."""
    verified: List[Dict] = []
    sem = asyncio.Semaphore(CONCURRENT_TASKS)

    async def worker(item: Dict):
        async with sem:
            url = item["url"]
            headers = item.get("custom_headers", {})
            # ensure a User-Agent is present
            headers.setdefault("User-Agent", USER_AGENT)
            async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT) as session:
                ok = await verify_stream_url(session, url, headers=headers)
            if ok:
                print(f"✅ Verified: {item['name']} → {url}")
                verified.append(item)
            else:
                print(f"⚠️ Skipping (no m3u8 / unreachable): {item['name']}")

    tasks = [asyncio.create_task(worker(c)) for c in candidates]
    await asyncio.gather(*tasks)
    return verified


def write_playlists(verified: List[Dict], out_plain: str = OUT_PLAIN, out_tiv: str = OUT_TIVIMATE):
    """Write two output playlists based on verified streams."""
    lines_plain: List[str] = ["#EXTM3U"]
    lines_tiv: List[str] = ["#EXTM3U"]

    for item in verified:
        name = item.get("name", "").strip()
        # sanitize name: remove additional site suffixes if present
        # e.g., remove " | MLS ..." style suffix — general approach: keep leftmost segment if ' | ' occurs
        if " | " in name:
            name = name.split(" | ", 1)[0].strip()

        url = item["url"]
        # plain entry
        lines_plain.append(f"#EXTINF:-1,{name}")
        lines_plain.append(url)

        # tivimate entry: append headers
        ref = item.get("ref") or NBA_BASE_URL
        origin = item.get("ref") or None
        ua = item.get("custom_headers", {}).get("User-Agent", USER_AGENT)
        tiv_url = make_tivimate_url(url, referrer=ref, origin=origin, user_agent=ua)
        lines_tiv.append(f"#EXTINF:-1,{name}")
        lines_tiv.append(tiv_url)

    Path(out_plain).write_text("\n".join(lines_plain) + "\n", encoding="utf-8")
    Path(out_tiv).write_text("\n".join(lines_tiv) + "\n", encoding="utf-8")
    print(f"\n✅ Playlists written: {out_plain} and {out_tiv}")


# --- Main ---


async def main():
    start = time.time()
    print("🚀 Starting NBA Webcast Scraper (Option A - pattern-based)...")

    candidates = await scrape_nba_page()
    if not candidates:
        print("❌ No candidate streams built. Exiting.")
        return 1

    print("Built candidate streams; verifying availability...")
    verified = await verify_candidates(candidates)

    if not verified:
        print("❌ No playable streams were verified.")
        return 1

    write_playlists(verified)
    elapsed = time.time() - start
    print(f"🎉 Completed in {elapsed:.1f}s — {len(verified)} stream(s) published.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Interrupted by user")
        raise
