import requests

URL = "https://sportsonline.sn/prog.txt"

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

        # Skip blank lines
        if not line:
            continue

        # EXTINF line
        if line.startswith("#EXTINF"):
            try:
                # Extract channel name after the comma
                name = line.split(",", 1)[1].strip()
            except:
                name = "Unknown"
            continue

        # Found URL
        if line.startswith("http"):
            playlist.append((name if name else "Unknown", line))
            name = None  # Reset for next entry

    print(f"📺 Parsed {len(playlist)} channels.")
    return playlist

def save_m3u(items):
    print("💾 Saving sportsonline.m3u ...")
    with open("sportsonline.m3u", "w", encoding="utf-8") as f:
        for name, url in items:
            f.write(f"#EXTINF:-1,{name}\n")
            f.write(url + "\n\n
    print("✅ Done.")

def main():
    raw = fetch_playlist()
    parsed = parse_playlist(raw)
    save_m3u(parsed)

if __name__ == "__main__":
    main()
