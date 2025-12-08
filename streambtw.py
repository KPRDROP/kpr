#!/usr/bin/env python3
"""
streambtw.py — resilient scraper for StreamBTW iframe pages & m3u8 extraction

Goal:
 - Crawl StreamBTW homepage (https://streambtw.live/)
 - Discover iframe pages (regular <iframe src="...">, JS-written base64 URLs,
   data:text/html;base64, and common obfuscation patterns)
 - Visit each iframe page (HTTP only — no Playwright) and heuristically extract
   playable .m3u8 URLs (direct, base64-encoded, reversed base64, data:...).
 - Validate m3u8 candidates and produce two output playlists:
     - Streambtw_VLC.m3u8  (VLC-style)
     - Streambtw_TiviMate.m3u8 (pipe header style)
 - Designed to run inside GitHub Actions without browsers.

Notes:
 - This is a heuristic scraper; streaming sites change often. If you see
   "No m3u8 found..." for many items, the site likely changed obfuscation.
 - Uses only aiohttp + built-ins (no Playwright) to avoid heavy system deps.
"""

from __future__ import annotations
import asyncio
import aiohttp
import re
import base64
import html
from urllib.parse import urljoin, urlparse
from typing import List, Set, Tuple, Optional
from pathlib import Path
from datetime import datetime

ROOT = "https://streambtw.com"
HOMEPAGE = ROOT + "/"
OUT_VLC = "Streambtw_VLC.m3u8"
OUT_TIVIMATE = "Streambtw_TiviMate.m3u8"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CONCURRENT_IFRAMES = 8
REQUEST_TIMEOUT = 15  # seconds

# Helper regexes
RE_IFRAME_SRC = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)
RE_PHP_IFRAME_PATH = re.compile(r'(\/iframe\/[^\s"\'<>]+\.php)', re.I)
RE_M3U8 = re.compile(r'https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*', re.I)
RE_ATOB = re.compile(r'atob\(\s*[\'"]([A-Za-z0-9+/=]+)[\'"]\s*\)', re.I)
RE_BASE64_LITERAL = re.compile(r'["\']([A-Za-z0-9+/=]{20,})["\']')  # long base64-like strings
RE_DATA_BASE64 = re.compile(r'data:text\/html;base64,([A-Za-z0-9+/=]+)', re.I)
RE_VAR_ENCODED = re.compile(r'\b(encoded|enc|e|x|s|server)\b\s*[:=]\s*[\'"]([A-Za-z0-9+/=]{10,})[\'"]', re.I)
RE_REVERSED_STR = re.compile(r'["\']([A-Za-z0-9+/=]{10,})["\']\s*\.split\(\s*[\'\"]\s*[\'\"]\s*\)\s*\.reverse\(\)\s*\.join\(\s*[\'\"]\s*[\'\"]\s*\)', re.I)
# Patterns like: encoded.split("").reverse().join("") or encoded.split("").reverse().join('')
RE_SPLIT_REVERSE = re.compile(r'([A-Za-z0-9+/=]{8,})\s*\.split\(\s*[\'\"]\s*[\'\"]\s*\)\s*\.reverse\(\)\s*\.join\(\s*[\'\"]\s*[\'\"]\s*\)', re.I)

HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*", "Referer": ROOT}


async def fetch_text(session: aiohttp.ClientSession, url: str, timeout: int = REQUEST_TIMEOUT) -> Optional[str]:
    try:
        async with session.get(url, headers=HEADERS, timeout=timeout) as resp:
            # Some pages block non-browser clients; return text on 200
            text = await resp.text(errors="ignore")
            return text
    except Exception as e:
        # print minimal debug info
        print(f"⚠️ Fetch failed: {url} -> {e}")
        return None


def try_base64_decode(s: str) -> Optional[str]:
    """Try decoding a base64 string and return decoded text (utf-8) if successful."""
    if not s or len(s) < 8:
        return None
    # ensure proper padding
    pad = (-len(s)) % 4
    s_padded = s + ("=" * pad)
    try:
        raw = base64.b64decode(s_padded, validate=True)
        try:
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return raw.decode("latin-1", errors="ignore")
    except Exception:
        return None


def reversed_and_decode(s: str) -> Optional[str]:
    """Reverse the string and try base64 decode (handles patterns like reversed base64)."""
    try:
        rev = s[::-1]
        return try_base64_decode(rev)
    except Exception:
        return None


def extract_m3u8_candidates_from_text(text: str) -> Set[str]:
    """Search text for obvious .m3u8 entries."""
    found = set(m.group(0) for m in RE_M3U8.finditer(text))
    return found


