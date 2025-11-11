import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote

BASE_URL = "https://roxiestreams.live/"
CATEGORIES = ["", "soccer", "nba", "mlb", "nfl", "fighting", "motorsports", "motogp", "ufc", "ppv", "wwe", "f1", "nascar"]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
REFERER = BASE_URL

VLC_OUTPUT = "Roxiestreams_VLC.m3u8"
TIVIMATE_OUTPUT = "Roxiestreams_TiviMate.m3u8"

# Logo / Metadata Dictionary
TV_INFO = {
    "ppv": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/mkj4tC62/PPV.png", "PPV"),
    "soccer": ("Soccer.Dummy.us", "https://i.postimg.cc/HsWHFvV0/Soccer.png", "Soccer"),
    "ufc": ("UFC.Fight.Pass.Dummy.us", "https://i.postimg.cc/59Sb7W9D/Combat-Sports2.png", "UFC"),
    "fighting": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/8c4GjMnH/Combat-Sports.png", "Combat Sports"),
    "nfl": ("Football.Dummy.us", "https://i.postimg.cc/tRNpSGCq/Maxx.png", "NFL"),
    "nba": ("NBA.Basketball.Dummy.us", "https://i.postimg.cc/jdqKB3LW/Basketball-2.png", "NBA"),
    "mlb": ("MLB.Baseball.Dummy.us", "https://i.postimg.cc/FsFmwC7K/Baseball3.png", "MLB"),
    "wwe": ("PPV.EVENTS.Dummy.us", "https://i.postimg.cc/wTxHn47J/WWE2.png", "WWE"),
    "f1": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Formula 1"),
    "motorsports": ("Racing.Dummy.us", "https://i.postimg.cc/yY6B2pkv/F1.png", "Motorsports"),
    "nascar": ("Racing.Dummy.us", "https://i.postimg.cc/m2dR43HV/Motorsports2.png", "NASCAR Cup Series"),
    "misc": ("Sports.Dummy.us", "https://i.postimg.cc/qMm0rc3L/247.png", "Random Events"),
}


def get_category_links(category_path):
    url = f"{BASE_URL}{category_path}" if category_path else BASE_URL
    headers = {"User-Agent": USER_AGENT, "Referer": REFERER}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a['href']
            text = a.get_text(strip=True)
            if "stream" in href:
                links.extend(get_event_m3u8(href, text))
        return links
    except Exception as e:
        print(f"❌ Failed to fetch category {category_path}: {e}")
        return []


def get_event_m3u8(event_path, event_title="Unknown Event"):
    url = event_path if event_path.startswith("http") else f"{BASE_URL}{event_path}"
    headers = {"User-Agent": USER_AGENT, "Referer": REFERER}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        m3u8_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.endswith(".m3u8"):
                clean_url = href.replace("showPlayer('clappr', '", "").replace("')", "").strip()
                m3u8_links.append((event_title, clean_url))

        # Also catch inline JS or embedded sources
        if not m3u8_links and ".m3u8" in html:
            parts = [p.split("'")[0] for p in html.split(".m3u8") if "http" in p]
            for p in parts:
                url_part = p[p.find("http"):] + ".m3u8"
                clean_url = url_part.strip().replace("')", "")
                m3u8_links.append((event_title, clean_url))

        return m3u8_links
    except Exception as e:
        print(f"❌ Failed to fetch event {event_path}: {e}")
        return []


def write_playlists(streams):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    m3u_header = f'#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n# Last Updated: {timestamp}\n\n'

    with open(VLC_OUTPUT, "w", encoding="utf-8") as f_vlc, open(TIVIMATE_OUTPUT, "w", encoding="utf-8") as f_tvm:
        f_vlc.write(m3u_header)
        f_tvm.write(m3u_header)

        for cat, event_title, url in streams:
            tv_id, logo, group_name = TV_INFO.get(cat.lower(), TV_INFO["misc"])
            line = f'#EXTINF:-1 tvg-logo="{logo}" tvg-id="{tv_id}" group-title="RoxieStreams - {group_name}",{event_title}\n'

            f_vlc.write(line + f"{url}\n")
            ua_encoded = quote(USER_AGENT, safe="")
            f_tvm.write(line + f"{url}|referer={REFERER}|user-agent={ua_encoded}\n")


def main():
    print("▶️ Starting RoxieStreams playlist generation...")
    all_streams = []

    for cat_path in CATEGORIES:
        cat_name = cat_path if cat_path else "Roxiestreams"
        print(f"Processing category: {cat_name}")
        links = get_category_links(cat_path)
        for event_title, url in links:
            all_streams.append((cat_name, event_title, url))

    if all_streams:
        print(f"✅ Found {len(all_streams)} streams.")
    else:
        print("⚠️ No streams found.")

    write_playlists(all_streams)
    print(f"✅ Playlists written:\n  VLC: {VLC_OUTPUT}\n  TiviMate: {TIVIMATE_OUTPUT}")


if __name__ == "__main__":
    main()
