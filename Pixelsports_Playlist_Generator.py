import asyncio
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from playwright.async_api import async_playwright

BASE = "https://pixelsport.tv"
API_EVENTS = "https://pixelsport.tv/backend/liveTV/events.json"

OUT_VLC = "Pixelsports_VLC.m3u8"
OUT_TIVI = "Pixelsports_TiviMate.m3u8"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
UA_ENC = quote(UA, safe="")

# ---------------- TIME HELPERS ---------------- #

def utc_to_et(utc):
    try:
        dt = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        off = -4 if 3 <= dt.month <= 11 else -5
        return (dt + timedelta(hours=off)).strftime("%I:%M %p ET %m/%d/%Y").replace(" 0", " ")
    except:
        return ""

# ---------------- PLAYWRIGHT FETCH ---------------- #

async def fetch_events_via_browser():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        ctx = await browser.new_context(user_agent=UA)
        page = await ctx.new_page()

        print("[*] Opening PixelSport homepage (Cloudflare)…")
        await page.goto(BASE, wait_until="networkidle", timeout=60000)

        print("[*] Executing browser fetch() for API…")
        data = await page.evaluate(
            """async (url) => {
                const r = await fetch(url, { credentials: "include" });
                return await r.json();
            }""",
            API_EVENTS,
        )

        await browser.close()
        return data

# ---------------- PLAYLIST BUILD ---------------- #

def build_playlist(events, tivimate=False):
    out = ["#EXTM3U"]

    for ev in events:
        title = ev.get("match_name", "Live Event")
        time_et = utc_to_et(ev.get("date", ""))
        if time_et:
            title += f" - {time_et}"

        ch = ev.get("channel", {})
        for i, lbl in [(1, "Home"), (2, "Away"), (3, "Alt")]:
            url = ch.get(f"server{i}URL")
            if not url or url == "null":
                continue

            out.append(f'#EXTINF:-1 group-title="PixelSport",{title} ({lbl})')

            if tivimate:
                out.append(
                    f"{url}|user-agent={UA_ENC}|referer={BASE}/|origin={BASE}|icy-metadata=1"
                )
            else:
                out.append(f"#EXTVLCOPT:http-user-agent={UA}")
                out.append(f"#EXTVLCOPT:http-referrer={BASE}/")
                out.append(url)

    return "\n".join(out)

# ---------------- MAIN ---------------- #

async def main():
    print("[*] Fetching events via real browser…")
    data = await fetch_events_via_browser()

    events = data.get("events", [])
    if not events:
        print("[-] No events found")
        return

    with open(OUT_VLC, "w", encoding="utf-8") as f:
        f.write(build_playlist(events, False))

    with open(OUT_TIVI, "w", encoding="utf-8") as f:
        f.write(build_playlist(events, True))

    print(f"[✔] Generated {len(events)} events")

if __name__ == "__main__":
    asyncio.run(main())
