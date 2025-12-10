#!/usr/bin/env python3
"""
nba_webcast.py - patched to probe boxingstreams.proxy pages when pattern URL fails.

Behavior:
 - Scrapes https://nbawebcast.top/ for game rows and builds candidate streams using
   https://gg.poocloud.in/{team_key}/index.m3u8 (Option A pattern).
 - Verifies pattern URL; if unreachable, attempts to extract real m3u8 via:
     https://boxingstreams.space/proxy/{team_key}3.php
   by parsing iframe src, direct .m3u8 links, or base64-encoded strings in the page
   and nested iframe pages.
 - Produces two playlists: nba_webcast.m3u8 and nba_webcast_tivimate.m3u8
"""

from __future__ import annotations
import asyncio
import aiohttp
import sys
import time
import re
import base64
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import quote, urljoin

# --- Config ---
NBA_BASE_URL = "https://nbawebcast.top/"
NBA_STREAM_URL_PATTERN = "https://gg.poocloud.in/{team_key}/index.m3u8"

NBA_CUSTOM_HEADERS = {
    "Origin": "https://embednow.top",
    "Referer": "https://embednow.top/",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"

OUT_PLAIN = "nba_webcast.m3u8"
OUT_TIVIMATE = "nba_webcast_tivimate.m3u8"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=18)
CONCURRENT_TASKS = 8

# --- Helpers ---


def build_stream_url(team_key: str) -> str:
    return NBA_STREAM_URL_PATTERN.format(team_key=team_key)


async def verify_stream_url(
    session: aiohttp.ClientSession,
    url: str,
    headers: Optional[Dict[str, str]] = None,
) -> bool:
    headers = headers or {}
    try:
        async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return False
            ctype = resp.headers.get("Content-Type", "").lower()
            if "application/vnd.apple.mpegurl" in ctype or "application/x-mpegurl" in ctype or "vnd.apple.mpegurl" in ctype:
                return True
            chunk = await resp.content.read(65536)
            if not chunk:
                return False
            txt = chunk.decode(errors="ignore").lower()
            if "#extm3u" in txt or ".m3u8" in txt:
                return True
            if ".m3u8" in url:
                return True
            return False
    except Exception:
        return False


def make_tivimate_url(m3u8_url: str, referrer: str, origin: Optional[str], user_agent: str) -> str:
    ua_enc = quote(user_agent, safe="")
    parts = [m3u8_url, f"referer={referrer}"]
    if origin:
        parts.append(f"origin={origin}")
    parts.append(f"user-agent=={ua_enc}")
    return "|".join(parts)


def _try_base64_decode(candidate: str) -> Optional[str]:
    """Try to decode a base64-like candidate (add padding) and assert it's a URL."""
    # strip non-base64 chars
    s = re.sub(r'[^A-Za-z0-9+/=]', '', candidate)
    # pad
    rem = len(s) % 4
    if rem:
        s += "=" * (4 - rem)
    try:
        data = base64.b64decode(s)
        txt = data.decode(errors="ignore")
        if txt.startswith("http"):
            return txt
    except Exception:
        return None
    return None


async def extract_m3u8_from_html(text: str, base: Optional[str] = None) -> Optional[str]:
    """
    Attempt multiple heuristics to find an m3u8 URL in HTML:
      - direct .m3u8 links
      - base64 payloads (aHR0... etc)
      - reversed base64 (string reversed then decode)
    """
    # 1) direct .m3u8 regex
    m = re.search(r"https?://[^\s'\"<>]+\.m3u8[^\s'\"<>]*", text)
    if m:
        return m.group(0)

    # 2) common base64 patterns: aHR0... (base64 for http)
    for b64 in re.findall(r"(?:[A-Za-z0-9+/]{10,}={0,2})", text):
        if "http" in b64:
            # skip obvious (rare)
            pass
        decoded = _try_base64_decode(b64)
        if decoded and ".m3u8" in decoded:
            return decoded

    # 3) look for atob("...") or atob('...') patterns
    for m_atob in re.findall(r"atob\(['\"]([^'\"]{8,})['\"]\)", text):
        # try direct
        dec = _try_base64_decode(m_atob)
        if dec and ".m3u8" in dec:
            return dec
        # try reversed (some obfuscation do reverse then atob)
        rev = m_atob[::-1]
        dec2 = _try_base64_decode(rev)
        if dec2 and ".m3u8" in dec2:
            return dec2

    # 4) sometimes source string is reversed then base64 encoded in JS var (e.g. encoded.split("").reverse().join(""))
    # find strings with '=' near the end and try reverse+decode
    for s in re.findall(r"['\"]([A-Za-z0-9+/=]{12,})['\"]", text):
        rev = s[::-1]
        dec = _try_base64_decode(rev)
        if dec and ".m3u8" in dec:
            return dec

    # 5) if base provided, attempt to resolve relative m3u8 occurrences
    if base:
        rel = re.search(r"['\"](/[^'\"]+\.m3u8[^'\"]*)['\"]", text)
        if rel:
            return urljoin(base, rel.group(1))

    return None


async def fetch_page_text(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT) as r:
            if r.status != 200:
                return None
            return await r.text()
    except Exception:
        return None


