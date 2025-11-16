import requests

# Input playlist URL
URL = "https://sportsonline.sn/prog.txt"

# Optional: map channel names to logos
CHANNEL_LOGOS = {
    "ESPN": "https://example.com/logos/espn.png",
    "Sky Sports": "https://example.com/logos/sky.png",
    # Add more logos as needed
}

# Optional: map channel names to categories (group-title)
CHANNEL_CATEGORIES = {
    "ESPN": "Sports",
    "Sky Sports": "Sports",
    # Add more categories as needed
}

# Optional: custom VLC/TiviMate headers
CUSTOM_HEADERS = [
    '#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    '#EXTVLCOPT:http-referrer=https://sportsonline.sn/'
]


def fetch_playlist():
    print(f"🌐 Fetching playlist from: {URL}")
    r = requests.get(URL, timeout=10)
    r.raise_for_status()
    return r.text


def parse_playlist(raw):
    print("🔍 Parsing playlist...")
    lines = raw.splitlines()
    playlist = []
    name = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            try:
                name = line.split(",", 1)[1].strip()
            except:
                name = "Unknown"
            continue
        if line.startswith("http"):
            playlist.append((name if name else "Unknown", line))
            name = None
    print(f"📺 Parsed {len(playlist)} channels.")
    return playlist


def save_m3u(items, filename="sportsonline.m3u"):
    print(f"💾 Saving {filename} ...")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for name, url in items:
            logo = CHANNEL_LOGOS.get(name, "")
            category = CHANNEL_CATEGORIES.get(name, "Sports")
            f.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{category}",{name}\n')
            for header in CUSTOM_HEADERS:
                f.write(f"{header}\n")
            f.write(url + "\n\n")
    print("✅ Done.")


def main():
    raw = fetch_playlist()
    parsed = parse_playlist(raw)
    save_m3u(parsed)


if __name__ == "__main__":
    main()
