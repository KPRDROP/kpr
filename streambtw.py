import requests
from bs4 import BeautifulSoup
import re
import urllib.parse

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

    # Find all card elements
    cards = soup.find_all('div', class_='card')

    for card in cards:
        try:
            # Extract category/league name
            category = card.find('h5', class_='card-title')
            category = category.text.strip() if category else "Unknown"

            # Extract event name
            event_name = card.find('p', class_='card-text')
            event_name = event_name.text.strip() if event_name else "Unknown Event"

            # Extract iframe URL
            link = card.find('a', class_='btn btn-primary')
            iframe_url = link['href'] if link and 'href' in link.attrs else None
            if iframe_url and not iframe_url.startswith('http'):
                iframe_url = f"https://streambtw.com{iframe_url}"

            # Extract logo (optional)
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

            m3u8_match = re.search(r'["\']([^"\'\s]+\.m3u8[^"\'\s]*)["\']', html_content)
            if m3u8_match:
                return m3u8_match.group(1)

    except Exception as e:
        print(f"Error fetching iframe {iframe_url}: {e}")

    return None

def generate_m3u_playlists(events):
    """Generate both VLC and TiviMate M3U playlist content."""
    vlc_content = "#EXTM3U\n"
    tivimate_content = "#EXTM3U\n"

    for event in events:
        category = event['category']
        name = event['name']
        m3u8_url = extract_m3u8_from_iframe(event['iframe_url'])
        if not m3u8_url:
            print(f"No m3u8 found for {name}")
            continue

        # VLC playlist
        vlc_content += f'#EXTINF:-1 tvg-logo="{event["logo"]}" group-title="{category.upper()}",{name}\n'
        vlc_content += '#EXTVLCOPT:http-origin=https://streambtw.com\n'
        vlc_content += '#EXTVLCOPT:http-referrer=https://streambtw.com/\n'
        vlc_content += f'#EXTVLCOPT:http-user-agent={HEADERS["User-Agent"]}\n'
        vlc_content += f'{m3u8_url}\n'

        # TiviMate playlist (pipe-separated headers)
        user_agent_encoded = urllib.parse.quote(HEADERS['User-Agent'], safe='')
        tivimate_headers = f'Referer=https://streambtw.com/|Origin=https://streambtw.com|User-Agent={user_agent_encoded}'
        tivimate_content += f'#EXTINF:-1 tvg-logo="{event["logo"]}" group-title="{category.upper()}",{name}\n'
        tivimate_content += f'{m3u8_url}|{tivimate_headers}\n'

        print(f"Processed event: {name}")

    return vlc_content, tivimate_content

# Main execution
if __name__ == "__main__":
    try:
        print("Fetching homepage...")
        html = fetch_homepage()

        print("Parsing events...")
        events = parse_events(html)
        print(f"Found {len(events)} events")

        print("\nGenerating M3U playlists...")
        vlc_playlist, tivimate_playlist = generate_m3u_playlists(events)

        with open("Streambtw_VLC.m3u8", "w", encoding="utf-8") as f:
            f.write(vlc_playlist)
        print("VLC playlist generated: Streambtw_VLC.m3u8")

        with open("Streambtw_TiviMate.m3u8", "w", encoding="utf-8") as f:
            f.write(tivimate_playlist)
        print("TiviMate playlist generated: Streambtw_TiviMate.m3u8")

    except Exception as e:
        print(f"Error: {e}")