def extract_base64_candidates(text: str) -> Set[str]:
    """Extract base64-like literals and candidate vars from page HTML/JS."""
    candidates: Set[str] = set()

    # explicit atob("...") usage
    for m in RE_ATOB.finditer(text):
        candidates.add(m.group(1))

    # data:text/html;base64,...
    for m in RE_DATA_BASE64.finditer(text):
        candidates.add(m.group(1))

    # look for var encoded = "..."
    for m in RE_VAR_ENCODED.finditer(text):
        candidates.add(m.group(2))

    # long quoted literals that look like base64
    for m in RE_BASE64_LITERAL.finditer(text):
        val = m.group(1)
        # heuristics: contains only base64 chars and length divisible-ish by 4 (or close)
        if re.fullmatch(r'[A-Za-z0-9+/=]+', val):
            candidates.add(val)

    # reversed patterns: "...".split("").reverse().join("")
    # Try to pull the literal preceding split()
    for m in RE_REVERSED_STR.finditer(text):
        candidates.add(m.group(1))
    for m in RE_SPLIT_REVERSE.finditer(text):
        candidates.add(m.group(1))

    return candidates


def extract_iframe_urls_from_homepage(html_text: str, base: str = ROOT) -> List[str]:
    """Find iframe URLs from homepage using multiple heuristics."""
    urls: Set[str] = set()

    text = html.unescape(html_text or "")

    # direct <iframe src="...">
    for m in RE_IFRAME_SRC.finditer(text):
        src = m.group(1).strip()
        # skip about:blank
        if not src or src.startswith("about:"):
            continue
        full = urljoin(base, src)
        urls.add(full)

    # plain php pattern /iframe/*.php
    for m in RE_PHP_IFRAME_PATH.finditer(text):
        p = m.group(1)
        urls.add(urljoin(base, p))

    # Inline JS base64 usage — decode candidate base64s and search for iframe-like url results
    base64_candidates = extract_base64_candidates(text)
    for cand in base64_candidates:
        # try decode normally
        dec = try_base64_decode(cand)
        if dec:
            # find iframe links inside decoded snippet
            for m in RE_IFRAME_SRC.finditer(dec):
                urls.add(urljoin(base, m.group(1)))
            for m in RE_PHP_IFRAME_PATH.finditer(dec):
                urls.add(urljoin(base, m.group(1)))
            # sometimes decoded string is a URL
            if dec.startswith("http"):
                urls.add(dec)
        # try reversed decode
        dec2 = reversed_and_decode(cand)
        if dec2:
            for m in RE_IFRAME_SRC.finditer(dec2):
                urls.add(urljoin(base, m.group(1)))
            for m in RE_PHP_IFRAME_PATH.finditer(dec2):
                urls.add(urljoin(base, m.group(1)))
            if dec2.startswith("http"):
                urls.add(dec2)

    # also look for data:text/html;base64, embedded pages (already captured above)
    for m in RE_DATA_BASE64.finditer(text):
        b = m.group(1)
        dec = try_base64_decode(b)
        if dec:
            # decoded content may include iframe URLs
            for mm in RE_IFRAME_SRC.finditer(dec):
                urls.add(urljoin(base, mm.group(1)))
            for mm in RE_PHP_IFRAME_PATH.finditer(dec):
                urls.add(urljoin(base, mm.group(1)))
            for mm in RE_M3U8.finditer(dec):
                # if decoded content already includes m3u8, we can treat decoded as a "page" source:
                # create a pseudo-url marker that we will process specially (data:decoded)
                urls.add("data:decoded-base64:" + b)

    return sorted(urls)


async def gather_iframe_pages(session: aiohttp.ClientSession, iframe_urls: List[str]) -> List[Tuple[str, Optional[str]]]:
    """
    Visit each iframe URL and extract m3u8 candidates:
      Returns list of tuples: (iframe_url, found_m3u8_or_None)
    """
    sem = asyncio.Semaphore(CONCURRENT_IFRAMES)
    results: List[Tuple[str, Optional[str]]] = []

    async def worker(url: str):
        async with sem:
            # special-case pseudo-data decoded items
            if url.startswith("data:decoded-base64:"):
                b64 = url.split(":", 2)[2]
                decoded = try_base64_decode(b64)
                if not decoded:
                    results.append((url, None))
                    return
                # search decoded for m3u8
                found = extract_m3u8_candidates_from_text(decoded)
                if found:
                    # return best candidate (first)
                    results.append((url, sorted(found)[0]))
                    return
                results.append((url, None))
                return

            text = await fetch_text(session, url)
            if not text:
                results.append((url, None))
                return

            # First look for direct m3u8 strings in the iframe page
            found = extract_m3u8_candidates_from_text(text)
            if found:
                results.append((url, sorted(found)[0]))
                return

            # Look for base64 obfuscation candidates in the iframe page
            b64_cands = extract_base64_candidates(text)
            for cand in b64_cands:
                dec = try_base64_decode(cand)
                if dec:
                    f = extract_m3u8_candidates_from_text(dec)
                    if f:
                        results.append((url, sorted(f)[0]))
                        return
                # reversed
                dec2 = reversed_and_decode(cand)
                if dec2:
                    f = extract_m3u8_candidates_from_text(dec2)
                    if f:
                        results.append((url, sorted(f)[0]))
                        return

            # Also search for encoded = "..." then .split("").reverse().join("") patterns where literal may be visible
            # fallback: search for any quoted long string, reverse, decode, then look
            for m in re.finditer(r'["\']([A-Za-z0-9+/=]{12,})["\']', text):
                lit = m.group(1)
                dec = try_base64_decode(lit)
                if dec and RE_M3U8.search(dec):
                    results.append((url, RE_M3U8.search(dec).group(0)))
                    return
                dec2 = reversed_and_decode(lit)
                if dec2 and RE_M3U8.search(dec2):
                    results.append((url, RE_M3U8.search(dec2).group(0)))
                    return

            # last attempt: sometimes the page constructs a JS var which is reversed at runtime -
            # look for sequences like var s="..."; var server = atob(s.split("").reverse().join(""));
            # We'll search for a quoted token then reverse+decode:
            for m in re.finditer(r'["\']([A-Za-z0-9+/=]{8,})["\']\s*\.split\(\)\s*\.reverse\(\)\s*\.join\(\)', text):
                token = m.group(1)
                dec = reversed_and_decode(token)
                if dec and RE_M3U8.search(dec):
                    results.append((url, RE_M3U8.search(dec).group(0)))
                    return

            # No m3u8 found
            results.append((url, None))

    await asyncio.gather(*(worker(u) for u in iframe_urls))
    return results


