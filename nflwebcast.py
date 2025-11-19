import json
import urllib.request
import ssl
from urllib.error import URLError, HTTPError
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

# Disable SSL certificate verification (be cautious in production)
ssl._create_default_https_context = ssl._create_unverified_context

BASE = "https://nflwebcast.com"
PAGE_PATH = "/sbl/"

# Output files
OUTPUT_FILE_VLC = "NFLwebcast_VLC.m3u8"
OUTPUT_FILE_TIVIMATE = "NFLwebcast_TiviMate.m3u8"

# Headers
VLC_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
VLC_REFERER = f"{BASE}{PAGE_PATH}"
TIVIMATE_USER_AGENT = quote(VLC_USER_AGENT, safe="")

LEAGUE_INFO = {
    # ... (your existing league info)
}

def utc_to_eastern(utc_str):
    try:
        utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        month = utc_dt.month
        # naive offset (Daylight Savings not handled precisely)
        offset = -4 if 3 <= month <= 11 else -5
        et = utc_dt + timedelta(hours=offset)
        return et.strftime("%I:%M %p ET - %m/%d/%Y").lstrip("0")
    except Exception as e:
        print(f"[!] utc_to_eastern parsing error: {e} (input: {utc_str})")
        return ""

def get_game_status(utc_str):
    try:
        utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (utc_dt - now).total_seconds()

        if diff < -10800:  # ended 3 hours ago
            return "Finished"
        elif diff < 0:
            return "Started"
        else:
            hours = int(diff // 3600)
            minutes = int((diff % 3600) // 60)
            if hours > 0:
                return f"In {hours}h {minutes}m"
            else:
                return f"In {minutes}m"
    except Exception as e:
        print(f"[!] get_game_status error: {e} (input: {utc_str})")
        return ""

def fetch_json(url):
    headers = {
        "User-Agent": VLC_USER_AGENT,
        "Referer": VLC_REFERER,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Connection": "close",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except HTTPError as e:
        print(f"[!] HTTPError fetching {url}: {e.code} — {e.reason}")
    except URLError as e:
        print(f"[!] URLError fetching {url}: {e.reason}")
    except json.JSONDecodeError as e:
        print(f"[!] JSON parse error for {url}: {e}")
        print("Raw response:", raw)
    except Exception as e:
        print(f"[!] Unexpected error fetching JSON: {e}")
    return None

def collect_links_with_labels(event):
    links = []
    comp1_home = event.get("competitors1_homeAway", "").lower() == "home"
    chan = event.get("channel", {}) or {}
    for i in range(1, 4):
        key = f"server{i}URL"
        link = chan.get(key)
        if not link or link.lower() == "null":
            continue
        if i == 1:
            label = "Home" if comp1_home else "Away"
        elif i == 2:
            label = "Away" if comp1_home else "Home"
        else:
            label = "Alt"
        links.append((link, label))
    return links

def get_league_info(league_name):
    for key, (tvid, logo, disp) in LEAGUE_INFO.items():
        if key.lower() in league_name.lower():
            return tvid, logo, disp
    return ("Pixelsports.Dummy.us", "", "Live Sports")

def build_m3u(events, tivimate=False):
    lines = ["#EXTM3U"]
    for ev in events:
        title = ev.get("match_name", ev.get("name", "Unknown Event")).strip()
        date_str = ev.get("date")
        time_et = utc_to_eastern(date_str) if date_str else ""
        status = get_game_status(date_str) if date_str else ""
        if time_et:
            title += f" - {time_et}"
        if status:
            title += f" - {status}"

        league = ev.get("channel", {}).get("TVCategory", {}).get("name", "")
        tvid, group_logo, group_display = get_league_info(league)

        logo = ev.get("competitors1_logo") or group_logo

        for link, label in collect_links_with_labels(ev):
            lines.append(
                f'#EXTINF:-1 tvg-id="{tvid}" tvg-logo="{logo}" group-title="NFLwebcast - {group_display} - {label}",{title}'
            )
            if tivimate:
                full = f"{link}|referer={VLC_REFERER}|origin={VLC_REFERER}|user-agent={TIVIMATE_USER_AGENT}"
                lines.append(full)
            else:
                lines.append(f"#EXTVLCOPT:http-user-agent={VLC_USER_AGENT}")
                lines.append(f"#EXTVLCOPT:http-referrer={VLC_REFERER}")
                lines.append(link)
    return "\n".join(lines)

def main():
    print("[*] Starting scraper…")
    # TODO: Replace this with actual API endpoint you discover
    API_EVENTS = "https://pixelsport.tv/backend/liveTV/events"  # <-- placeholder

    data = fetch_json(API_EVENTS)
    if not data:
        print("[-] No data returned from API.")
        return

    events = data.get("events")
    if not events:
        print("[-] No 'events' key in API response, or it's empty.")
        return

    print(f"[*] Fetched {len(events)} events.")

    vlc_playlist = build_m3u(events, tivimate=False)
    with open(OUTPUT_FILE_VLC, "w", encoding="utf-8") as f:
        f.write(vlc_playlist)
    print(f"[+] VLC playlist written to {OUTPUT_FILE_VLC}")

    tiv_playlist = build_m3u(events, tivimate=True)
    with open(OUTPUT_FILE_TIVIMATE, "w", encoding="utf-8") as f:
        f.write(tiv_playlist)
    print(f"[+] TiviMate playlist written to {OUTPUT_FILE_TIVIMATE}")

if __name__ == "__main__":
    main()
