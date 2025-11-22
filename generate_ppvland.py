#!/usr/bin/env python3
import os
import sys
import aiohttp
import asyncio

OUTPUT_VLC = "PPVland_VLC.m3u8"
OUTPUT_TIVIMATE = "PPVland_TiviMate.m3u8"


async def fetch_streams(api_url: str):
    """Fetch JSON safely from API_URL using aiohttp."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=20) as resp:
                resp.raise_for_status()
                return await resp.json()
    except Exception as e:
        print(f"❌ ERROR: Failed to fetch API data ({e}).")
        return None


def write_vlc_playlist(data):
    """Write M3U playlist for VLC."""
    with open(OUTPUT_VLC, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in data:
            name = item.get("name", "Unknown")
            url = item.get("url", "")
            f.write(f"#EXTINF:-1,{name}\n{url}\n")


def write_tivimate_playlist(data):
    """Write M3U playlist for TiviMate."""
    with open(OUTPUT_TIVIMATE, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for item in data:
            name = item.get("name", "Unknown")
            url = item.get("url", "")
            logo = item.get("logo", "")
            group = item.get("group", "PPV")

            f.write(
                f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group}",{name}\n{url}\n'
            )


async def main():
    print("🚀 Starting PPVLand playlist builder")

    # ---------------------------
    #  SAFE API_URL VALIDATION
    # ---------------------------
    api_url = os.getenv("API_URL")

    if not api_url or api_url.strip() == "":
        print("❌ ERROR: Missing API_URL environment variable.")
        print("   → Add it in GitHub: Settings → Secrets → Actions → API_URL")
        sys.exit(1)

    print("🔐 API_URL loaded (value not shown for security)")

    # Fetch
    data = await fetch_streams(api_url)
    if not data:
        print("❌ No data received. Exiting.")
        sys.exit(1)

    if not isinstance(data, list):
        print("❌ ERROR: API returned invalid format (expected JSON list).")
        sys.exit(1)

    print(f"📦 Retrieved {len(data)} stream items")

    # Write output playlists
    write_vlc_playlist(data)
    write_tivimate_playlist(data)

    print(f"✅ Saved: {OUTPUT_VLC}")
    print(f"✅ Saved: {OUTPUT_TIVIMATE}")
    print("🎉 Done!")


if __name__ == "__main__":
    asyncio.run(main())
