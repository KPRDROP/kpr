#!/usr/bin/env python3
"""
streambtw.py — lightweight scraper that extracts .m3u8 streams from iframe pages
(no API). Produces two outputs:
  - Streambtw_VLC.m3u8      (VLC-friendly with #EXTVLCOPT headers)
  - Streambtw_TiviMate.m3u8 (TiviMate pipe-format with referer|origin|user-agent)

Behavior:
 - Fetch the site homepage (config HOME_URL) and find iframe pages under /iframe/
 - For each iframe page, GET its HTML and search for any .m3u8 occurrences
   (also handles embedded nested <iframe> tags).
 - Attempts simple rewriting (tracks-v1a1/... -> index.m3u8) to prefer index
 - Deduplicates by normalized title
 - Replaces "@" with "vs" in event titles
 - Prints progress and warnings for items with no m3u8
"""
from __future__ import annotations
import asyncio
import aiohttp
import re
import urllib.parse
from pathlib import Path
from typing import List, Dict, Set

HOME_URL = "https://streambtw.com/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"

VLC_OUTPUT = "Streambtw_VLC.m3u8"
TIVIMATE_OUTPUT = "Streambtw_TiviMate.m3u8"

VLC_CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-origin=https://streambtw.live',
    '#EXTVLCOPT:http-referrer=https://streambtw.live',
    f'#EXTVLCOPT:http-user-agent={USER_AGENT}',
]

# Regex to find .m3u8-like URLs (covers quoted, unquoted, querystring)
M3U8_RE = re.compile(
    r"""(?P<url>https?://[^\s"'<>]+?\.m3u8(?:\?[^\s"'<>]*)?)""",
    re.IGNORECASE,
)

# Fallback regex to detect URLs encoded inside js params (mu=... or src=...),
# and unquote them if found.
ENCODED_PARAM_RE = re.compile(r'(?:mu|src|file)=([^&"\'<>]+)', re.IGNORECASE)


async def fetch_text(session: aiohttp.ClientSession, url: str, timeout: int = 20) -> str | None:
    try:
        async with session.get(url, timeout=timeout) as resp:
            text = await resp.text(errors="ignore")
            return text
    except Exception as e:
        print(f"❌ Fetch failed for {url}: {e}")
        return None


