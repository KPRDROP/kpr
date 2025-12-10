#!/usr/bin/env python3
"""
nba_webcast.py — Option B replacement (no Playwright).

- Scrapes https://nbawebcast.top/ (or similar) for schedule table rows.
- Builds stream URLs using pattern: https://gg.poocloud.in/{team_name}/index.m3u8
- Verifies availability of the m3u8 (small GET & lightweight checks).
- Writes two playlist files:
    - nba_webcast.m3u8
    - nba_webcast_tivimate.m3u8  (Tivimate headers format)
"""

import asyncio
import aiohttp
from bs4 import BeautifulSoup
from typing import List, Dict
from urllib.parse import quote
from pathlib import Path
import time

# === CONFIG ===
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

NBA_BASE_URL = "https://nbawebcast.top/"
NBA_STREAM_URL_PATTERN = "https://gg.poocloud.in/{team_name}/index.m3u8"

# Headers to try when verifying. Keep origin/referrer as requested by your earlier snippet.
NBA_CUSTOM_HEADERS = {
    "Origin": "https://embednow.top",
    "Referer": "https://embednow.top/",
    "User-Agent": USER_AGENT,
}

OUTPUT_NORMAL = "nba_webcast.m3u8"
OUTPUT_TIVIMATE = "nba_webcast_tivimate.m3u8"

# Verification params
VERIFY_TIMEOUT = 12
VERIFY_READ_BYTES = 2048  # read first chunk to check for #EXTM3U or content-type
MAX_CONCURRENT = 6


# === Helpers ===
async def verify_stream_url(session: aiohttp.ClientSession, url: str, headers: Dict[str, str]) -> bool:
    """
    Try to verify a candidate m3u8 URL. Returns True if it looks valid.
    Strategy:
      - Do a GET with a short timeout
      - Accept response.status == 200 and:
         * content-type looks like m3u8, OR
         * the first chunk contains '#EXTM3U', OR
         * final redirected URL contains '.m3u8'
    """
    try:
        async with session.get(url, headers=headers, timeout=VERIFY_TIMEOUT, allow_redirects=True) as resp:
            status = resp.status
            if status != 200:
                return False

            # If server responds with m3u8 content-type, accept
            ctype = resp.headers.get("Content-Type", "").lower()
            if "application/vnd.apple.mpegurl" in ctype or "vnd.apple.mpegurl" in ctype or "mpegurl" in ctype:
                return True

            # Try to read a small chunk and search for '#EXTM3U'
            try:
                chunk = await resp.content.read(VERIFY_READ_BYTES)
            except Exception:
                chunk = b""
            if not chunk:
                # No content (maybe server requires cookies) -> treat as invalid
                return False
            try:
                text = chunk.decode(errors="ignore")
            except Exception:
                text = ""
            if "#EXTM3U" in text or ".m3u8" in text:
                return True

            # Fallback: final URL contains .m3u8
            final_url = str(resp.url)
            if ".m3u8" in final_url:
                return True

            return False
    except Exception:
        return False


def make_tivimate_suffix(ref: str, origin: str, user_agent: str) -> str:
    """
    Build the Tivimate headers suffix, encoding the user-agent.
    Format: |referer=<ref>|origin=<origin>|user-agent==<urlencoded UA>
    Note: older examples show user-agent== (double equals). We'll match that.
    """
    ua_enc = quote(user_agent, safe="")
    # ensure trailing slash in ref/origin for compatibility; use as-is if provided
    return f"|referer={ref}|origin={origin}|user-agent=={ua_enc}"


