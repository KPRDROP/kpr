import json
import urllib.request
import ssl
from urllib.error import URLError, HTTPError
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

# Disable SSL certificate verification globally
ssl._create_default_https_context = ssl._create_unverified_context

BASE = "https://pixelsport.tv"
API_EVENTS = f"{BASE}/backend/liveTV/events"

# File outputs
OUTPUT_FILE_VLC = "Pixelsports_VLC.m3u8"
OUTPUT_FILE_TIVIMATE = "Pixelsports_TiviMate.m3u8"

# Headers and constants
VLC_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
VLC_REFERER = f"{BASE}/"
VLC_ICY = "1"

# Encoded user agent for TiviMate
TIVIMATE_USER_AGENT = quote(VLC_USER_AGENT, safe="")

LEAGUE_INFO = {
    "NFL": ("NFL.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Maxx.png", "NFL"),
    "MLB": ("MLB.Baseball.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Baseball3.png", "MLB"),
    "NHL": ("NHL.Hockey.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Hockey2.png", "NHL"),
    "NBA": ("NBA.Basketball.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Basketball-2.png", "NBA"),
    "NASCAR": ("Racing.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Motorsports2.png", "NASCAR Cup Series"),
    "UFC": ("UFC.Fight.Pass.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/CombatSports2.png", "UFC"),
    "SOCCER": ("Soccer.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Soccer.png", "Soccer"),
    "BOXING": ("PPV.EVENTS.Dummy.us", "http://drewlive24.duckdns.org:9000/Logos/Combat-Sports.png", "Boxing"),
}

def utc_to_eastern(utc_str):
    """Convert UTC time string to Eastern Time (approximation)."""
    try:
        utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        month = utc_dt.month
        offset = -4 if 3 <= month <= 11 else -5
        et = utc_dt + timedelta(hours=offset)
        return et.strftime("%I:%M %p ET - %m/%d/%Y").replace(" 0", " ")
    except Exception:
        return ""

def get_game_status(utc_str):
    """Return game status text."""
    try:
        utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        time_diff = (utc_dt - now).total_seconds()

        if time_diff < -10800:
            return "Finished"
        elif time_diff < 0:
            return "Started"
        else:
            hours = int(time_diff // 3600)
            minutes = int((time_diff % 3600) // 60)
            return f"In {hours}h {minutes}m" if hours > 0 else f"In {minutes}m"
    except Exception:
        return ""

def fetch_json(url):
    """Fetch JSON data from the API safely."""
    headers = {
        "User-Agent": VLC_USER_AGENT,
        "Referer": VLC_REFERER,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Icy-MetaData": VLC_ICY,
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[!] Failed to fetch JSON from {url}: {e}")
        return {}

def collect_links_with_labels(event):
    """Extract valid stream links with Home/Away/Alt labels."""
    links = []
    comp1_home = event.get("competitors1_homeAway", "").lower() == "home"
    
    channel_info = event.get("channel", {})
    for i in range(1, 4):
        key = f"server{i}URL"
        link = channel_info.get(key)
        if link and link.lower() != "null":
            if i == 1:
                label = "Home" if comp1_home else "Away"
            elif i == 2:
                label = "Away" if comp1_home else "Home"
            else:
                label = "Alt"
            links.append((link, label))
    return links

def get_league_info(league_name):
    """Return league metadata (tvg-id, logo, name)."""
    for key, (tvid, logo, display_name) in LEAGUE_INFO.items():
        if key.lower() in league_name.lower():
            return tvid, logo, display_name
    return ("Pixelsports.Dummy.us", "", "Live Sports")

def build_m3u(events, tivimate=False):
    """Generate playlist in VLC or TiviMate format."""
    lines = ["#EXTM3U"]
    for ev in events:
        title = ev.get("match_name", "Unknown Event").strip()
        logo = ev.get("competitors1_logo", "")
        date_str = ev.get("date", "")
        time_et = utc_to_eastern(date_str)
        status = get_game_status(date_str)

        if time_et:
            title += f" - {time_et}"
        if status:
            title += f" - {status}"

        league = ev.get("channel", {}).get("TVCategory", {}).get("name", "LIVE")
        tvid, group_logo, group_display = get_league_info(league)
        if not logo:
            logo = group_logo

        for link, label in collect_links_with_labels(ev):
            lines.append(
                f'#EXTINF:-1 tvg-id="{tvid}" tvg-logo="{logo}" group-title="Pixelsports - {group_display} - {label}",{title}'
            )
            if tivimate:
                # TiviMate format (pipe style) with icy-metadata=1
                full_link = (
                    f"{link}|referer={VLC_REFERER}|origin={VLC_REFERER}|user-agent={TIVIMATE_USER_AGENT}|icy-metadata=1"
                )
                lines.append(full_link)
            else:
                # VLC/Generic format
                lines.append(f"#EXTVLCOPT:http-user-agent={VLC_USER_AGENT}")
                lines.append(f"#EXTVLCOPT:http-referrer={VLC_REFERER}")
                lines.append(f"#EXTVLCOPT:http-icy-metadata={VLC_ICY}")
                lines.append(link)
    return "\n".join(lines)

def main():
    print("[*] Fetching PixelSport live events…")
    data = fetch_json(API_EVENTS)
    events = data.get("events", [])
    if not events:
        print("[-] No live events found.")
        return

    # Generate both formats
    print("[*] Building VLC playlist...")
    playlist_vlc = build_m3u(events, tivimate=False)
    with open(OUTPUT_FILE_VLC, "w", encoding="utf-8") as f:
        f.write(playlist_vlc)
    print(f"[+] Saved VLC playlist: {OUTPUT_FILE_VLC} ({len(events)} events)")

    print("[*] Building TiviMate playlist...")
    playlist_tivimate = build_m3u(events, tivimate=True)
    with open(OUTPUT_FILE_TIVIMATE, "w", encoding="utf-8") as f:
        f.write(playlist_tivimate)
    print(f"[+] Saved TiviMate playlist: {OUTPUT_FILE_TIVIMATE} ({len(events)} events)")

    print("[✔] All playlists generated successfully!")

if __name__ == "__main__":
    main()