def find_iframe_links_from_home(html: str) -> List[str]:
    """
    Extract candidate iframe URLs from the homepage HTML.
    Looks for hrefs containing '/iframe/' or direct iframe srcs.
    Returns absolute paths (joined with HOME_URL where necessary).
    """
    links: List[str] = []
    # href="/iframe/xxx.php" or href="iframe/xxx.php" or src="/iframe/xxx.php"
    for m in re.finditer(r'(?:href|src)\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        href = m.group(1).strip()
        if "/iframe/" in href or href.startswith("iframe/"):
            # make absolute
            full = urllib.parse.urljoin(HOME_URL + "/", href)
            if full not in links:
                links.append(full)
    # also scan for javascript window.open('/iframe/...')
    for m in re.finditer(r'["\'](/?iframe/[^"\']+)["\']', html, flags=re.IGNORECASE):
        full = urllib.parse.urljoin(HOME_URL + "/", m.group(1))
        if full not in links:
            links.append(full)
    return links


def extract_m3u8_candidates_from_text(text: str) -> List[str]:
    found: List[str] = []
    if not text:
        return found
    # first, direct .m3u8 urls
    for m in M3U8_RE.finditer(text):
        url = m.group("url")
        # decode entities
        url = urllib.parse.unquote(url)
        found.append(url)
    # second, encoded params like mu=<encoded_url>
    for m in ENCODED_PARAM_RE.finditer(text):
        val = urllib.parse.unquote(m.group(1))
        if ".m3u8" in val:
            # ensure it looks like an http url
            if val.startswith("http"):
                found.append(val)
            else:
                # maybe relative: join with HOME_URL
                found.append(urllib.parse.urljoin(HOME_URL + "/", val))
    # dedupe, preserve order
    out: List[str] = []
    seen: Set[str] = set()
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def prefer_index_m3u8(url: str) -> str:
    """
    If URL ends with tracks-v1a1/... or similar, rewrite to index.m3u8
    e.g. https://.../tracks-v1a1/mono.ts.m3u8 --> https://.../index.m3u8
    or https://.../.../playlist/stream_redzone.m3u8?.... -> keep as-is
    """
    if "tracks-v1" in url and url.endswith(".m3u8"):
        # change everything after the last '/' to index.m3u8
        parsed = urllib.parse.urlparse(url)
        base = parsed.scheme + "://" + parsed.netloc + "/"
        # preserve path up to last segment before 'tracks-v1'
        path = parsed.path
        # try to find segment before '/tracks-v1'
        idx = path.find("/tracks-v1")
        if idx != -1:
            new_path = path[:idx] + "/index.m3u8"
            new_url = parsed._replace(path=new_path, query="").geturl()
            return new_url
    # if contains '/tracks-v1a1/' but then '/mono.ts.m3u8' we still try to rewrite:
    if re.search(r"/tracks-v1[^/]*?/[^/]*?\.m3u8", url):
        parsed = urllib.parse.urlparse(url)
        # take directory up to the 'tracks...' parent
        new_path = re.sub(r"/tracks-v1[^/]*?/.*$", "/index.m3u8", parsed.path)
        new_url = parsed._replace(path=new_path, query="").geturl()
        return new_url
    return url


def sanitize_title(raw: str) -> str:
    # normalize whitespace, remove enclosing quotes, replace '@' -> 'vs'
    t = raw.strip()
    t = re.sub(r"\s*\@\s*", " vs ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t


def build_vlc_playlist(entries: List[Dict]) -> str:
    lines = ['#EXTM3U']
    for e in entries:
        title = sanitize_title(e["title"])
        tvg_logo = e.get("logo", "")
        group = e.get("group", "Live")
        tvg_id = e.get("tvg_id", "")
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{tvg_logo}" group-title="{group}",{title}')
        # add VLC headers
        for h in VLC_CUSTOM_HEADERS:
            lines.append(h)
        lines.append(e["url"])
    return "\n".join(lines)


def build_tivimate_playlist(entries: List[Dict]) -> str:
    """
    TiviMate pipe format: url|referer=<referer>|origin=<origin>|user-agent=<encoded UA>
    IMPORTANT: order the pipe parts as the user requested: referer first, then origin.
    """
    lines = ['#EXTM3U']
    ua_encoded = urllib.parse.quote(USER_AGENT, safe="")
    for e in entries:
        title = sanitize_title(e["title"])
        tvg_logo = e.get("logo", "")
        group = e.get("group", "Live")
        tvg_id = e.get("tvg_id", "")
        lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{tvg_logo}" group-title="{group}",{title}')
        referer = e.get("referer") or HOME_URL
        origin = urllib.parse.urlparse(referer).scheme + "://" + urllib.parse.urlparse(referer).netloc
        url_with_pipe = f'{e["url"]}|referer={referer}|origin={origin}|user-agent={ua_encoded}'
        lines.append(url_with_pipe)
    return "\n".join(lines)


async def process_iframe_page(session: aiohttp.ClientSession, iframe_url: str) -> List[str]:
    """
    Fetch iframe page, extract m3u8 candidates from its HTML and from nested iframe sources.
    Returns list of m3u8 URLs (possibly rewritten).
    """
    html = await fetch_text(session, iframe_url)
    if not html:
        return []
    candidates = extract_m3u8_candidates_from_text(html)

    # Also search for nested iframe src attributes and try to fetch them as well
    nested_iframes = []
    for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        src = m.group(1)
        nested_iframes.append(urllib.parse.urljoin(iframe_url, src))

    # fetch nested iframe pages concurrently to look for m3u8 there too
    if nested_iframes:
        tasks = [fetch_text(session, u) for u in nested_iframes]
        pages = await asyncio.gather(*tasks)
        for p in pages:
            if p:
                candidates += extract_m3u8_candidates_from_text(p)

    # prefer index style and dedupe preserving order
    out: List[str] = []
    seen: Set[str] = set()
    for c in candidates:
        c = prefer_index_m3u8(c)
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


async def main():
    print("Fetching homepage...")
    headers = {"User-Agent": USER_AGENT, "Referer": HOME_URL}
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        home_html = await fetch_text(session, HOME_URL)
        if not home_html:
            print("❌ Failed to download homepage; aborting.")
            return

        iframe_pages = find_iframe_links_from_home(home_html)
        if not iframe_pages:
            # as fallback: search for explicit example endpoints like '/iframe/redzone.php'
            for m in re.finditer(r'(/iframe/[^"\'>\s]+\.php)', home_html, flags=re.IGNORECASE):
                url = urllib.parse.urljoin(HOME_URL + "/", m.group(1))
                if url not in iframe_pages:
                    iframe_pages.append(url)

        print(f"Parsing homepage — found {len(iframe_pages)} iframe pages to check.")

        entries: List[Dict] = []
        seen_titles: Set[str] = set()

        # process pages sequentially (avoid too many concurrent connections that trip hosts)
        for idx, frame_url in enumerate(iframe_pages, start=1):
            print(f"\n🔎 [{idx}/{len(iframe_pages)}] Checking iframe: {frame_url}")
            m3u8s = await process_iframe_page(session, frame_url)
            if not m3u8s:
                print(f"⚠️ No m3u8 found for {frame_url}")
                continue

            # Determine a friendly title from the iframe path or from page (cheap)
            title = ""
            # try to infer name in the iframe url path (e.g. /iframe/redzone.php -> REDZONE)
            p = urllib.parse.urlparse(frame_url).path
            name_guess = p.rstrip("/").split("/")[-1].replace(".php", "")
            name_guess = name_guess.replace("_", " ").replace("-", " ").strip()
            title = name_guess if name_guess else frame_url

            # prefer the first m3u8 candidate that's usable
            selected = None
            for candidate in m3u8s:
                # very basic check: must be http and end with .m3u8 (or contain .m3u8)
                if candidate.startswith("http") and ".m3u8" in candidate:
                    selected = candidate
                    break
            if not selected:
                print(f"⚠️ No valid http m3u8 candidates for {frame_url}")
                continue

            # store entry
            # create TVG id from name_guess
            tvg_id = re.sub(r'[^a-z0-9\-]+', '-', name_guess.lower()).strip('-') or ""
            # use frame_url as referer for headers
            entry = {
                "title": title,
                "url": selected,
                "logo": "",  # site doesn't provide logos here; user can post-process
                "group": "StreamBTW",
                "tvg_id": tvg_id,
                "referer": frame_url,
            }
            # dedupe by normalized title
            normalized = sanitize_title(title).lower()
            if normalized in seen_titles:
                print(f"ℹ️ Skipping duplicate title: {title}")
                continue
            seen_titles.add(normalized)
            entries.append(entry)
            print(f"✅ Captured: {selected}")

        if not entries:
            print("❌ No streams captured from any iframe pages.")
        else:
            print("\nGenerating playlists...")
            vlc_text = build_vlc_playlist(entries)
            tivi_text = build_tivimate_playlist(entries)
            Path(VLC_OUTPUT).write_text(vlc_text, encoding="utf-8")
            Path(TIVIMATE_OUTPUT).write_text(tivi_text, encoding="utf-8")
            print(f"VLC playlist generated: {VLC_OUTPUT}")
            print(f"TiviMate playlist generated: {TIVIMATE_OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
