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

CHANNEL_LOGOS = {}

CATEGORY_KEYWORDS = {
    "NBA": "Basketball",
    "UFC": "Combat Sports",
    "Football": "Football",
    "Soccer": "Football",
    "x": "Football",
}

NAV_TIMEOUT = 60000
CONCURRENT_FETCHES = 8
RETRIES = 3
CLICK_WAIT = 4
VALIDATE_TIMEOUT = 10
MIN_TOKEN_PARAMS = ("?s=", "&e=")

# ------------------------
# Helpers
# ------------------------

def has_tokenized_query(url: str) -> bool:
    return ("?s=" in url) and ("&e=" in url or "&exp=" in url)

def hostname_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except:
        return ""

def replace_hostname(original_url: str, new_hostname: str) -> str:
    try:
        p = urlparse(original_url)
        new_netloc = new_hostname
        if p.port:
            new_netloc = f"{new_hostname}:{p.port}"
        new_p = p._replace(netloc=new_netloc)
        return urlunparse(new_p)
    except:
        return original_url

async def http_check(url: str, session: aiohttp.ClientSession, timeout: int = VALIDATE_TIMEOUT) -> bool:
    try:
        async with session.get(url, headers={"User-Agent": USER_AGENT, "Referer": "https://sportsonline.sn/"}, timeout=timeout) as resp:
            return resp.status == 200
    except:
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
# Clappr extraction
# ------------------------

async def extract_from_clappr(page):
    try:
        js = """() => {
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
                if (window.player && window.player.getPlaylist) {
                    try {
                        const p = window.player.getPlaylist();
                        if (p && p.length) out.push(p[0].file || (p[0].sources && p[0].sources[0].file));
                    } catch(e){}
                }
                return out;