async def validate_m3u8(session: aiohttp.ClientSession, url: str) -> bool:
    """Quick validation: attempt HEAD then GET small amount to see if URL is accessible."""
    try:
        # prefer HEAD
        async with session.head(url, headers=HEADERS, timeout=10) as resp:
            if resp.status in (200, 206, 403):
                return True
    except Exception:
        # try GET
        try:
            async with session.get(url, headers=HEADERS, timeout=10) as resp:
                if resp.status in (200, 206, 403):
                    return True
        except Exception:
            return False
    return False


def build_playlists(entries: List[Tuple[str, str]]) -> Tuple[str, str]:
    """
    entries: list of (title, url)
    returns: (vlc_text, tivimate_text)
    """
    # VLC: standard EXTINF with minimal metadata
    vlc_lines = ['#EXTM3U']
    tiv_lines = ['#EXTM3U']

    for title, url in entries:
        safe_title = title.replace(",", " -").strip()
        vlc_lines.append(f'#EXTINF:-1,{safe_title}')
        vlc_lines.append(url)

        # TiviMate style pipe headers (simple referer/origin)
        try:
            parsed = urlparse(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            origin = ROOT
        # include referer to iframe root to help servers that require origin/referrer
        tiv_lines.append(f'#EXTINF:-1,{safe_title}')
        tiv_lines.append(f'{url}|Referer={ROOT}|Origin={origin}|User-Agent={USER_AGENT}')

    return ("\n".join(vlc_lines) + "\n", "\n".join(tiv_lines) + "\n")


async def main():
    print("🔍 Fetching StreamBTW homepage...")
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(limit_per_host=CONCURRENT_IFRAMES)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS, connector=connector) as session:
        homepage = await fetch_text(session, HOMEPAGE)
        if not homepage:
            print("❌ Failed to fetch homepage.")
            return

        iframe_urls = extract_iframe_urls_from_homepage(homepage, base=HOMEPAGE)
        if not iframe_urls:
            print("📌 Found 0 iframe pages")
            print("❌ No streams captured.")
            return

        print(f"📌 Found {len(iframe_urls)} iframe pages")
        # de-duplicate
        iframe_urls = sorted(dict.fromkeys(iframe_urls))

        # fetch each iframe page and extract m3u8 candidates
        print("🔎 Checking iframe pages (HTTP-only heuristics)...")
        pairs = await gather_iframe_pages(session, iframe_urls)

        # Validate each candidate and assemble list of (title, url)
        found_entries: List[Tuple[str, str]] = []
        for iframe_url, candidate in pairs:
            if candidate:
                ok = await validate_m3u8(session, candidate)
                if ok:
                    # Try to derive a friendly title from iframe url path
                    parsed = urlparse(iframe_url)
                    title = parsed.path.strip("/").split("/")[-1] or iframe_url
                    # prettify
                    title = title.replace(".php", "").replace("-", " ").replace("_", " ").title()
                    found_entries.append((title, candidate))
                    print(f"✅ Found m3u8 for {iframe_url} -> {candidate}")
                else:
                    print(f"⚠️ Candidate found but not validated: {candidate} (from {iframe_url})")
            else:
                print(f"⚠️ No m3u8 found for {iframe_url}")

        if not found_entries:
            print("❌ No streams captured from any iframe pages.")
            return

        # Build playlists
        vlc_text, tiv_text = build_playlists(found_entries)
        Path(OUT_VLC).write_text(vlc_text, encoding="utf-8")
        Path(OUT_TIVIMATE).write_text(tiv_text, encoding="utf-8")
        print(f"✅ VLC playlist generated: {OUT_VLC}")
        print(f"✅ TiviMate playlist generated: {OUT_TIVIMATE}")


if __name__ == "__main__":
    asyncio.run(main())
