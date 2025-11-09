import requests
from bs4 import BeautifulSoup
import re
import urllib.parse  # For URL encoding the User-Agent

# Base URL and headers
BASE_URL = "https://streambtw.com/"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Referer': 'https://streambtw.com'
}

def fetch_homepage():
    """Fetch the homepage HTML content."""
    response = requests.get(BASE_URL, headers=HEADERS)
    response.raise_for_status()
    return response.text

def parse_events(html_content):
    """Parse events from the homepage HTML."""
    soup = BeautifulSoup(html_content, 'html.parser')
    events = []

    cards = soup.find_all('div', class_='card')

    for card in cards:
        try:
            category = card.find('h5', class_='card-title')
            category = category.text.strip() if category else "Unknown"

            event_name = card.find('p', class_='card-text')
            event_name = event_name.text.strip() if event_name else "Unknown Event"

            link = card.find('a', class_='btn btn-primary')
            iframe_url = None
            if link and 'href' in link.attrs:
                iframe_url = link['href']
                if not iframe_url.startswith('http'):
                    iframe_url = f"https://streambtw.com{iframe_url}"

            logo = card.find('img', class_='league-logo')
            logo_url = logo['src'] if logo and 'src' in logo.attrs else ""

            if iframe_url:
                events.append({
                    'category': category,
                    'name': event_name,
                    'iframe_url': iframe_url,
                    'logo': logo_url
                })
        except Exception as e:
            print(f"Error parsing card: {e}")
            continue

    return events

def extract_m3u8_from_iframe(iframe_url):
    """Extract the m3u8 URL from the iframe page."""
    try:
        response = requests.get(iframe_url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            html_content = response.text
            m3u8_match = re.search(r'https?://[^\s"\']+\.m3u8[^\s"\'>]*', html_content)
            if m3u8_match:
                return m3u8_match.group(0)

            # Alternative pattern
            m3u8_match = re.search(r'["\']([^"\'\s]+\.m3u8[^"\'\s]*)["\']', html_content)
            if m3u8_match:
                return m3u8_match.group(1)
    except Exception as e:
        print(f"Error fetching iframe {iframe_url}: {e}")

    return None

def generate_m3u_playlists(events):
    """Generate both VLC and TiviMate M3U playlists."""
    vlc_content = "#EXTM3U\n"
    tivimate_content = "#EXTM3U\n"

    # Encode User-Agent for TiviMate
    tivimate_user_agent = urllib.parse.quote(HEADERS['User-Agent'])

    categories = {}
    for event in events:
        category = event['category']
        if category not in categories:
            categories[category] = []
        categories[category].append(event)

    for category, category_events in categories.items():
        print(f"\nProcessing category: {category}")
        for event in category_events:
            print(f"  - {event['name']}")
            m3u8_url = extract_m3u8_from_iframe(event['iframe_url'])

            if m3u8_url:
                print(f"    Found m3u8: {m3u8_url[:80]}...")

                # VLC Playlist
                vlc_content += f'#EXTINF:-1 tvg-logo="{event["logo"]}" group-title="{category.upper()}",{event["name"]}\n'
                vlc_content += '#EXTVLCOPT:http-origin=https://streambtw.com\n'
                vlc_content += '#EXTVLCOPT:http-referrer=https://streambtw.com/\n'
                vlc_content += f'#EXTVLCOPT:http-user-agent={HEADERS["User-Agent"]}\n'
                vlc_content += f'{m3u8_url}\n'

                # TiviMate Playlist
                tivimate_content += f'#EXTINF:-1 tvg-logo="{event["logo"]}" group-title="{category.upper()}",{event["name"]}\n'
                tivimate_content += f'#EXTGRP:Referer=https://streambtw.com/|Origin=https://streambtw.com|User-Agent={tivimate_user_agent}\n'
                tivimate_content += f'{m3u8_url}\n'
            else:
                print(f"    No m3u8 found")

    return vlc_content, tivimate_content

# Main execution
if __name__ == "__main__":
    try:
        print("Fetching homepage...")
        html = fetch_homepage()

        print("Parsing events...")
        events = parse_events(html)
        print(f"Found {len(events)} events")

        print("\nExtracting m3u8 URLs and generating playlists...")
        vlc_playlist, tivimate_playlist = generate_m3u_playlists(events)

        with open("streambtw_VLC.m3u8", "w", encoding="utf-8") as f:
            f.write(vlc_playlist)
        print("VLC playlist generated: streambtw_VLC.m3u8")

        with open("streambtw_TiviMate.m3u8", "w", encoding="utf-8") as f:
            f.write(tivimate_playlist)
        print("TiviMate playlist generated: streambtw_TiviMate.m3u8")

    except Exception as e:
        print(f"Error: {e}")
