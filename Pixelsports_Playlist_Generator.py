#!/usr/bin/env python3

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import quote
from urllib.error import URLError, HTTPError
import requests

# ---------------- CONFIG ---------------- #

BASE = "https://pixelsport.tv"

# 🔐 API URL MUST come from env (GitHub Secrets / Variables)
API_EVENTS = os.getenv("PIXELSPORTS_API_URL")

OUT_VLC = "Pixelsports_VLC.m3u8"
OUT_TIVI = "Pixelsports_TiviMate.m3u8"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
UA_ENC = quote(UA, safe="")

# ---------------- SAFETY ---------------- #

def log(*a):
    print(*a)
    sys.stdout.flush()

if not API_EVENTS:
    log("❌ Missing API URL. Set PIXELSPORTS_API_URL env variable.")
    sys.exit(1)

# ---------------- TIME HELPERS ---------------- #

def utc_to_et(utc_str: str) -> str:
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        # US Eastern DST approx
        offset = -4 if 3 <= dt.month <= 11 else -5
        et = dt + timedelta(hours=offset)
        return et.strftime("%I:%M %p ET %m/%d/%Y").replace(" 0", " ")
    except Exception:
        return ""

# ---------------- API FETCH ---------------- #

def fetch_events() -> list:
    log("[*] Fetching PixelSports events API…")

    try:
        r = requests.get(
            API_EVENTS,
            headers={
                "User-Agent": UA,
                "Accept": "application/json",
                "Referer": BASE + "/"
            },
            timeout=15
        )
        r.raise_for_status()
    except Exception as e:
        log("❌ API request failed:", e)
        return []

    raw = r.text.strip()

    if not raw.startswith("{") and not raw.startswith("["):
        log("❌ API did not return JSON")
        log(raw[:200])
        return []

    try:
        data = json.loads(raw)
    except Exception as e:
        log("❌ JSON parse failed:", e)
        log(raw[:200])
        return []

    events = data.get("events", [])
    if not isinstance(events, list):
        log("❌ Invalid API format")
        return []

    return events

# ---------------- PLAYLIST BUILD ---------------- #

def build_playlist(events: list, tivimate: bool = False) -> str:
    out = ["#EXTM3U"]

    for ev in events:
        title = ev.get("match_name", "Live Event")
        logo = ev.get("logo", "")
        time_et = utc_to_et(ev.get("date", ""))

        if time_et:
            title = f"{title} - {time_et}"

        channels = ev.get("channel", {})

        for idx, label in [(1, "Home"), (2, "Away"), (3, "Alt")]:
            url = channels.get(f"server{idx}URL")
            if not url or url == "null":
                continue

            extinf = f'#EXTINF:-1 group-title="PixelSport"'
            if logo:
                extinf += f' tvg-logo="{logo}"'
            extinf += f",{title} ({label})"

            out.append(extinf)

            if tivimate:
                out.append(
                    f"{url}|user-agent={UA_ENC}|referer={BASE}/|origin={BASE}|icy-metadata=1"
                )
            else:
                out.append(f"#EXTVLCOPT:http-user-agent={UA}")
                out.append(f"#EXTVLCOPT:http-referrer={BASE}/")
                out.append(url)

    return "\n".join(out)

# ---------------- MAIN ---------------- #

async def main():
    events = fetch_events()

    if not events:
        log("❌ No events found")
        return

    with open(OUT_VLC, "w", encoding="utf-8") as f:
        f.write(build_playlist(events, tivimate=False))

    with open(OUT_TIVI, "w", encoding="utf-8") as f:
        f.write(build_playlist(events, tivimate=True))

    log(f"✔ Generated {len(events)} events")
    log(f"✔ {OUT_VLC}")
    log(f"✔ {OUT_TIVI}")

if __name__ == "__main__":
    asyncio.run(main())
