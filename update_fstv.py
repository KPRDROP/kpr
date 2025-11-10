import requests
import os
import re
from datetime import datetime
from urllib.parse import quote
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

def update_playlist():
    # Get M3U URL from environment variable
    m3u_url = os.getenv('FSTV_SOURCE_URL')
    if not m3u_url:
        raise ValueError("FSTV_SOURCE_URL environment variable not set")
    
    # Configure retry strategy
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

    try:
        # Fetch the M3U content with timeout
        response = http.get(
            m3u_url,
            timeout=30,  # 30 seconds timeout
            headers={'User-Agent': 'M3U-Playlist-Updater/1.0'}
        )
        response.raise_for_status()  # Raise an exception for bad status codes
        
        # Get the content and remove the original #EXTM3U header if it exists
        content = response.text
        if content.startswith("#EXTM3U"):
            content = content[content.find('\n') + 1:]
            
        # Add M3U header with EPG URL, timestamp, and original content
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        m3u_content = "#EXTM3U x-tvg-url=\"https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz\"\n" \
                     f"# Last Updated: {timestamp}\n" \
                     + content
        
        # Write standard playlist
        output_filename = "FSTV.m3u8"
        output_path = os.path.join(os.getcwd(), output_filename)
        print(f"Writing standard playlist to: {output_path}")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(m3u_content)
        print(f"✅ Successfully updated {output_filename} ({os.path.getsize(output_path)} bytes)")

        # --- TIVIMATE FORMAT ---
        tivimate_lines = ["#EXTM3U x-tvg-url=\"https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz\"",
                          f"# Last Updated: {timestamp}"]

        for line in content.splitlines():
            if line.startswith("#EXTINF:"):
                tivimate_lines.append(line)
            elif line.strip() and not line.startswith("#"):
                # Encode User-Agent
                ua_encoded = quote("M3U-Playlist-Updater/1.0", safe="")
                tivimate_lines.append(f"{line.strip()}|User-Agent={ua_encoded}")

        tivimate_filename = "FSTV_Tivimate.m3u8"
        tivimate_path = os.path.join(os.getcwd(), tivimate_filename)
        print(f"Writing TiviMate playlist to: {tivimate_path}")
        with open(tivimate_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(tivimate_lines))
        print(f"✅ Successfully updated {tivimate_filename} ({os.path.getsize(tivimate_path)} bytes)")

        return True  # Success
        
    except Exception as e:
        print(f"Error updating M3U playlist: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    update_playlist()
