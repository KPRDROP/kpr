import asyncio
import os
import json
from urllib.parse import quote
from urllib.request import Request, urlopen

from playwright.async_api import async_playwright

API_URL = os.environ.get("SPZONE_API_URL")
HOME_URL = os.environ.get("HOME_URL")

if not API_URL:
    raise RuntimeError("Missing SPZONE_API_URL secret")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
UA_ENC = quote(USER_AGENT)


# ------------------------------------------------
# FETCH JSON
# ------------------------------------------------

def fetch_json(url):

    req = Request(url, headers={"User-Agent": USER_AGENT})

    with urlopen(req, timeout=20) as r:
        data = r.read().decode()

    return json.loads(data)


# ------------------------------------------------
# WRITE PLAYLISTS
# ------------------------------------------------

def write_playlists(entries):

    vlc = ["#EXTM3U"]
    tiv = ["#EXTM3U"]

    for e in entries:

        name = e["name"]
        league = e["league"]
        url = e["url"]

        vlc.append(
            f'#EXTINF:-1 tvg-id="{league}" group-title="{league}",{name}'
        )
        vlc.append(
            f'#EXTVLCOPT:http-user-agent={USER_AGENT}'
        )
        vlc.append(url)

        tiv.append(
            f'#EXTINF:-1 tvg-id="{league}" group-title="{league}",{name}'
        )
        tiv.append(
            f"{url}|user-agent={UA_ENC}"
        )

    with open("spzone_vlc.m3u8","w",encoding="utf8") as f:
        f.write("\n".join(vlc))

    with open("spzone_tivimate.m3u8","w",encoding="utf8") as f:
        f.write("\n".join(tiv))


# ------------------------------------------------
# GET EVENTS FROM API
# ------------------------------------------------

async def get_events():

    data = fetch_json(API_URL)

    events = []

    for match in data:

        league = match.get("league")
        links = match.get("links")
        team1 = match.get("team1")
        team2 = match.get("team2")

        if not links:
            continue

        name = f"{team1} vs {team2}"

        for link in links:

            events.append({
                "name": name,
                "league": league,
                "link": link
            })

    return events


# ------------------------------------------------
# CAPTURE M3U8 FROM PLAYER
# ------------------------------------------------

async def capture_stream(page, url):

    found = None

    def handle_response(resp):

        nonlocal found

        rurl = resp.url

        if ".m3u8" in rurl and not found:
            found = rurl

    page.on("response", handle_response)

    try:
        await page.goto(url, timeout=60000)
    except:
        return None

    # wait up to 12 seconds
    for _ in range(12):

        if found:
            return found

        await asyncio.sleep(1)

    return None


# ------------------------------------------------
# MAIN SCRAPER
# ------------------------------------------------

async def scrape():

    events = await get_events()

    print(f"Processing {len(events)} streams")

    results = []

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        context = await browser.new_context(
            user_agent=USER_AGENT
        )

        page = await context.new_page()

        for i,e in enumerate(events,1):

            link = e["link"]

            print(f"{i}) Opening {link}")

            stream = await capture_stream(page, link)

            if stream:

                print("   M3U8 FOUND")

                results.append({
                    "name": e["name"],
                    "league": e["league"],
                    "url": stream
                })

            else:

                print("   timeout")

        await browser.close()

    return results


# ------------------------------------------------
# MAIN
# ------------------------------------------------

async def main():

    print("Starting SportZone scraper")

    streams = await scrape()

    print(f"Found {len(streams)} streams")

    write_playlists(streams)

    print("Playlists written")


if __name__ == "__main__":

    asyncio.run(main())