# === Scrape / parse ===
async def scrape_nba_league(session: aiohttp.ClientSession, default_logo: str = "") -> List[Dict]:
    """
    Scrape the NBA base page for schedule rows and derive stream URLs by pattern.
    Returns list of dicts with keys:
      name, url, tvg_id, tvg_logo, group, ref, custom_headers
    """
    results = []

    try:
        async with session.get(NBA_BASE_URL, timeout=20, headers={"User-Agent": USER_AGENT}) as resp:
            resp.raise_for_status()
            html = await resp.text()
    except Exception as e:
        print(f"  ❌ Error fetching NBA page: {e}")
        return results

    soup = BeautifulSoup(html, "lxml")

    # Prefer your provided classname table if present; otherwise be permissive
    schedule_table = soup.find("table", class_="NBA_schedule_container")
    if not schedule_table:
        # Fallback: find any table with 'teamvs' cells or rows that look like games
        tables = soup.find_all("table")
        chosen = None
        for t in tables:
            if t.find("td", class_="teamvs"):
                chosen = t
                break
        schedule_table = chosen

    if not schedule_table:
        # Another fallback: some sites list games using cards; try to find 'teamvs' spans anywhere
        rows = []
        divs = soup.find_all(lambda tag: tag.name == "div" and ("teamvs" in " ".join(tag.get("class", [])) or tag.find_all("span", class_="teamvs")))
        for d in divs:
            # attempt to extract two teams
            spans = d.find_all("span", class_="teamvs")
            if len(spans) >= 2:
                rows.append(d)
        if rows:
            # convert into pseudo-rows
            print(f"  Found {len(rows)} potential NBA games via div fallback.")
            for d in rows:
                spans = d.find_all("span", class_="teamvs")
                try:
                    away = spans[0].get_text(strip=True)
                    home = spans[1].get_text(strip=True)
                    title = f"{away} vs {home}"
                    # attempt to find a button with data-team attribute nearby
                    btn = d.find(lambda tag: tag.name in ("button", "a") and tag.has_attr("data-team"))
                    team_key = btn["data-team"] if btn and btn.has_attr("data-team") else None
                    logo_img = d.find("img")
                    logo_url = logo_img["src"] if logo_img and logo_img.has_attr("src") else default_logo
                    if team_key:
                        stream_url = NBA_STREAM_URL_PATTERN.format(team_name=team_key)
                        results.append({
                            "name": title,
                            "url": stream_url,
                            "tvg_id": "NBA.Basketball.Dummy.us",
                            "tvg_logo": logo_url,
                            "group": "NBAWebcast - Live Games",
                            "ref": NBA_BASE_URL,
                            "custom_headers": NBA_CUSTOM_HEADERS,
                        })
                except Exception:
                    continue
        else:
            print("  ❌ Could not find NBA schedule table (it may be JavaScript-driven).")
            return results
    else:
        # parse rows
        tbody = schedule_table.find("tbody") or schedule_table
        rows = tbody.find_all("tr")
        print(f"  Found {len(rows)} potential NBA games (table).")
        for row in rows:
            try:
                # teams often in td.teamvs or spans
                team_cells = row.find_all("td", class_="teamvs")
                if not team_cells:
                    # fallback: find two team name tds
                    tds = row.find_all("td")
                    # try to extract plain text that looks like teams
                    if len(tds) >= 2:
                        text_cells = [td.get_text(strip=True) for td in tds]
                        # pick first two that are not empty
                        nonempty = [t for t in text_cells if t]
                        if len(nonempty) >= 2:
                            away_team, home_team = nonempty[0], nonempty[1]
                        else:
                            continue
                    else:
                        continue
                else:
                    away_team = team_cells[0].get_text(strip=True)
                    home_team = team_cells[1].get_text(strip=True)

                # try to find team logo (second team) or fallback default_logo
                logo_td = row.find("td", class_="teamlogo")
                logo_url = default_logo
                if logo_td:
                    img = logo_td.find("img")
                    if img and img.has_attr("src"):
                        logo_url = img["src"]

                # find the watch button that contains a team key
                watch_btn = row.find(lambda tag: tag.name in ("button", "a") and tag.has_attr("data-team"))
                team_key = None
                if watch_btn and watch_btn.has_attr("data-team"):
                    team_key = watch_btn["data-team"]
                else:
                    # fallback: look for data attributes in row
                    for attr in ("data-team", "data-key", "data-id"):
                        if row.has_attr(attr):
                            team_key = row[attr]
                            break

                if not team_key:
                    # nothing to build stream from — skip
                    continue

                stream_url = NBA_STREAM_URL_PATTERN.format(team_name=team_key)
                match_title = f"{away_team} vs {home_team}"

                results.append({
                    "name": match_title,
                    "url": stream_url,
                    "tvg_id": "NBA.Basketball.Dummy.us",
                    "tvg_logo": logo_url,
                    "group": "NBA Games - Live Games",
                    "ref": NBA_BASE_URL,
                    "custom_headers": NBA_CUSTOM_HEADERS,
                })
            except Exception:
                # skip broken row
                continue

    return results


# === Orchestration ===
async def main():
    print("\nScraping NBAWebcast streams (Option B - pattern-based)...")
    connector = aiohttp.TCPConnector(limit_per_host=10)
    results = []

    async with aiohttp.ClientSession(connector=connector, headers={"User-Agent": USER_AGENT}) as session:
        # scrape page and build candidate entries
        candidates = await scrape_nba_league(session)
        if not candidates:
            print("No candidates found — exiting.")
            return

        print(f"Built {len(candidates)} candidate streams; verifying availability...")

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)

        async def verify_and_collect(entry):
            async with semaphore:
                url = entry["url"]
                headers = {k: v for k, v in (entry.get("custom_headers") or {}).items()}
                # include a fallback User-Agent header if not present
                headers.setdefault("User-Agent", USER_AGENT)
                ok = await verify_stream_url(session, url, headers=headers)
                if ok:
                    print(f"✅ Verified: {entry['name']} -> {url}")
                    results.append(entry)
                else:
                    print(f"⚠️ Skipping (no m3u8 / unreachable): {entry['name']}")

        # run verifies concurrently
        await asyncio.gather(*(verify_and_collect(c) for c in candidates))

    if not results:
        print("❌ No playable streams were verified.")
        return

    # Write normal playlist
    lines = ["#EXTM3U"]
    for e in results:
        title = e["name"]
        url = e["url"]
        lines.append(f'#EXTINF:-1,{title}')
        lines.append(url)
    Path(OUTPUT_NORMAL).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✅ Wrote {OUTPUT_NORMAL} ({len(results)} entries)")

    # Write tivimate playlist (headers appended)
    tiv_lines = ["#EXTM3U"]
    for e in results:
        title = e["name"]
        url = e["url"]
        tiv_suffix = make_tivimate_suffix(ref=e.get("ref", NBA_BASE_URL), origin=e.get("ref", NBA_BASE_URL), user_agent=USER_AGENT)
        tiv_lines.append(f'#EXTINF:-1,{title}')
        tiv_lines.append(url + tiv_suffix)
    Path(OUTPUT_TIVIMATE).write_text("\n".join(tiv_lines) + "\n", encoding="utf-8")
    print(f"✅ Wrote {OUTPUT_TIVIMATE} ({len(results)} entries)")

if __name__ == "__main__":
    asyncio.run(main())
