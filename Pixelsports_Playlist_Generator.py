import json
import ssl
import urllib.request
import http.cookiejar
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

# --------------------------------------------------
# SSL BYPASS
# --------------------------------------------------
ssl._create_default_https_context = ssl._create_unverified_context

BASE = "https://pixelsport.tv"
API_EVENTS = f"{BASE}/backend/liveTV/events"

OUT_VLC = "Pixelsports_VLC.m3u8"
OUT_TIVI = "Pixelsports_TiviMate.m3u8"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

ENC_UA = quote(UA, safe="")

# --------------------------------------------------
# COOKIE JAR (CRITICAL)
# --------------------------------------------------
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cj)
)
urllib.request.install_opener(opener)

# --------------------------------------------------

def cloudflare_warmup():
    """Visit homepage once to obtain cf_clearance cookies"""
    req = urllib.request.Request(
        BASE,
        headers={
            "User-Agent": UA,
            "Accept": "text/html",
        },
    )
    opener.open(req, timeout=15)

def fetch_events():
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE + "/",
        "Origin": BASE,
        "X-Requested-With": "XMLHttpRequest",
    }

    req = urllib.request.Request(API_EVENTS, headers=headers)

    with opener.open(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

# --------------------------------------------------

def utc_to_et(utc):
    try:
        dt = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        offset = -4 if 3 <= dt.month <= 11 else -5
        return (dt + timedelta(hours=offset)).strftime("%I:%M %p ET %m/%d")
    except:
        return ""

def status(utc):
    try:
        dt = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (dt - now).total_seconds()
        if diff < -7200:
            return "Finished"
        if diff < 0:
            return "Live"
        return f"In {int(diff // 3600)}h"
    except:
        return ""

# --------------------------------------------------

def streams(ev):
    out = []
    for i in (1, 2, 3):
        u = ev["channel"].get(f"server{i}URL")
        if u and u.startswith("http"):
            out.append((u, "Home" if i == 1 else "Away" if i == 2 else "Alt"))
    return out

# --------------------------------------------------

def build(events, tivimate=False):
    lines = ["#EXTM3U"]

    for ev in events:
        title = ev["match_name"]
        title += f" - {utc_to_et(ev['date'])} - {status(ev['date'])}"

        logo = ev.get("competitors1_logo", "")
        league = ev["channel"]["TVCategory"]["name"]

        for url, label in streams(ev):
            lines.append(
                f'#EXTINF:-1 tvg-logo="{logo}" group-title="Pixelsports - {league} - {label}",{title}'
            )

            if tivimate:
                lines.append(
                    f"{url}|referer={BASE}/|origin={BASE}|user-agent={ENC_UA}|icy-metadata=1"
                )
            else:
                lines.append(f"#EXTVLCOPT:http-user-agent={UA}")
                lines.append(f"#EXTVLCOPT:http-referrer={BASE}/")
                lines.append(url)

    return "\n".join(lines)

# --------------------------------------------------

def main():
    print("[*] Cloudflare warm-up…")
    cloudflare_warmup()

    print("[*] Fetching PixelSport live events…")
    data = fetch_events()

    events = data.get("events", [])
    if not events:
        print("[-] No live events found.")
        return

    print(f"[+] {len(events)} events loaded")

    with open(OUT_VLC, "w", encoding="utf-8") as f:
        f.write(build(events, False))

    with open(OUT_TIVI, "w", encoding="utf-8") as f:
        f.write(build(events, True))

    print("[✔] Playlists generated successfully")

# --------------------------------------------------

if __name__ == "__main__":
    main()
