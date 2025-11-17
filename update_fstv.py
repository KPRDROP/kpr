import requests
import os
import re
from datetime import datetime
from urllib.parse import quote
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

def update_playlist():
    m3u_url = os.getenv('FSTV_SOURCE_URL')
    if not m3u_url:
        raise ValueError("FSTV_SOURCE_URL environment variable not set")
    
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    http = requests.Session()
    http.mount("https://", adapter)
    http.mount("http://", adapter)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/129.0 Safari/537.36'
    }

    output_filename = "FSTV_VLC.m3u8"
    tivimate_filename = "FSTV_Tivimate.m3u8"

    try:
        print(f"Fetching M3U playlist from {m3u_url}")
        response = http.get(m3u_url, timeout=30, headers=headers)
        if response.status_code == 403:
            print("❌ Access forbidden (403). The source may block GitHub Actions or need auth headers.")
            print(f"Response headers: {response.headers}")
            print(f"Response text sample: {response.text[:200]}")
            raise requests.HTTPError("403 Forbidden")

        response.raise_for_status()

        content = response.text
        if content.startswith("#EXTM3U"):
            content = content[content.find('\n') + 1:]
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        m3u_content = (
            '#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"\n'
            f"# Last Updated: {timestamp}\n{content}"
        )

        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write(m3u_content)
        print(f"✅ {output_filename} written ({os.path.getsize(output_filename)} bytes)")

        tivimate_lines = [
            '#EXTM3U x-tvg-url="https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"',
            f"# Last Updated: {timestamp}"
        ]

        for line in content.splitlines():
            if line.startswith("#EXTINF:"):
                tivimate_lines.append(line)
            elif line.strip() and not line.startswith("#"):
                ua_encoded = quote("Mozilla/5.0", safe="")
                tivimate_lines.append(f"{line.strip()}|User-Agent={ua_encoded}")

        with open(tivimate_filename, 'w', encoding='utf-8') as f:
            f.write("\n".join(tivimate_lines))
        print(f"✅ {tivimate_filename} written ({os.path.getsize(tivimate_filename)} bytes)")

        return True

    except Exception as e:
        print(f"❌ Error updating playlist: {e}")
        import traceback; traceback.print_exc()
        # Create placeholder files so git steps don’t fail
        for fn in [output_filename, tivimate_filename]:
            if not os.path.exists(fn):
                with open(fn, "w", encoding="utf-8") as f:
                    f.write("#EXTM3U\n# Failed to fetch playlist\n")
                print(f"⚠️ Created placeholder file {fn}")
        return False

if __name__ == "__main__":
    update_playlist()
