import re
import base64
import requests
from bs4 import BeautifulSoup

STREAMBTW = "https://streambtw.com"

def decode_stream(encoded_js):
    """
    Extract and decode StreamBTW's obfuscated Base64 reverse-encoded m3u8 URL.
    """

    # find:  var encoded = "xxxxxxxx"
    m = re.search(r'var\s+encoded\s*=\s*"([^"]+)"', encoded_js)
    if not m:
        return None

    encoded = m.group(1)

    try:
        step1 = encoded[::-1]                     # reverse string
        step2 = step1[::-1]                      # reverse again (yes they do it twice)
        decoded = base64.b64decode(step2).decode("utf-8")
        return decoded
    except:
        return None


def scrape_iframe(url):
    """
    Fetch iframe HTML and extract the stream URL.
    """

    print(f"🔎 Checking: {url}")

    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        print("  ❌ Request error:", e)
        return None

    html = r.text

    # FIRST: Try decoding the embedded JS
    decoded = decode_stream(html)
    if decoded and "m3u8" in decoded:
        print("  ✅ m3u8 found (JS decode):", decoded)
        return decoded

    # SECOND: Fallback — search directly for Base64 strings that decode into m3u8
    all_strings = re.findall(r'"([A-Za-z0-9+/=]{50,})"', html)

    for s in all_strings:
        try:
            candidate = base64.b64decode(s).decode("utf-8")
            if "m3u8" in candidate:
                print("  ✅ m3u8 found (Base64 brute):", candidate)
                return candidate
        except:
            pass

    print("  ❌ No m3u8 found")
    return None


def get_iframe_list():
    """
    Fetch main page and extract iframe URLs.
    """

    print("🔍 Fetching StreamBTW homepage...")
    r = requests.get(STREAMBTW, timeout=10, headers={"User-Agent": "Mozilla/5.0"})

    soup = BeautifulSoup(r.text, "html.parser")

    iframes = soup.find_all("iframe")

    links = []
    for i in iframes:
        src = i.get("src")
        if src:
            if not src.startswith("http"):
                src = STREAMBTW + "/" + src.lstrip("/")
            links.append(src)

    print(f"📌 Found {len(links)} iframe pages")
    return links


def main():
    iframes = get_iframe_list()

    found = []

    for idx, iframe in enumerate(iframes, start=1):
        print(f"\n[{idx}/{len(iframes)}] iframe: {iframe}")
        m3u8 = scrape_iframe(iframe)
        if m3u8:
            found.append((iframe, m3u8))

    if not found:
        print("\n❌ No streams captured.")
        return

    print("\n🎉 DONE — Captured streams:\n")
    for src, stream in found:
        print(src, "→", stream)


if __name__ == "__main__":
    main()