async def fetch_m3u8_from_proxy(session: aiohttp.ClientSession, team_key: str) -> Optional[str]:
    """
    Try to fetch an m3u8 by probing boxingstreams proxy pages:
      https://boxingstreams.space/proxy/{team_key}3.php
    Steps:
      - GET proxy page, search for .m3u8 or iframe src
      - If iframe found, GET iframe URL and search for .m3u8 or base64 encoded url
    """
    proxy_url = f"https://boxingstreams.space/proxy/{team_key}3.php"
    text = await fetch_page_text(session, proxy_url)
    if not text:
        return None

    # first attempt: direct in proxy page
    m3u8 = await extract_m3u8_from_html(text, base=proxy_url)
    if m3u8:
        return m3u8

    # look for iframe src
    iframe_match = re.search(r"<iframe[^>]+src=['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
    if iframe_match:
        iframe_src = iframe_match.group(1)
        # make absolute if needed
        if iframe_src.startswith("//"):
            iframe_src = "https:" + iframe_src
        elif iframe_src.startswith("/"):
            iframe_src = urljoin(proxy_url, iframe_src)
        iframe_text = await fetch_page_text(session, iframe_src)
        if iframe_text:
            m3u8 = await extract_m3u8_from_html(iframe_text, base=iframe_src)
            if m3u8:
                return m3u8

    # fallback: search proxy page for base64-like payloads and try decodes
    # (extract_m3u8_from_html already tried many base64 patterns; try a broader regex for 'aHR0' pieces)
    for maybe in re.findall(r"(aHR0[^\s'\"<>{}\)]+)", text):
        dec = _try_base64_decode(maybe)
        if dec and ".m3u8" in dec:
            return dec

    # nothing found
    return None


# --- Scraping logic ---


async def scrape_nba_page() -> List[Dict]:
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
    schedule_table = soup.find("table", class_="NBA_schedule_container")
    if not schedule_table:
        possible_rows = soup.find_all("tr")
        rows = possible_rows
        print("  ⚠️ Could not find 'NBA_schedule_container' table. Falling back to scanning <tr> elements.")
    else:
        tbody = schedule_table.find("tbody") or schedule_table
        rows = tbody.find_all("tr")

    print(f"  Found {len(rows)} potential game rows (scanned).")

    for row in rows:
        try:
            team_tds = row.find_all("td", class_="teamvs")
            if len(team_tds) >= 2:
                away_team = team_tds[0].get_text(strip=True)
                home_team = team_tds[1].get_text(strip=True)
            else:
                txt = row.get_text(" ", strip=True)
                if "vs" in txt:
                    parts = txt.split("vs", 1)
                    away_team = parts[0].strip()
                    home_team = parts[1].strip().split()[0] if parts[1].strip() else ""
                else:
                    continue

            logo_url = ""
            logos = row.find_all("td", class_="teamlogo")
            if logos and len(logos) >= 2:
                img = logos[1].find("img")
                if img and img.get("src"):
                    logo_url = img["src"]
            if not logo_url:
                img = row.find("img")
                if img and img.get("src"):
                    logo_url = img["src"]

            button = row.find("button", class_="watch_btn")
            team_key = None
            if button and button.has_attr("data-team"):
                team_key = button["data-team"]
            else:
                any_el = row.find(attrs={"data-team": True})
                if any_el:
                    team_key = any_el["data-team"]

            if not team_key:
                # skip rows without explicit team_key
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
                    "team_key": team_key,
                }
            )
        except Exception:
            continue

    print(f"  Built {len(results)} candidate streams from the page.")
    return results


async def verify_candidates(candidates: List[Dict]) -> List[Dict]:
    verified: List[Dict] = []
    sem = asyncio.Semaphore(CONCURRENT_TASKS)

    async def worker(item: Dict):
        async with sem:
            url = item["url"]
            headers = item.get("custom_headers", {})
            headers.setdefault("User-Agent", USER_AGENT)
            async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT) as session:
                ok = await verify_stream_url(session, url, headers=headers)
                if not ok:
                    # Try boxingstreams proxy fallback
                    team_key = item.get("team_key")
                    if team_key:
                        proxy_candidate = await fetch_m3u8_from_proxy(session, team_key)
                        if proxy_candidate:
                            # attempt verify proxy candidate
                            if await verify_stream_url(session, proxy_candidate, headers=headers):
                                item["url"] = proxy_candidate
                                ok = True
                if ok:
                    print(f"✅ Verified: {item['name']} → {item['url']}")
                    verified.append(item)
                else:
                    print(f"⚠️ Skipping (no m3u8 / unreachable): {item['name']}")

    tasks = [asyncio.create_task(worker(c)) for c in candidates]
    await asyncio.gather(*tasks)
    return verified


def write_playlists(verified: List[Dict], out_plain: str = OUT_PLAIN, out_tiv: str = OUT_TIVIMATE):
    lines_plain: List[str] = ["#EXTM3U"]
    lines_tiv: List[str] = ["#EXTM3U"]

    for item in verified:
        name = item.get("name", "").strip()
        if " | " in name:
            name = name.split(" | ", 1)[0].strip()
        url = item["url"]
        lines_plain.append(f"#EXTINF:-1,{name}")
        lines_plain.append(url)

        ref = item.get("ref") or NBA_BASE_URL
        origin = item.get("ref") or None
        ua = item.get("custom_headers", {}).get("User-Agent", USER_AGENT)
        tiv_url = make_tivimate_url(url, referrer=ref, origin=origin, user_agent=ua)
        lines_tiv.append(f"#EXTINF:-1,{name}")
        lines_tiv.append(tiv_url)

    Path(out_plain).write_text("\n".join(lines_plain) + "\n", encoding="utf-8")
    Path(out_tiv).write_text("\n".join(lines_tiv) + "\n", encoding="utf-8")
    print(f"\n✅ Playlists written: {out_plain} and {out_tiv}")


async def main():
    start = time.time()
    print("🚀 Starting NBA Webcast Scraper (pattern + boxingstreams fallback)...")
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
