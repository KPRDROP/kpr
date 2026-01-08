import json
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

# --------------------------------------------------
# SSL BYPASS (API CERT IS OFTEN MISCONFIGURED)
# --------------------------------------------------
ssl._create_default_https_context = ssl._create_unverified_context

BASE = "https://pixelsport.tv"
API_EVENTS = f"{BASE}/backend/liveTV/events.json"

OUTPUT_FILE_VLC = "Pixelsports_VLC.m3u8"
OUTPUT_FILE_TIVIMATE = "Pixelsports_TiviMate.m3u8"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

REFERER = f"{BASE}/"
ENC_UA = quote(USER_AGENT, safe="")

# --------------------------------------------------

def fetch_events():
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": REFERER,
        "Origin": BASE,
        "Connection": "close",
    }

    req = urllib.request.Request(API_EVENTS, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[!] API fetch failed: {e}")
        return {}

# --------------------------------------------------

def utc_to_et(utc):
    try:
        dt = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        offset = -4 if 3 <= dt.month <= 11 else -5
        return (dt + timedelta(hours=offset)).strftime("%I:%M %p ET - %m/%d/%Y")
    except Exception:
        return ""

def game_status(utc):
    try:
        dt = datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (dt - now).total_seconds()
        if diff < -10800:
            return "Finished"
        if diff < 0:
            return "Live"
        return f"In {int(diff // 3600)}h {int(diff % 3600 // 60)}m"
    except Exception:
        return ""

# --------------------------------------------------

def extract_streams(event):
    streams = []
    for i in (1, 2, 3):
        url = event.get(f"server{i}URL")
        if url and url.lower().startswith("http"):
            label = "Home" if i == 1 else "Away" if i == 2 else "Alt"
            streams.append((url, label))
    return streams

# --------------------------------------------------

def build_playlist(events, tivimate=False):
    out = ["#EXTM3U"]

    for ev in events:
        title = ev.get("match_name", "Live Event")
        date = ev.get("date", "")
        title += f" - {utc_to_et(date)} - {game_status(date)}"

        logo = ev.get("competitors1_logo", "")
        league = ev.get("channel", {}).get("TVCategory", {}).get("name", "SPORTS")

        for url, label in extract_streams(ev):
            out.append(
                f'#EXTINF:-1 tvg-logo="{logo}" group-title="Pixelsports - {league} - {label}",{title}'
            )

            if tivimate:
                out.append(
                    f"{url}|referer={REFERER}|origin={REFERER}|user-agent={ENC_UA}|icy-metadata=1"
                )
            else:
                out.append(f"#EXTVLCOPT:http-user-agent={USER_AGENT}")
                out.append(f"#EXTVLCOPT:http-referrer={REFERER}")
                out.append(url)

    return "\n".join(out)

# --------------------------------------------------

def main():
    print("[*] Fetching PixelSport live events…")
    data = fetch_events()

    events = data.get("events", [])
    if not events:
        print("[-] No live events found.")
        return

    print(f"[+] {len(events)} events loaded")

    with open(OUTPUT_FILE_VLC, "w", encoding="utf-8") as f:
        f.write(build_playlist(events, tivimate=False))

    with open(OUTPUT_FILE_TIVIMATE, "w", encoding="utf-8") as f:
        f.write(build_playlist(events, tivimate=True))

    print("[✔] Playlists generated successfully")

# --------------------------------------------------

if __name__ == "__main__":
    main()
