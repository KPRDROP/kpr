#!/usr/bin/env python3
import requests
import time
import urllib.parse

HOME_URL = "https://tazztv.io/"
API_URL = "https://tazztv.io/api/leagues/streams?league_uuid=a68c1287&timestamp={ts}"

# Output files
OUT_VLC = "Taz.m3u8"
OUT_TM = "Taz_TiviMate.m3u8"


def clean_title(title: str) -> str:
    """
    Replace '@' with 'vs' and remove any stray commas after the date.
    """
    if not title:
        return "Unknown Match"

    title = title.replace("@", "vs")
    title = title.replace("  ", " ")
    return title.strip()


def build_tivimate_url(url: str, referer: str, origin: str, ua: str) -> str:
    """
    Build TiviMate pipe-format URL with proper URL-encoded User-Agent.
    """
    encoded_ua = urllib.parse.quote(ua, safe="")
    return f"{url}|referer={referer}|origin={origin}|user-agent={encoded_ua}"


def main():
    print("🔄 Fetching TazzTV API…")

    ts = int(time.time() * 1000)
    api_url = API_URL.format(ts=ts)

    try:
        r = requests.get(api_url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"❌ API request failed: {e}")
        return

    if "streams" not in data:
        print("❌ API format invalid: no 'streams' key")
        return

    streams = data["streams"]
    if not streams:
        print("⚠️ No streams found in API")
        return

    print(f"✅ Found {len(streams)} streams")

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    with open(OUT_VLC, "w", encoding="utf-8") as v, open(OUT_TM, "w", encoding="utf-8") as t:
        v.write("#EXTM3U\n")
        t.write("#EXTM3U\n")

        for item in streams:
            title = clean_title(item.get("title", "Untitled"))
            stream_url = item.get("stream_url")

            if not stream_url:
                continue

            # ---- VLC FORMAT ----
            v.write(f"#EXTINF:-1,{title}\n{stream_url}\n")

            # ---- TIVIMATE FORMAT ----
            tm_url = build_tivimate_url(
                stream_url,
                referer=HOME_URL,
                origin=HOME_URL,
                ua=user_agent,
            )

            t.write(f"#EXTINF:-1,{title}\n{tm_url}\n")

    print(f"🎉 Finished!\n✓ {OUT_VLC}\n✓ {OUT_TM}")


if __name__ == "__main__":
    main()
